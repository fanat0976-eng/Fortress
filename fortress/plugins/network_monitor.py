"""Network monitor plugin — watches for new devices and connections."""

import asyncio
import logging
from typing import TYPE_CHECKING

from fortress.core.plugin import BasePlugin

if TYPE_CHECKING:
    from fortress.core.config import PluginConfig
    from fortress.core.event_bus import EventBus

logger = logging.getLogger("fortress.plugins.network_monitor")


class NetworkMonitorPlugin(BasePlugin):
    """Monitor network for new devices and suspicious activity."""

    name = "network_monitor"
    description = "Watches ARP table, connections, and DNS changes"

    def __init__(self, config: "PluginConfig"):
        self.config = config
        self._task = None
        self._known_macs: set[str] = set()
        self._running = False

    async def start(self, bus: "EventBus") -> None:
        self._bus = bus
        self._running = True
        self._task = asyncio.create_task(self._monitor_loop())
        logger.info("Network monitor started")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._bus = None
        logger.info("Network monitor stopped")

    async def _monitor_loop(self) -> None:
        while self._running:
            try:
                # Check ARP table for new devices
                new_macs = await self._check_arp()
                for mac in new_macs:
                    await self._bus.emit(self._make_event(
                        "network.new_device",
                        {"mac": mac},
                        severity=1,
                    ))

                # Check open ports
                open_ports = await self._check_ports()
                if open_ports:
                    await self._bus.emit(self._make_event(
                        "network.ports_scan",
                        {"open_ports": open_ports},
                        severity=0,
                    ))

                await asyncio.sleep(300)  # Check every 5 minutes

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Network monitor error: {e}")
                await asyncio.sleep(300)

    async def _check_arp(self) -> list[str]:
        """Check ARP table for new MAC addresses (cross-platform)."""
        import subprocess
        import platform
        new_macs = []
        try:
            if platform.system() == "Windows":
                result = await asyncio.to_thread(
                    subprocess.run, ["arp", "-a"],
                    capture_output=True, text=True, timeout=10,
                )
                for line in result.stdout.split("\n"):
                    parts = line.split()
                    if len(parts) >= 2 and "-" in parts[1]:
                        mac = parts[1].upper()
                        if mac not in self._known_macs and self._known_macs:
                            new_macs.append(mac)
                        self._known_macs.add(mac)
            else:
                # Linux/macOS: parse /proc/net/arp or arp -a
                result = await asyncio.to_thread(
                    subprocess.run, ["arp", "-a"],
                    capture_output=True, text=True, timeout=10,
                )
                for line in result.stdout.split("\n"):
                    parts = line.split()
                    if len(parts) >= 3:
                        mac = parts[3].upper() if ":" in parts[3] else parts[1].upper()
                        if len(mac) == 17:  # MAC format XX:XX:XX:XX:XX:XX
                            if mac not in self._known_macs and self._known_macs:
                                new_macs.append(mac)
                            self._known_macs.add(mac)
        except Exception as e:
            logger.debug(f"ARP check failed: {e}")
        return new_macs

    async def _check_ports(self) -> list[int]:
        """Check for open ports on localhost."""
        import socket
        open_ports = []
        common_ports = [22, 80, 443, 3000, 5000, 8000, 8080, 8888]
        for port in common_ports:
            sock = None
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(0.5)
                result = await asyncio.to_thread(sock.connect_ex, ("127.0.0.1", port))
                if result == 0:
                    open_ports.append(port)
            except Exception:
                pass
            finally:
                if sock:
                    sock.close()
        return open_ports

    @staticmethod
    def _make_event(event_type: str, payload: dict, severity: int = 0):
        from fortress.core.event_bus import Event
        return Event(type=event_type, source="plugin.network_monitor", payload=payload, severity=severity)

    def config_schema(self) -> dict:
        return {
            "interfaces": {"type": "array", "items": {"type": "string"}},
        }
