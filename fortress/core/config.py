"""Configuration — YAML + Pydantic."""

import logging
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger("fortress.config")


class LLMConfig(BaseModel):
    fast_model: str = "gemma2:2b"
    deep_model: str = "qwen2.5:14b"
    ollama_url: str = "http://127.0.0.1:11434"
    fast_timeout: int = 5
    deep_timeout: int = 30
    max_tokens: int = 500


class EventBusConfig(BaseModel):
    dedup_window: float = 5.0
    max_rate: float = 10.0
    max_history: int = 500


class PluginConfig(BaseModel):
    enabled: bool = True
    paths: list[str] = Field(default_factory=list)
    ha_url: str = ""
    ha_token: str = ""
    mqtt_broker: str = "127.0.0.1"
    mqtt_port: int = 1883
    cpu_threshold: int = 80
    ram_threshold: int = 80
    interfaces: list[str] = Field(default_factory=lambda: ["eth0", "wifi"])
    ai_model: str = "llava"
    yolo_model: str = "yolov8n.pt"
    yolo_confidence: float = 0.5
    motion_threshold: int = 5000
    frame_interval: float = 1.0
    rtsp_urls: list[str] = Field(default_factory=list)
    imap_server: str = "imap.gmail.com"
    imap_port: int = 993
    imap_email: str = ""
    imap_password: str = ""
    imap_ssl: bool = True
    check_interval: int = 60
    important_senders: list[str] = Field(default_factory=list)
    bot_token: str = ""
    chat_id: str = ""


class NotificationConfig(BaseModel):
    enabled: bool = False
    bot_token: str = ""
    chat_id: str = ""


class SecurityConfig(BaseModel):
    destructive_approval: bool = True
    allowed_paths: list[str] = Field(default_factory=lambda: ["~/Desktop", "~/Documents"])


class DatabaseConfig(BaseModel):
    path: str = "~/.fortress/data/fortress.db"
    log_retention_days: int = 30


class WebConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8090


class RulesConfig(BaseModel):
    event_pattern: str = "*"
    condition: str = ""
    action_type: str = "log"
    action_params: dict = Field(default_factory=dict)
    enabled: bool = True
    name: str = "unnamed"
    priority: int = 0


class FortressConfig(BaseModel):
    name: str = "Fortress V2"
    dry_run: bool = False
    llm: LLMConfig = Field(default_factory=LLMConfig)
    event_bus: EventBusConfig = Field(default_factory=EventBusConfig)
    plugins: dict[str, PluginConfig] = Field(default_factory=dict)
    notifications: NotificationConfig = Field(default_factory=NotificationConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    web: WebConfig = Field(default_factory=WebConfig)
    rules: list[RulesConfig] = Field(default_factory=list)


DEFAULT_CONFIG_PATH = Path(__file__).parent.parent.parent / "config.yaml"


def load_config(path: str | Path | None = None) -> FortressConfig:
    """Load config from YAML file."""
    if path is None:
        path = DEFAULT_CONFIG_PATH
    else:
        path = Path(path)

    if not path.exists():
        logger.warning(f"Config not found at {path}, using defaults")
        return FortressConfig()

    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        logger.error(f"Invalid YAML in {path}: {e}")
        return FortressConfig()

    # Flatten nested fortress config
    data = raw.get("fortress", raw)

    # Convert plugins dict to PluginConfig objects
    plugins_raw = data.get("plugins", {})
    plugins = {}
    for name, pcfg in plugins_raw.items():
        if isinstance(pcfg, dict):
            plugins[name] = PluginConfig(**pcfg)
        else:
            plugins[name] = PluginConfig()
    data["plugins"] = plugins

    # Convert rules list
    rules_raw = data.get("rules", [])
    rules = [RulesConfig(**r) if isinstance(r, dict) else RulesConfig() for r in rules_raw]
    data["rules"] = rules

    # Convert nested models
    for key, model_class in [
        ("llm", LLMConfig), ("event_bus", EventBusConfig),
        ("notifications", NotificationConfig), ("security", SecurityConfig),
        ("database", DatabaseConfig), ("web", WebConfig),
    ]:
        if key in data and isinstance(data[key], dict):
            data[key] = model_class(**data[key])

    return FortressConfig(**data)
