"""Process monitor plugin — watches CPU, RAM, and new processes."""

import asyncio
import logging
from typing import TYPE_CHECKING

from fortress.core.plugin import BasePlugin

if TYPE_CHECKING:
    from fortress.core.config import PluginConfig
    from fortress.core.event_bus import EventBus

logger = logging.getLogger("fortress.plugins.process_monitor")


class ProcessMonitorPlugin(BasePlugin):
    """Monitor system processes for anomalies."""

    name = "process_monitor"
    description = "Watches CPU, RAM usage and new processes"

    def __init__(self, config: "PluginConfig"):
        self.config = config
        self._task = None
        self._known_pids: set[int] = set()
        self._running = False

    async def start(self, bus: "EventBus") -> None:
        try:
            import psutil
        except ImportError:
            logger.error("psutil not installed. Run: pip install psutil")
            return

        self._bus = bus
        self._running = True

        # Record initial PIDs
        self._known_pids = {p.pid for p in psutil.process_iter(["pid"])}

        self._task = asyncio.create_task(self._monitor_loop())
        logger.info("Process monitor started")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Process monitor stopped")

    async def _monitor_loop(self) -> None:
        import psutil
        from fortress.core.event_bus import Event

        while self._running:
            try:
                # CPU/RAM check — use to_thread to avoid blocking event loop
                cpu = await asyncio.to_thread(psutil.cpu_percent, interval=1)
                ram = psutil.virtual_memory().percent

                if cpu > self.config.cpu_threshold:
                    await self._bus.emit(Event(
                        type="process.high_cpu",
                        source="plugin.process_monitor",
                        payload={"cpu_percent": cpu},
                        severity=1,
                    ))

                if ram > self.config.ram_threshold:
                    await self._bus.emit(Event(
                        type="process.high_ram",
                        source="plugin.process_monitor",
                        payload={"ram_percent": ram},
                        severity=1,
                    ))

                # New process detection
                current_pids = {p.pid for p in psutil.process_iter(["pid", "name", "username"])}
                new_pids = current_pids - self._known_pids
                if new_pids:
                    for pid in new_pids:
                        try:
                            proc = psutil.Process(pid)
                            name = proc.name()
                            user = proc.username()
                            await self._bus.emit(Event(
                                type="process.new",
                                source="plugin.process_monitor",
                                payload={"pid": pid, "name": name, "user": user},
                                severity=0,
                            ))
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            pass
                    self._known_pids = current_pids

                await asyncio.sleep(30)  # Check every 30 seconds

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Process monitor error: {e}")
                await asyncio.sleep(30)

    def config_schema(self) -> dict:
        return {
            "cpu_threshold": {"type": "integer", "default": 80},
            "ram_threshold": {"type": "integer", "default": 80},
        }
