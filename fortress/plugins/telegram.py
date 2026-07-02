"""Telegram plugin — notifications + remote commands + camera control."""

import asyncio
import json
import logging
from typing import TYPE_CHECKING

from fortress.core.plugin import BasePlugin
from fortress.core.event_bus import Event

if TYPE_CHECKING:
    from fortress.core.config import PluginConfig
    from fortress.core.event_bus import EventBus

logger = logging.getLogger("fortress.plugins.telegram")


class TelegramPlugin(BasePlugin):
    """Telegram bot for notifications, remote control, and camera monitoring."""

    name = "telegram"
    description = "Send notifications, receive commands, view cameras via Telegram"

    def __init__(self, config: "PluginConfig"):
        self.config = config
        self._bus = None
        self._running = False
        self._offset = 0
        self._camera_plugin = None  # Set by main.py after all plugins load
        self._poll_task = None

    def set_camera_plugin(self, camera_plugin) -> None:
        """Set reference to camera plugin for /cameras command."""
        self._camera_plugin = camera_plugin

    async def start(self, bus: "EventBus") -> None:
        self._bus = bus
        self._running = True

        # Subscribe to events for notifications
        bus.subscribe("*", self._on_event)

        # Start polling for commands
        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info("Telegram plugin started")

    async def stop(self) -> None:
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None
        logger.info("Telegram plugin stopped")

    async def _on_event(self, event: Event) -> None:
        """Forward events as Telegram notifications."""
        if not self.config.bot_token or not self.config.chat_id:
            return

        # Only notify on warning+ severity
        if event.severity < 1:
            return

        severity_icons = {1: "⚠️", 2: "🔴", 3: "🚨"}
        icon = severity_icons.get(event.severity, "ℹ️")

        message = f"{icon} **{event.type}**\nSource: {event.source}\n"
        if event.payload:
            payload_str = json.dumps(event.payload, ensure_ascii=False)[:200]
            message += f"Data: `{payload_str}`"

        await self._send_message(message)

    async def _send_message(self, text: str) -> bool:
        """Send message via Telegram Bot API."""
        import httpx

        if not self.config.bot_token or not self.config.chat_id:
            return False

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"https://api.telegram.org/bot{self.config.bot_token}/sendMessage",
                    json={
                        "chat_id": self.config.chat_id,
                        "text": text,
                        "parse_mode": "Markdown",
                    },
                )
                if resp.status_code == 200:
                    return True
                else:
                    logger.error(f"Telegram send failed: {resp.status_code}")
                    return False
        except Exception as e:
            logger.error(f"Telegram error: {e}")
            return False

    async def _poll_loop(self) -> None:
        """Poll Telegram for incoming commands."""
        import httpx

        if not self.config.bot_token:
            return

        while self._running:
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    resp = await client.get(
                        f"https://api.telegram.org/bot{self.config.bot_token}/getUpdates",
                        params={"offset": self._offset, "timeout": 25},
                    )

                    if resp.status_code == 429:
                        try:
                            retry_after = resp.json().get("parameters", {}).get("retry_after", 5)
                        except Exception:
                            retry_after = 5
                        logger.warning(f"Telegram rate limited, waiting {retry_after}s")
                        await asyncio.sleep(retry_after)
                        continue

                    data = resp.json()

                    if not data.get("ok"):
                        await asyncio.sleep(5)
                        continue

                    for update in data.get("result", []):
                        self._offset = update["update_id"] + 1
                        await self._handle_update(update)

            except Exception as e:
                logger.error(f"Telegram poll error: {e}")
                await asyncio.sleep(5)

    async def _handle_update(self, update: dict) -> None:
        """Handle incoming Telegram message."""
        message = update.get("message")
        if not message:
            return

        chat = message.get("chat", {})
        chat_id = chat.get("id")
        if chat_id is None:
            return
        text = message.get("text", "")

        if not text:
            return

        # Handle commands
        if text.startswith("/"):
            response = await self._handle_command(text)
            if response:
                await self._send_message_to(chat_id, response)
        else:
            # Forward as Fortress event
            await self._bus.emit(Event(
                type="telegram.message",
                source="plugin.telegram",
                payload={"chat_id": chat_id, "text": text, "user": message.get("from", {}).get("first_name", "")},
            ))

    async def _handle_command(self, text: str) -> str | None:
        """Handle Telegram bot commands."""
        cmd = text.strip().split()[0].lower()

        if cmd == "/start":
            return (
                "🏰 **Fortress V2** — AI daemon online.\n\n"
                "/status — daemon status\n"
                "/events — recent events\n"
                "/cameras — camera list\n"
                "/snapshots — latest snapshots\n"
                "/rules — active rules\n"
                "/email — email monitor status\n"
                "/test — emit test event\n"
                "/help — this help"
            )

        elif cmd == "/status":
            return await self._cmd_status()

        elif cmd == "/events":
            return await self._cmd_events()

        elif cmd == "/cameras":
            return await self._cmd_cameras()

        elif cmd == "/snapshots":
            return await self._cmd_snapshots()

        elif cmd == "/rules":
            return await self._cmd_rules()

        elif cmd == "/email":
            return await self._cmd_email()

        elif cmd == "/test":
            await self._bus.emit(Event(
                type="test.ping", source="telegram",
                payload={"message": "test from Telegram"},
            ))
            return "✅ Test event emitted"

        elif cmd == "/help":
            return (
                "/status — daemon status\n"
                "/events — recent events\n"
                "/cameras — camera list\n"
                "/snapshots — latest snapshots\n"
                "/rules — active rules\n"
                "/email — email monitor status\n"
                "/test — emit test event\n"
                "/help — this help"
            )

        return None

    async def _cmd_status(self) -> str:
        """Show daemon status."""
        if not self._bus:
            return "Bus not available"
        stats = self._bus.stats()
        return (
            f"📊 **Fortress Status**\n"
            f"Subscribers: {stats['subscribers']}\n"
            f"History: {stats['history_size']} events\n"
            f"Queue: {stats['queue_size']}"
        )

    async def _cmd_events(self) -> str:
        """Show recent events."""
        if not self._bus:
            return "No events"
        history = self._bus.history(limit=10)
        if not history:
            return "No recent events"
        lines = [f"• {e.type} ({e.source})" for e in history[-10:]]
        return "📋 **Recent Events**\n" + "\n".join(lines)

    async def _cmd_cameras(self) -> str:
        """Show camera list and status."""
        if not self._camera_plugin:
            return "Camera plugin not available"

        cameras = self._camera_plugin.registry.list_all()
        if not cameras:
            return "No cameras registered"

        lines = ["📷 **Cameras**"]
        for cam in cameras:
            status_icon = {"online": "🟢", "offline": "🔴", "error": "⚠️"}.get(cam["status"], "⚪")
            lines.append(f"{status_icon} {cam['name']} ({cam['type']}) — {cam['resolution'] or 'no signal'}")

        return "\n".join(lines)

    async def _cmd_snapshots(self) -> str:
        """Show latest snapshots."""
        from pathlib import Path
        from fortress.plugins.camera import SNAPSHOT_DIR

        if not SNAPSHOT_DIR.exists():
            return "No snapshots yet"

        # Find latest snapshot across all cameras
        all_snaps = sorted(SNAPSHOT_DIR.rglob("*.jpg"), key=lambda f: f.stat().st_mtime, reverse=True)
        if not all_snaps:
            return "No snapshots yet"

        lines = ["📸 **Latest Snapshots**"]
        for snap in all_snaps[:5]:
            cam_name = snap.parent.name if snap.parent != SNAPSHOT_DIR else "default"
            lines.append(f"• {cam_name}/{snap.name}")

        return "\n".join(lines)

    async def _cmd_rules(self) -> str:
        """Show active rules."""
        from fortress.core.config import load_config
        try:
            config = load_config()
            if not config.rules:
                return "No rules configured"
            lines = ["📜 **Rules**"]
            for rule in config.rules:
                status = "ON" if rule.enabled else "OFF"
                lines.append(f"[{status}] {rule.event_pattern} → {rule.action_type}")
            return "\n".join(lines)
        except Exception:
            return "Cannot load rules"

    async def _cmd_email(self) -> str:
        """Show email monitor status."""
        # Try to find email_monitor plugin from bus subscribers
        if self._bus:
            for pattern, handler in self._bus._subscribers:
                if hasattr(handler, '__self__') and hasattr(handler.__self__, 'name'):
                    if handler.__self__.name == "email_monitor":
                        ep = handler.__self__
                        return (
                            "📧 **Email Monitor**\n"
                            f"Server: {ep.config.imap_server}\n"
                            f"Email: {ep.config.imap_email or 'not configured'}\n"
                            f"Interval: {ep.config.check_interval}s"
                        )
        return "📧 Email monitor not active"

    async def _send_message_to(self, chat_id: int, text: str) -> None:
        """Send message to specific chat."""
        import httpx

        if not self.config.bot_token:
            return

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(
                    f"https://api.telegram.org/bot{self.config.bot_token}/sendMessage",
                    json={"chat_id": chat_id, "text": text},
                )
        except Exception as e:
            logger.error(f"Telegram send error: {e}")

    async def send_photo(self, photo_path: str, caption: str = "") -> bool:
        """Send a photo (snapshot) to the configured chat."""
        import httpx

        if not self.config.bot_token or not self.config.chat_id:
            return False

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                with open(photo_path, "rb") as photo:
                    resp = await client.post(
                        f"https://api.telegram.org/bot{self.config.bot_token}/sendPhoto",
                        data={"chat_id": self.config.chat_id, "caption": caption},
                        files={"photo": photo},
                    )
                return resp.status_code == 200
        except Exception as e:
            logger.error(f"Telegram photo error: {e}")
            return False

    def config_schema(self) -> dict:
        return {
            "bot_token": {"type": "string"},
            "chat_id": {"type": "string"},
        }
