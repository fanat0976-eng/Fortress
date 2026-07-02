"""Tests for Fortress V2 core components."""

import asyncio
import time
import pytest
from fortress.core.event_bus import EventBus, Event, RateLimiter
from fortress.core.rules import RulesEngine, Rule, Action
from fortress.core.metrics import Metrics
from fortress.core.context import ContextManager
from fortress.core.actions import ActionRunner, ActionResult


# === Event Bus Tests ===

@pytest.mark.asyncio
async def test_event_bus_emit():
    bus = EventBus(dedup_window=0.1, max_rate=100)
    event = Event(type="test.event", source="test", payload={"key": "value"})
    result = await bus.emit(event)
    assert result is True
    assert len(bus.history()) == 1


@pytest.mark.asyncio
async def test_event_bus_dedup():
    bus = EventBus(dedup_window=5.0, max_rate=100)
    event = Event(type="test.event", source="test", payload={"key": "value"})
    r1 = await bus.emit(event)
    r2 = await bus.emit(event)
    assert r1 is True
    assert r2 is False  # deduped


@pytest.mark.asyncio
async def test_event_bus_subscribe():
    bus = EventBus()
    received = []
    async def handler(event):
        received.append(event)
    bus.subscribe("test.*", handler)
    event = Event(type="test.event", source="test")
    await bus.emit(event)
    assert len(received) == 1


@pytest.mark.asyncio
async def test_event_bus_wildcard():
    bus = EventBus()
    received = []
    async def handler(event):
        received.append(event)
    bus.subscribe("*", handler)
    await bus.emit(Event(type="a", source="t"))
    await bus.emit(Event(type="b", source="t"))
    assert len(received) == 2


@pytest.mark.asyncio
async def test_event_bus_next_timeout():
    bus = EventBus()
    result = await bus.next(timeout=0.1)
    assert result is None


def test_rate_limiter():
    rl = RateLimiter(max_per_second=2)
    assert rl.allow() is True
    assert rl.allow() is True
    # Third should be throttled (tokens exhausted)
    assert rl.allow() is False


def test_event_bus_stats():
    bus = EventBus()
    stats = bus.stats()
    assert "subscribers" in stats
    assert "history_size" in stats


# === Rules Engine Tests ===

def test_rules_match_simple():
    engine = RulesEngine()
    engine.add_rule(Rule(
        name="test_rule",
        event_pattern="file.created",
        action=Action(type="log", params={"message": "test"}),
    ))
    event = Event(type="file.created", source="test")
    action = engine.match(event)
    assert action is not None
    assert action.type == "log"


def test_rules_no_match():
    engine = RulesEngine()
    engine.add_rule(Rule(
        name="test_rule",
        event_pattern="file.created",
        action=Action(type="log"),
    ))
    event = Event(type="sensor.motion", source="test")
    action = engine.match(event)
    assert action is None


def test_rules_wildcard():
    engine = RulesEngine()
    engine.add_rule(Rule(
        name="wildcard",
        event_pattern="sensor.*",
        action=Action(type="notify"),
    ))
    event = Event(type="sensor.temperature", source="test")
    action = engine.match(event)
    assert action is not None
    assert action.type == "notify"


def test_rules_condition():
    engine = RulesEngine()
    engine.add_rule(Rule(
        name="pdf_rule",
        event_pattern="file.created",
        condition="payload.get('path', '').endswith('.pdf')",
        action=Action(type="move"),
    ))
    # Matches
    e1 = Event(type="file.created", source="test", payload={"path": "/tmp/report.pdf"})
    assert engine.match(e1) is not None
    # Doesn't match
    e2 = Event(type="file.created", source="test", payload={"path": "/tmp/report.txt"})
    assert engine.match(e2) is None


def test_rules_disabled():
    engine = RulesEngine()
    engine.add_rule(Rule(name="off", event_pattern="*", enabled=False, action=Action(type="log")))
    event = Event(type="any", source="test")
    assert engine.match(event) is None


def test_rules_priority():
    engine = RulesEngine()
    engine.add_rule(Rule(name="low", event_pattern="*", priority=0, action=Action(type="log")))
    engine.add_rule(Rule(name="high", event_pattern="*", priority=10, action=Action(type="notify")))
    event = Event(type="any", source="test")
    action = engine.match(event)
    assert action.type == "notify"  # higher priority wins


def test_rules_load_from_config():
    engine = RulesEngine()
    engine.load_from_config([
        {"name": "r1", "event": "file.created", "action": {"type": "log", "message": "hi"}},
    ])
    assert len(engine.rules) == 1


# === Metrics Tests ===

def test_metrics_snapshot():
    m = Metrics()
    m.record_event()
    m.record_action()
    m.record_rule_match()
    m.record_llm_call(100.0)
    snap = m.snapshot()
    assert snap["events"] == 1
    assert snap["actions"] == 1
    assert snap["rules_matched"] == 1
    assert snap["llm_calls"] == 1
    assert snap["rules_pct"] == 100.0


def test_metrics_latency():
    m = Metrics()
    m.record_llm_call(100.0)
    m.record_llm_call(200.0)
    assert m.avg_latency_ms() == 150.0


# === Action Runner Tests ===

@pytest.mark.asyncio
async def test_action_runner_dry_run():
    runner = ActionRunner(dry_run=True)
    action = Action(type="log", params={"message": "test"})
    result = await runner.execute(action)
    assert result.status == "dry_run"


@pytest.mark.asyncio
async def test_action_runner_log():
    runner = ActionRunner(dry_run=False, require_approval=False)
    action = Action(type="log", params={"message": "test log"})
    result = await runner.execute(action)
    assert result.status == "ok"


@pytest.mark.asyncio
async def test_action_runner_unknown_type():
    runner = ActionRunner(dry_run=False, require_approval=False)
    action = Action(type="unknown_thing", params={})
    result = await runner.execute(action)
    assert result.status == "denied"


# === Context Manager Tests ===

@pytest.mark.asyncio
async def test_context_empty():
    from unittest.mock import AsyncMock, MagicMock
    db = AsyncMock()
    db.get_recent_decisions.return_value = []
    ctx = ContextManager(db, window_size=5)
    text = await ctx.get_recent()
    assert text == "No recent decisions."


@pytest.mark.asyncio
async def test_context_with_decisions():
    from unittest.mock import AsyncMock
    db = AsyncMock()
    db.get_recent_decisions.return_value = [
        {"event_type": "file.created", "action_type": "log", "rule_name": "r1", "result": "ok", "timestamp": 1.0},
        {"event_type": "sensor.motion", "action_type": "notify", "rule_name": None, "result": "ok", "timestamp": 2.0},
    ]
    ctx = ContextManager(db, window_size=5)
    text = await ctx.get_recent()
    assert "file.created" in text
    assert "(rule: r1)" in text
    assert "(LLM)" in text


# === Config Tests ===

def test_config_load_default():
    from fortress.core.config import FortressConfig
    config = FortressConfig()
    assert config.name == "Fortress V2"
    assert config.llm.fast_model == "gemma2:2b"
    assert config.dry_run is False


def test_config_load_yaml():
    import tempfile
    from fortress.core.config import load_config
    yaml_content = """
fortress:
  name: "Test"
  dry_run: true
  llm:
    fast_model: "test-model"
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_content)
        f.flush()
        config = load_config(f.name)
    assert config.name == "Test"
    assert config.dry_run is True
    assert config.llm.fast_model == "test-model"


# === Safe Eval Tests ===

def test_safe_eval_simple():
    from fortress.core.rules import _safe_eval
    from fortress.core.event_bus import Event
    event = Event(type="test", source="t", payload={"path": "/tmp/file.pdf"})
    assert _safe_eval("payload.get('path', '').endswith('.pdf')", event) is True
    assert _safe_eval("payload.get('path', '').endswith('.txt')", event) is False


def test_safe_eval_blocked():
    from fortress.core.rules import _safe_eval
    from fortress.core.event_bus import Event
    event = Event(type="test", source="t", payload={})
    # Should block dangerous operations
    assert _safe_eval("__import__('os').system('rm -rf /')", event) is False
    assert _safe_eval("open('/etc/passwd')", event) is False


def test_safe_eval_compare():
    from fortress.core.rules import _safe_eval
    from fortress.core.event_bus import Event
    event = Event(type="test", source="t", payload={"severity": 2})
    assert _safe_eval("payload.get('severity', 0) >= 2", event) is True
    assert _safe_eval("payload.get('severity', 0) < 1", event) is False


# === Plugin Interface Tests ===

def test_plugin_interface():
    from fortress.core.plugin import BasePlugin
    assert hasattr(BasePlugin, "start")
    assert hasattr(BasePlugin, "stop")


def test_file_watcher_plugin_init():
    from fortress.plugins.file_watcher import FileWatcherPlugin
    from fortress.core.config import PluginConfig
    plugin = FileWatcherPlugin(PluginConfig(paths=["/tmp"]))
    assert plugin.name == "file_watcher"


def test_process_monitor_plugin_init():
    from fortress.plugins.process_monitor import ProcessMonitorPlugin
    from fortress.core.config import PluginConfig
    plugin = ProcessMonitorPlugin(PluginConfig(cpu_threshold=90))
    assert plugin.name == "process_monitor"
    assert plugin.config.cpu_threshold == 90


def test_mqtt_plugin_init():
    from fortress.plugins.mqtt import MQTTPlugin
    from fortress.core.config import PluginConfig
    plugin = MQTTPlugin(PluginConfig(mqtt_broker="10.0.0.1", mqtt_port=1884))
    assert plugin.name == "mqtt"
    assert plugin.config.mqtt_broker == "10.0.0.1"


def test_ha_plugin_init():
    from fortress.plugins.home_assistant import HomeAssistantPlugin
    from fortress.core.config import PluginConfig
    plugin = HomeAssistantPlugin(PluginConfig(ha_url="http://test:8123", ha_token="abc"))
    assert plugin.name == "home_assistant"


def test_telegram_plugin_init():
    from fortress.plugins.telegram import TelegramPlugin
    from fortress.core.config import PluginConfig
    plugin = TelegramPlugin(PluginConfig(bot_token="test", chat_id="123"))
    assert plugin.name == "telegram"


# === Integration: Event → Rules → Action ===

@pytest.mark.asyncio
async def test_event_to_action_pipeline():
    """Test full pipeline: event → rules → action."""
    bus = EventBus()
    rules = RulesEngine()
    runner = ActionRunner(dry_run=True)

    # Add rule
    rules.add_rule(Rule(
        name="test_rule",
        event_pattern="file.created",
        condition="payload.get('path', '').endswith('.pdf')",
        action=Action(type="log", params={"message": "PDF detected"}),
    ))

    # Emit event
    event = Event(type="file.created", source="test", payload={"path": "/tmp/report.pdf"})
    await bus.emit(event)

    # Match rule
    action = rules.match(event)
    assert action is not None
    assert action.type == "log"

    # Execute (dry run)
    result = await runner.execute(action, event)
    assert result.status == "dry_run"


# === Metrics Integration ===

def test_metrics_full_cycle():
    m = Metrics()
    for _ in range(10):
        m.record_event()
    for _ in range(3):
        m.record_rule_match()
    m.record_llm_call(100.0)
    m.record_llm_decision()
    m.record_action()

    snap = m.snapshot()
    assert snap["events"] == 10
    assert snap["rules_pct"] == 30.0
    assert snap["llm_calls"] == 1


# === Security Tests ===

@pytest.mark.asyncio
async def test_action_runner_path_traversal_blocked():
    """Path traversal attempts must be blocked."""
    runner = ActionRunner(dry_run=False, require_approval=False,
                          allowed_paths=["/tmp"])
    action = Action(type="move", params={"src": "/tmp/ok.txt", "dst": "/etc/passwd"})
    result = await runner.execute(action)
    assert result.status == "denied"


@pytest.mark.asyncio
async def test_action_runner_path_traversal_dotdot():
    """../ traversal must be blocked."""
    runner = ActionRunner(dry_run=False, require_approval=False,
                          allowed_paths=["/tmp"])
    action = Action(type="move", params={"src": "/tmp/a.txt", "dst": "/tmp/../etc/passwd"})
    result = await runner.execute(action)
    assert result.status == "denied"


@pytest.mark.asyncio
async def test_action_runner_no_allowed_paths_blocks_all():
    """Empty allowed_paths must deny all file operations."""
    runner = ActionRunner(dry_run=False, require_approval=False, allowed_paths=[])
    action = Action(type="move", params={"src": "/tmp/a.txt", "dst": "/tmp/b.txt"})
    result = await runner.execute(action)
    assert result.status == "denied"


@pytest.mark.asyncio
async def test_action_runner_shell_injection_blocked():
    """Shell injection attempts must be blocked."""
    runner = ActionRunner(dry_run=False, require_approval=False)
    # Semicolon injection
    action = Action(type="execute_bash", params={"command": "echo hello; rm -rf /"})
    result = await runner.execute(action)
    assert result.status == "denied"

    # Pipe injection
    action = Action(type="execute_bash", params={"command": "echo hello | cat /etc/passwd"})
    result = await runner.execute(action)
    assert result.status == "denied"


@pytest.mark.asyncio
async def test_action_runner_shell_whitelist():
    """Only whitelisted commands are allowed."""
    runner = ActionRunner(dry_run=False, require_approval=False)
    # Allowed command
    action = Action(type="execute_bash", params={"command": "echo hello"})
    result = await runner.execute(action)
    assert result.status == "ok"

    # Not in whitelist
    action = Action(type="execute_bash", params={"command": "nc -l 4444"})
    result = await runner.execute(action)
    assert result.status == "denied"


def test_auth_manager_tokens():
    """Auth token creation and validation."""
    from fortress.core.auth import AuthManager
    am = AuthManager()
    # Master token works
    assert am.validate(am.master_token) is True
    # Created token works
    token = am.create_token("test_cam")
    assert am.validate(token) is True
    # Wrong token fails
    assert am.validate("wrong_token") is False
    # Empty token fails
    assert am.validate("") is False


def test_auth_manager_revoke():
    """Token revocation works."""
    from fortress.core.auth import AuthManager
    am = AuthManager()
    token = am.create_token("test")
    assert am.validate(token) is True
    assert am.revoke_token(token) is True
    assert am.validate(token) is False


def test_auth_manager_ip_whitelist():
    """IP whitelist restricts token access."""
    from fortress.core.auth import AuthManager
    am = AuthManager()
    token = am.create_token("restricted", ip_whitelist=["192.168.1.100"])
    assert am.validate(token, "192.168.1.100") is True
    assert am.validate(token, "10.0.0.1") is False
    assert am.validate(token, "") is False
