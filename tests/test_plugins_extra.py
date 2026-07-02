"""Tests for network_monitor, mqtt, home_assistant plugins."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import asyncio


# === Network Monitor Tests ===

def test_network_monitor_init():
    from fortress.plugins.network_monitor import NetworkMonitorPlugin
    from fortress.core.config import PluginConfig
    plugin = NetworkMonitorPlugin(PluginConfig())
    assert plugin.name == "network_monitor"
    assert plugin._running is False


@pytest.mark.asyncio
async def test_network_monitor_stop_without_start():
    from fortress.plugins.network_monitor import NetworkMonitorPlugin
    from fortress.core.config import PluginConfig
    plugin = NetworkMonitorPlugin(PluginConfig())
    await plugin.stop()  # Should not raise


def test_network_monitor_config_schema():
    from fortress.plugins.network_monitor import NetworkMonitorPlugin
    from fortress.core.config import PluginConfig
    plugin = NetworkMonitorPlugin(PluginConfig())
    schema = plugin.config_schema()
    assert "interfaces" in schema


# === MQTT Plugin Tests ===

def test_mqtt_plugin_config_schema():
    from fortress.plugins.mqtt import MQTTPlugin
    from fortress.core.config import PluginConfig
    plugin = MQTTPlugin(PluginConfig(mqtt_broker="10.0.0.1", mqtt_port=1884))
    schema = plugin.config_schema()
    assert "mqtt_broker" in schema
    assert "mqtt_port" in schema


@pytest.mark.asyncio
async def test_mqtt_stop_without_start():
    from fortress.plugins.mqtt import MQTTPlugin
    from fortress.core.config import PluginConfig
    plugin = MQTTPlugin(PluginConfig())
    await plugin.stop()  # Should not raise (no client)


def test_mqtt_publish_not_connected():
    from fortress.plugins.mqtt import MQTTPlugin
    from fortress.core.config import PluginConfig
    plugin = MQTTPlugin(PluginConfig())
    plugin.publish("test/topic", {"key": "value"})  # Should not raise, just log warning


# === Home Assistant Plugin Tests ===

def test_ha_plugin_init():
    from fortress.plugins.home_assistant import HomeAssistantPlugin
    from fortress.core.config import PluginConfig
    plugin = HomeAssistantPlugin(PluginConfig(ha_url="http://test:8123", ha_token="abc"))
    assert plugin.name == "home_assistant"
    assert plugin._running is False


@pytest.mark.asyncio
async def test_ha_stop_without_start():
    from fortress.plugins.home_assistant import HomeAssistantPlugin
    from fortress.core.config import PluginConfig
    plugin = HomeAssistantPlugin(PluginConfig())
    await plugin.stop()  # Should not raise


@pytest.mark.asyncio
async def test_ha_no_config_skips_start():
    from fortress.plugins.home_assistant import HomeAssistantPlugin
    from fortress.core.config import PluginConfig
    from fortress.core.event_bus import EventBus
    plugin = HomeAssistantPlugin(PluginConfig())
    bus = EventBus()
    await plugin.start(bus)  # Should log warning and return without error
    assert plugin._running is False


def test_ha_config_schema():
    from fortress.plugins.home_assistant import HomeAssistantPlugin
    from fortress.core.config import PluginConfig
    plugin = HomeAssistantPlugin(PluginConfig())
    schema = plugin.config_schema()
    assert "ha_url" in schema
    assert "ha_token" in schema


@pytest.mark.asyncio
async def test_ha_call_service_not_connected():
    from fortress.plugins.home_assistant import HomeAssistantPlugin
    from fortress.core.config import PluginConfig
    plugin = HomeAssistantPlugin(PluginConfig(ha_url="http://test:8123", ha_token="abc"))
    result = await plugin.call_service("light", "turn_on", "light.living_room")
    assert "error" in result


@pytest.mark.asyncio
async def test_ha_get_state_not_connected():
    from fortress.plugins.home_assistant import HomeAssistantPlugin
    from fortress.core.config import PluginConfig
    plugin = HomeAssistantPlugin(PluginConfig(ha_url="http://test:8123", ha_token="abc"))
    result = await plugin.get_state("light.living_room")
    assert result is None


# === File Watcher Thread Safety ===

def test_file_watcher_init():
    from fortress.plugins.file_watcher import FileWatcherPlugin
    from fortress.core.config import PluginConfig
    plugin = FileWatcherPlugin(PluginConfig(paths=["/tmp"]))
    assert plugin.name == "file_watcher"


def test_file_watcher_debounce():
    from fortress.plugins.file_watcher import FileWatcherPlugin, DEBOUNCE_WINDOW
    from fortress.core.config import PluginConfig
    plugin = FileWatcherPlugin(PluginConfig(paths=["/tmp"]))
    # First emit should pass
    assert plugin._should_emit("/tmp/test.txt") is True
    # Second immediate emit should be debounced
    assert plugin._should_emit("/tmp/test.txt") is False
    assert DEBOUNCE_WINDOW == 0.5
