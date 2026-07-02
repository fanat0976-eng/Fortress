"""MQTT plugin — subscribe/publish for IoT devices."""

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Callable

from fortress.core.plugin import BasePlugin
from fortress.core.event_bus import Event

if TYPE_CHECKING:
    from fortress.core.config import PluginConfig
    from fortress.core.event_bus import EventBus

logger = logging.getLogger("fortress.plugins.mqtt")


class MQTTPlugin(BasePlugin):
    """MQTT subscriber/publisher for IoT device communication."""

    name = "mqtt"
    description = "Subscribe to MQTT topics and publish commands"

    def __init__(self, config: "PluginConfig"):
        self.config = config
        self._client = None
        self._bus = None
        self._subscriptions: dict[str, Callable] = {}
        self._connected = False

    async def start(self, bus: "EventBus") -> None:
        self._bus = bus
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            logger.error("paho-mqtt not installed. Run: pip install paho-mqtt")
            return

        loop = asyncio.get_running_loop()

        # paho-mqtt v2: no client_id kwarg; use CallbackAPIVersion
        try:
            self._client = mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                protocol=mqtt.MQTTv311,
            )
        except TypeError:
            # paho-mqtt v1 fallback
            self._client = mqtt.Client(protocol=mqtt.MQTTv311)

        def on_connect(client, userdata, flags, rc):
            if rc == 0:
                self._connected = True
                logger.info(f"MQTT connected to {self.config.mqtt_broker}:{self.config.mqtt_port}")
                # Subscribe to fortress topics
                client.subscribe("fortress/+/state")
                client.subscribe("sensor/#")
                client.subscribe("device/#")
            else:
                logger.error(f"MQTT connection failed: rc={rc}")

        def on_disconnect(client, userdata, rc):
            self._connected = False
            logger.warning(f"MQTT disconnected (rc={rc})")

        def on_message(client, userdata, msg):
            try:
                topic = msg.topic
                payload = json.loads(msg.payload.decode()) if msg.payload else {}
                # Thread-safe: paho runs on its own thread
                loop.call_soon_threadsafe(
                    asyncio.ensure_future, self._handle_message(topic, payload)
                )
            except Exception as e:
                logger.error(f"MQTT message error: {e}")

        self._client.on_connect = on_connect
        self._client.on_disconnect = on_disconnect
        self._client.on_message = on_message

        try:
            self._client.connect_async(
                self.config.mqtt_broker,
                self.config.mqtt_port,
                keepalive=60,
            )
            self._client.loop_start()
            logger.info(f"MQTT plugin started ({self.config.mqtt_broker}:{self.config.mqtt_port})")
        except Exception as e:
            logger.error(f"MQTT connection failed: {e}")

    async def stop(self) -> None:
        if self._client:
            self._client.publish("fortress/available", "offline", retain=True)
            self._client.loop_stop()
            self._client.disconnect()
            logger.info("MQTT plugin stopped")

    async def _handle_message(self, topic: str, payload: dict) -> None:
        """Translate MQTT message to Fortress event + call subscribed handlers."""
        # Call custom subscribed handlers first
        for sub_topic, handler in self._subscriptions.items():
            if topic.startswith(sub_topic.replace("#", "")):
                try:
                    result = handler(topic, payload)
                    if hasattr(result, '__await__'):
                        await result
                except Exception as e:
                    logger.error(f"MQTT handler error for {sub_topic}: {e}")

        # Parse topic structure: device/{device_id}/state or sensor/{sensor_id}/state
        parts = topic.split("/")
        if len(parts) >= 3:
            source_type = parts[0]  # "device", "sensor", "fortress"
            device_id = parts[1]
            msg_type = parts[2]     # "state", "availability"

            event_type = f"mqtt.{source_type}.{msg_type}"
            await self._bus.emit(Event(
                type=event_type,
                source=f"plugin.mqtt",
                payload={"topic": topic, "device_id": device_id, "data": payload},
            ))

    def publish(self, topic: str, payload: dict, retain: bool = False) -> None:
        """Publish a message to MQTT."""
        if self._client and self._connected:
            self._client.publish(topic, json.dumps(payload), retain=retain)
        else:
            logger.warning(f"MQTT not connected, cannot publish to {topic}")

    def subscribe(self, topic: str, handler: Callable) -> None:
        """Subscribe to additional MQTT topics."""
        if self._client and self._connected:
            self._client.subscribe(topic)
        self._subscriptions[topic] = handler

    def config_schema(self) -> dict:
        return {
            "mqtt_broker": {"type": "string", "default": "127.0.0.1"},
            "mqtt_port": {"type": "integer", "default": 1883},
        }
