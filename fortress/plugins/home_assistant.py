"""Home Assistant plugin — WebSocket bridge for smart home events."""

import asyncio
import json
import logging
from typing import TYPE_CHECKING

from fortress.core.plugin import BasePlugin
from fortress.core.event_bus import Event

if TYPE_CHECKING:
    from fortress.core.config import PluginConfig
    from fortress.core.event_bus import EventBus

logger = logging.getLogger("fortress.plugins.ha")


class HomeAssistantPlugin(BasePlugin):
    """Bridge between Home Assistant and Fortress event bus."""

    name = "home_assistant"
    description = "Receives HA state changes, sends service calls"

    def __init__(self, config: "PluginConfig"):
        self.config = config
        self._ws = None
        self._bus = None
        self._running = False
        self._msg_id = 0
        self._connect_task = None

    async def start(self, bus: "EventBus") -> None:
        if not self.config.ha_url or not self.config.ha_token:
            logger.warning("HA plugin disabled: no ha_url/ha_token configured")
            return

        self._bus = bus
        self._running = True
        self._connect_task = asyncio.create_task(self._connect_loop())
        logger.info(f"HA plugin starting ({self.config.ha_url})")

    async def stop(self) -> None:
        self._running = False
        if self._connect_task:
            self._connect_task.cancel()
            try:
                await self._connect_task
            except asyncio.CancelledError:
                pass
            self._connect_task = None
        if self._ws:
            await self._ws.close()
        logger.info("HA plugin stopped")

    async def _connect_loop(self) -> None:
        """Reconnect loop with exponential backoff."""
        backoff = 1
        while self._running:
            try:
                await self._connect()
                backoff = 1
            except Exception as e:
                logger.error(f"HA connect failed: {e}, retry in {backoff}s")
                await asyncio.sleep(min(backoff, 60))
                backoff *= 2

    async def _connect(self) -> None:
        """Connect to HA WebSocket and listen for events."""
        import websockets

        ws_url = self.config.ha_url.replace("http://", "ws://").replace("https://", "wss://")
        ws_url = ws_url.rstrip("/") + "/api/websocket"

        async with websockets.connect(
            ws_url,
            additional_headers={"Authorization": f"Bearer {self.config.ha_token}"},
        ) as ws:
            self._ws = ws
            logger.info("HA WebSocket connected")

            # Authenticate
            auth_msg = await ws.recv()
            auth_data = json.loads(auth_msg)
            if auth_data.get("type") == "auth_required":
                await ws.send(json.dumps({"type": "auth", "access_token": self.config.ha_token}))
                auth_result = json.loads(await ws.recv())
                if auth_result.get("type") != "auth_ok":
                    raise Exception(f"HA auth failed: {auth_result}")

            # Subscribe to state changes
            self._msg_id += 1
            await ws.send(json.dumps({
                "id": self._msg_id,
                "type": "subscribe_events",
                "event_type": "state_changed",
            }))

            # Listen for events
            async for msg in ws:
                try:
                    data = json.loads(msg)
                except (json.JSONDecodeError, TypeError):
                    logger.debug(f"HA: ignoring non-JSON message")
                    continue
                if data.get("type") == "event":
                    await self._handle_state_change(data.get("event", {}))

    async def _handle_state_change(self, event_data: dict) -> None:
        """Convert HA state_changed event to Fortress event."""
        entity_id = event_data.get("entity_id", "")
        new_state = event_data.get("new_state", {})
        old_state = event_data.get("old_state", {})

        if not entity_id:
            return

        # Determine event type from domain
        domain = entity_id.split(".")[0]
        event_type = f"ha.{domain}.changed"

        # Extract useful data
        payload = {
            "entity_id": entity_id,
            "state": new_state.get("state"),
            "old_state": old_state.get("state"),
            "attributes": new_state.get("attributes", {}),
        }

        # Determine severity
        severity = 0
        if domain == "binary_sensor" and new_state.get("state") == "on":
            severity = 1  # binary sensor triggered
        if domain == "alarm_control_panel":
            severity = 2  # alarm event

        await self._bus.emit(Event(
            type=event_type,
            source="plugin.home_assistant",
            payload=payload,
            severity=severity,
        ))

    async def call_service(self, domain: str, service: str, entity_id: str = None, **kwargs) -> dict:
        """Call a Home Assistant service."""
        if not self._ws:
            return {"error": "Not connected to HA"}

        self._msg_id += 1
        msg = {
            "id": self._msg_id,
            "type": "call_service",
            "domain": domain,
            "service": service,
        }
        if entity_id:
            msg["target"] = {"entity_id": entity_id}
        if kwargs:
            msg["service_data"] = kwargs

        await self._ws.send(json.dumps(msg))

        # Wait for result
        try:
            response = await asyncio.wait_for(self._ws.recv(), timeout=10)
            return json.loads(response)
        except asyncio.TimeoutError:
            return {"error": "HA service call timed out"}

    async def get_state(self, entity_id: str) -> dict | None:
        """Get current state of an entity."""
        if not self._ws:
            return None

        self._msg_id += 1
        await self._ws.send(json.dumps({
            "id": self._msg_id,
            "type": "get_state",
            "entity_id": entity_id,
        }))

        try:
            response = await asyncio.wait_for(self._ws.recv(), timeout=5)
            data = json.loads(response)
            return data.get("result")
        except (asyncio.TimeoutError, Exception):
            return None

    def config_schema(self) -> dict:
        return {
            "ha_url": {"type": "string"},
            "ha_token": {"type": "string"},
        }
