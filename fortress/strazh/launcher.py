"""Fortress Strazh — standalone launcher with GUI window."""

import asyncio
import tkinter as tk
from tkinter import ttk, scrolledtext
from datetime import datetime


class StrazhWindow:
    """Tkinter window for Fortress Strazh — monitoring and events."""

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Fortress Strazh — System Monitoring")
        self.root.geometry("800x600")
        self.root.configure(bg="#0a0a0f")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._running = False
        self._daemon = None

        self._setup_ui()

    def _setup_ui(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background="#0a0a0f")
        style.configure("TLabel", background="#0a0a0f", foreground="#00d4ff", font=("Segoe UI", 11))
        style.configure("Header.TLabel", background="#0a0a0f", foreground="#00d4ff", font=("Segoe UI", 14, "bold"))
        style.configure("Status.TLabel", background="#0a0a0f", foreground="#00ff88", font=("Segoe UI", 10))

        # Header
        header = ttk.Frame(self.root)
        header.pack(fill=tk.X, padx=10, pady=10)
        ttk.Label(header, text="Fortress Strazh", style="Header.TLabel").pack(side=tk.LEFT)
        self._status_label = ttk.Label(header, text="Stopped", style="Status.TLabel")
        self._status_label.pack(side=tk.RIGHT)

        # Metrics
        metrics = tk.Frame(self.root, bg="#0a0a0f")
        metrics.pack(fill=tk.X, padx=10, pady=5)

        self._metrics = {}
        for i, name in enumerate(["Events", "Actions", "Rules%", "LLM"]):
            f = tk.Frame(metrics, bg="#12121a", relief=tk.RAISED, bd=1)
            f.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
            tk.Label(f, text=name, bg="#12121a", fg="#666", font=("Segoe UI", 9)).pack()
            val = tk.Label(f, text="0", bg="#12121a", fg="#00d4ff", font=("Segoe UI", 18, "bold"))
            val.pack()
            self._metrics[name] = val

        # Events log
        log_frame = tk.Frame(self.root, bg="#0a0a0f")
        log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        tk.Label(log_frame, text="Events", bg="#0a0a0f", fg="#666", font=("Segoe UI", 10)).pack(anchor=tk.W)
        self._log = scrolledtext.ScrolledText(log_frame, height=15, bg="#12121a", fg="#e0e0e0",
                                               font=("Consolas", 9), insertbackground="#00d4ff")
        self._log.pack(fill=tk.BOTH, expand=True)

        # Controls
        ctrl = tk.Frame(self.root, bg="#0a0a0f")
        ctrl.pack(fill=tk.X, padx=10, pady=10)

        self._start_btn = tk.Button(ctrl, text="Start Strazh", bg="#00d4ff", fg="#000",
                                     font=("Segoe UI", 10, "bold"), command=self._start, relief=tk.FLAT)
        self._start_btn.pack(side=tk.LEFT, padx=5)

        self._stop_btn = tk.Button(ctrl, text="Stop", bg="#333", fg="#fff",
                                    font=("Segoe UI", 10), command=self._stop, relief=tk.FLAT, state=tk.DISABLED)
        self._stop_btn.pack(side=tk.LEFT, padx=5)

    def _log_msg(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self._log.insert(tk.END, f"[{ts}] {msg}\n")
        self._log.see(tk.END)
        if self._log.index(tk.END).split(".")[0] > 500:
            self._log.delete("1.0", "100.0")

    def _start(self):
        self._running = True
        self._start_btn.config(state=tk.DISABLED)
        self._stop_btn.config(state=tk.NORMAL)
        self._status_label.config(text="Running")
        self._log_msg("Strazh started")

        self._daemon_task = asyncio.ensure_future(self._monitor_loop())

    def _stop(self):
        self._running = False
        self._start_btn.config(state=tk.NORMAL)
        self._stop_btn.config(state=tk.DISABLED)
        self._status_label.config(text="Stopped")
        self._log_msg("Strazh stopped")
        if hasattr(self, '_daemon_task') and self._daemon_task:
            self._daemon_task.cancel()

    async def _monitor_loop(self):
        """Main monitoring loop — process events, rules, LLM."""
        try:
            from fortress.core.event_bus import EventBus
            from fortress.core.rules import RulesEngine
            from fortress.core.config import load_config
            from fortress.core.database import Database
            from fortress.core.metrics import Metrics

            config = load_config()
            db = Database(config.database.path)
            await db.connect()

            bus = EventBus(dedup_window=config.event_bus.dedup_window, max_rate=config.event_bus.max_rate)
            rules = RulesEngine()
            rules.load_from_config(config.rules)
            metrics = Metrics()

            self._log_msg(f"Loaded {len(rules.rules)} rules")
            self._log_msg(f"Database: {config.database.path}")

            # Subscribe to events
            async def on_event(event):
                self._log_msg(f"Event: {event.type} ({event.source})")
                metrics.record_event()

            bus.subscribe("*", on_event)

            # Main loop
            while self._running:
                event = await bus.next(timeout=1.0)
                if event is None:
                    continue

                metrics.record_event()
                await db.store_event(event)

                # Rules
                action = rules.match(event)
                if action:
                    metrics.record_rule_match()
                    self._log_msg(f"Rule matched: {action.type}")

                # Update metrics display
                self._update_metrics(metrics)

            await db.close()

        except asyncio.CancelledError:
            pass
        except Exception as e:
            self._log_msg(f"Error: {e}")

    def _update_metrics(self, metrics):
        snap = metrics.snapshot()
        self._metrics["Events"].config(text=str(snap["events"]))
        self._metrics["Actions"].config(text=str(snap["actions"]))
        self._metrics["Rules%"].config(text=f"{snap['rules_pct']:.0f}%")
        self._metrics["LLM"].config(text=f"{snap['avg_latency_ms']:.0f}ms")

    def _on_close(self):
        self._running = False
        if hasattr(self, '_daemon_task') and self._daemon_task:
            self._daemon_task.cancel()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    """Entry point for fortress-strazh command."""
    window = StrazhWindow()
    window.run()


if __name__ == "__main__":
    main()
