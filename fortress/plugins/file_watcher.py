"""File watcher plugin — monitors directories for file events with debounce."""

import asyncio
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

from fortress.core.plugin import BasePlugin

if TYPE_CHECKING:
    from fortress.core.config import PluginConfig
    from fortress.core.event_bus import EventBus

logger = logging.getLogger("fortress.plugins.file_watcher")

DEBOUNCE_WINDOW = 0.5  # seconds — ignore duplicate events within this window


class FileWatcherPlugin(BasePlugin):
    """Monitor directories for file system changes using watchdog."""

    name = "file_watcher"
    description = "Watches directories for file creation, modification, deletion"

    def __init__(self, config: "PluginConfig"):
        self.config = config
        self._observer = None
        self._last_events: dict[str, float] = {}  # path → timestamp

    def _should_emit(self, path: str) -> bool:
        """Debounce: skip if same path seen within DEBOUNCE_WINDOW."""
        now = time.time()
        last = self._last_events.get(path, 0)
        if now - last < DEBOUNCE_WINDOW:
            return False
        self._last_events[path] = now
        return True

    async def start(self, bus: "EventBus") -> None:
        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler
        except ImportError:
            logger.error("watchdog not installed. Run: pip install watchdog")
            return

        plugin = self
        loop = asyncio.get_running_loop()

        class Handler(FileSystemEventHandler):
            def __init__(self, bus):
                self.bus = bus

            def _emit(self, event_type: str, path: str):
                if not plugin._should_emit(path):
                    return
                event = self._make_event(event_type, path)
                loop.call_soon_threadsafe(asyncio.ensure_future, self.bus.emit(event))

            def _make_event(self, event_type: str, path: str):
                from fortress.core.event_bus import Event
                return Event(
                    type=event_type,
                    source="plugin.file_watcher",
                    payload={"path": path, "extension": Path(path).suffix.lower(), "name": Path(path).name},
                )

            def on_created(self, event):
                if not event.is_directory:
                    self._emit("file.created", event.src_path)

            def on_modified(self, event):
                if not event.is_directory:
                    self._emit("file.modified", event.src_path)

            def on_deleted(self, event):
                if not event.is_directory:
                    self._emit("file.deleted", event.src_path)

            def on_moved(self, event):
                if not event.is_directory:
                    self._emit("file.moved", event.dest_path)

        self._observer = Observer()
        handler = Handler(bus)

        for path_str in self.config.paths:
            path = Path(path_str).expanduser()
            if path.exists():
                self._observer.schedule(handler, str(path), recursive=False)
                logger.info(f"Watching: {path}")

        self._observer.start()
        logger.info(f"File watcher started ({len(self.config.paths)} dirs, debounce={DEBOUNCE_WINDOW}s)")

    async def stop(self) -> None:
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=5)
            logger.info("File watcher stopped")

    def config_schema(self) -> dict:
        return {"paths": {"type": "array", "items": {"type": "string"}}}
