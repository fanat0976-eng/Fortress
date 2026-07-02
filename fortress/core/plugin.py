"""Base plugin interface for Fortress V2."""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fortress.core.event_bus import EventBus


class BasePlugin(ABC):
    """Abstract base class for all Fortress plugins."""

    name: str = "unnamed"
    description: str = ""

    @abstractmethod
    async def start(self, bus: "EventBus") -> None:
        """Start the plugin. Called once at daemon startup."""

    @abstractmethod
    async def stop(self) -> None:
        """Stop the plugin gracefully. Called on shutdown."""

    def config_schema(self) -> dict:
        """Return JSON schema for plugin configuration. Override if needed."""
        return {}
