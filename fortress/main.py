"""Fortress V2 — Main daemon entry point."""

import asyncio
import logging
import logging.handlers
import signal
import sys
import time
from pathlib import Path

from fortress.core.config import FortressConfig, load_config
from fortress.core.database import Database
from fortress.core.event_bus import EventBus, Event
from fortress.core.rules import RulesEngine
from fortress.core.actions import ActionRunner
from fortress.core.context import ContextManager
from fortress.core.metrics import Metrics
from fortress.core.auth import AuthManager
from fortress.reasoner.reasoner import Reasoner

logger = logging.getLogger("fortress")

PLUGIN_SHUTDOWN_TIMEOUT = 5.0  # seconds


class FortressDaemon:
    """Main daemon: event → rules/LLM → action pipeline."""

    def __init__(self, config: FortressConfig):
        self.config = config
        self.db = Database(config.database.path)
        self.bus = EventBus(
            dedup_window=config.event_bus.dedup_window,
            max_rate=config.event_bus.max_rate,
        )
        self.rules = RulesEngine()
        self.context = ContextManager(self.db)
        self.reasoner = Reasoner(config, self.context)
        self.runner = ActionRunner(
            dry_run=config.dry_run,
            require_approval=config.security.destructive_approval,
            allowed_paths=config.security.allowed_paths,
        )
        self.auth = AuthManager()
        self.metrics = Metrics()
        self._shutdown = asyncio.Event()
        self._plugins = []
        self._web_app = None
        self._web_server = None
        self._hud = None
        self._emit_tasks = set()

    async def start(self) -> None:
        logger.info(f"Fortress V2 starting (dry_run={self.config.dry_run})")
        await self.db.connect()

        self.rules.load_from_config(self.config.rules)
        logger.info(f"Loaded {len(self.rules.rules)} rules")

        await self._load_plugins()

        # Start web dashboard
        await self._start_web_server()

        # Start OpenCV HUD if display available
        await self._start_hud()

        # Log auth info
        logger.info(f"Auth token file: {Path.home() / '.fortress' / 'auth_token'}")
        logger.info("Use token in URL: ?token=YOUR_TOKEN or header: Authorization: Bearer YOUR_TOKEN")

        # Graceful shutdown
        loop = asyncio.get_event_loop()
        if sys.platform == "win32":
            # Windows only supports SIGINT; SIGTERM is not a real signal
            signal.signal(signal.SIGINT, lambda s, f: loop.call_soon_threadsafe(
                asyncio.ensure_future, self.shutdown()
            ))
        else:
            for sig in (signal.SIGINT, signal.SIGTERM):
                try:
                    loop.add_signal_handler(sig, lambda: asyncio.create_task(self.shutdown()))
                except NotImplementedError:
                    signal.signal(sig, lambda s, f: loop.call_soon_threadsafe(
                        asyncio.ensure_future, self.shutdown()
                    ))

        logger.info("Daemon running. Waiting for events...")
        await self._main_loop()

    async def shutdown(self) -> None:
        logger.info("Shutting down...")
        self._shutdown.set()

        # Stop web server
        await self._stop_web_server()

        # Stop HUD
        await self._stop_hud()

        # Stop plugins with timeout
        for plugin in self._plugins:
            try:
                await asyncio.wait_for(plugin.stop(), timeout=PLUGIN_SHUTDOWN_TIMEOUT)
                logger.info(f"Plugin stopped: {plugin.name}")
            except asyncio.TimeoutError:
                logger.warning(f"Plugin {plugin.name} stop timed out, forcing")
            except Exception as e:
                logger.error(f"Error stopping plugin {plugin.name}: {e}")
        self._plugins.clear()

        # Close LLM clients
        await self.reasoner.close()

        await self.db.close()
        logger.info("Fortress V2 stopped")

    async def _main_loop(self) -> None:
        while not self._shutdown.is_set():
            event = await self.bus.next(timeout=1.0)
            if event is None:
                continue

            self.metrics.record_event()
            await self.db.store_event(event)

            # Fast path: rules
            action = self.rules.match(event)
            if action:
                self.metrics.record_rule_match()
                result = await self.runner.execute(action, event)
                await self.db.store_decision(
                    event.id, event.type, "rule", action.type, action.params,
                    result.result, llm_used=False, latency_ms=0,
                )
                self.metrics.record_action()
                continue

            # Slow path: LLM
            try:
                t0 = time.time()
                should = await self.reasoner.should_act(event)
                if not should:
                    await self.db.store_decision(
                        event.id, event.type, None, "skip", {}, "filtered", llm_used=True, latency_ms=0,
                    )
                    continue

                action = await self.reasoner.decide_action(event)
                latency = (time.time() - t0) * 1000
                self.metrics.record_llm_call(latency)

                if action is None:
                    await self.db.store_decision(
                        event.id, event.type, None, "none", {}, "no action", llm_used=True, latency_ms=latency,
                    )
                    continue

                self.metrics.record_llm_decision()
                result = await self.runner.execute(action, event)
                await self.db.store_decision(
                    event.id, event.type, "llm", action.type, action.params,
                    result.result, llm_used=True, latency_ms=latency,
                )
                self.metrics.record_action()
            except Exception as e:
                logger.error(f"Event processing error: {e}")
                self.metrics.record_error()

    async def _load_plugins(self) -> None:
        plugin_map = {
            "file_watcher": ("fortress.plugins.file_watcher", "FileWatcherPlugin"),
            "process_monitor": ("fortress.plugins.process_monitor", "ProcessMonitorPlugin"),
            "network_monitor": ("fortress.plugins.network_monitor", "NetworkMonitorPlugin"),
            "mqtt": ("fortress.plugins.mqtt", "MQTTPlugin"),
            "home_assistant": ("fortress.plugins.home_assistant", "HomeAssistantPlugin"),
            "camera": ("fortress.plugins.camera", "CameraPlugin"),
            "telegram": ("fortress.plugins.telegram", "TelegramPlugin"),
            "email_monitor": ("fortress.plugins.email_monitor", "EmailMonitorPlugin"),
        }
        for name, pcfg in self.config.plugins.items():
            if not pcfg.enabled:
                continue
            if name not in plugin_map:
                logger.warning(f"Unknown plugin: {name}")
                continue
            module_path, class_name = plugin_map[name]
            try:
                import importlib
                module = importlib.import_module(module_path)
                plugin_cls = getattr(module, class_name)
                plugin = plugin_cls(pcfg)
                await plugin.start(self.bus)
                self._plugins.append(plugin)
                logger.info(f"Plugin started: {name}")
            except ImportError as e:
                logger.error(f"Plugin {name} dependency missing: {e}")
            except Exception as e:
                logger.error(f"Failed to start plugin {name}: {e}")

    async def _start_web_server(self) -> None:
        """Start FastAPI dashboard in background."""
        try:
            import uvicorn
            from fortress.web.app import create_dashboard_app

            self._web_app = create_dashboard_app(
                event_bus=self.bus, db=self.db, metrics=self.metrics, auth=self.auth,
            )

            # Set camera plugin reference for web + telegram endpoints
            for p in self._plugins:
                if hasattr(p, 'registry'):
                    # Store on function attribute AND app.state
                    from fortress.web.app import _find_camera_plugin
                    _find_camera_plugin._instance = p
                    self._web_app.state.camera_plugin = p
                    # Also set on telegram plugin
                    for tp in self._plugins:
                        if hasattr(tp, 'set_camera_plugin'):
                            tp.set_camera_plugin(p)
                    break

            # Subscribe to events for WebSocket broadcast
            async def broadcast_handler(event):
                if hasattr(self._web_app.state, 'broadcast_event'):
                    await self._web_app.state.broadcast_event(event)
            self.bus.subscribe("*", broadcast_handler)

            config = self.config.web
            server = uvicorn.Server(uvicorn.Config(
                self._web_app, host=config.host, port=config.port,
                log_level="warning",
            ))
            self._web_server = asyncio.create_task(server.serve())
            logger.info(f"Dashboard: http://{config.host}:{config.port}")
        except ImportError:
            logger.warning("uvicorn not installed — web dashboard disabled")
        except Exception as e:
            logger.error(f"Web server error: {e}")

    async def _stop_web_server(self) -> None:
        """Stop web server."""
        if self._web_server:
            self._web_server.cancel()
            try:
                await self._web_server
            except asyncio.CancelledError:
                pass

    async def _start_hud(self) -> None:
        """Start OpenCV HUD if display is available."""
        try:
            from fortress.hud.opencv_hud import OpenCVHUD
            # Find camera plugin
            cam_plugin = None
            for p in self._plugins:
                if hasattr(p, 'registry'):
                    cam_plugin = p
                    break
            if not cam_plugin:
                logger.info("No camera plugin — HUD skipped")
                return
            self._hud = OpenCVHUD(cam_plugin)
            await self._hud.start()
        except Exception as e:
            logger.debug(f"HUD not available: {e}")

    async def _stop_hud(self) -> None:
        """Stop HUD."""
        if self._hud:
            await self._hud.stop()

    def emit(self, event_type: str, source: str, payload: dict = None, severity: int = 0) -> None:
        event = Event(type=event_type, source=source, payload=payload or {}, severity=severity)
        task = asyncio.create_task(self.bus.emit(event))
        self._emit_tasks.add(task)
        task.add_done_callback(self._emit_tasks.discard)


async def run_daemon(config_path: str | None = None) -> None:
    config = load_config(config_path)
    daemon = FortressDaemon(config)
    await daemon.start()


def main() -> None:
    import argparse

    # Logging with rotation — use user home to avoid permission issues
    log_dir = Path.home() / ".fortress" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger("fortress")
    root_logger.setLevel(logging.INFO)

    # Console handler
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s", "%H:%M:%S"))
    root_logger.addHandler(console)

    # File handler with rotation (10MB, keep 5 backups)
    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "fortress.log", maxBytes=10_000_000, backupCount=5, encoding="utf-8",
    )
    file_handler.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s"))
    root_logger.addHandler(file_handler)

    parser = argparse.ArgumentParser(description="Fortress V2 — AI daemon")
    default_config = str(Path(__file__).parent.parent / "config.yaml")
    parser.add_argument("--config", "-c", default=default_config, help="Config file path")
    parser.add_argument("--dry-run", action="store_true", help="Dry run mode")
    # Ignore unknown args (like "start" from CLI)
    args, _ = parser.parse_known_args()

    if args.dry_run:
        root_logger.setLevel(logging.DEBUG)

    asyncio.run(run_daemon(args.config))


if __name__ == "__main__":
    main()
