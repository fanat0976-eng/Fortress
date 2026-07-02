"""Fortress Strazh — standalone launcher with live monitoring."""

import asyncio
import psutil
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
        self._daemon_task = None

        self._setup_ui()

    def _setup_ui(self):
        # Header
        header = tk.Frame(self.root, bg="#12121a", height=45)
        header.pack(fill=tk.X)
        header.pack_propagate(False)

        tk.Label(header, text="Fortress Strazh", bg="#12121a", fg="#00d4ff",
                 font=("Segoe UI", 14, "bold")).pack(side=tk.LEFT, padx=15)
        self._status_label = tk.Label(header, text="Stopped", bg="#12121a", fg="#666",
                                       font=("Segoe UI", 10))
        self._status_label.pack(side=tk.RIGHT, padx=15)

        # Metrics
        metrics = tk.Frame(self.root, bg="#0a0a0f")
        metrics.pack(fill=tk.X, padx=10, pady=5)

        self._metrics = {}
        for name in ["CPU", "RAM", "Events", "Uptime"]:
            f = tk.Frame(metrics, bg="#12121a", relief=tk.RAISED, bd=1)
            f.pack(side=tk.LEFT, padx=3, fill=tk.X, expand=True)
            tk.Label(f, text=name, bg="#12121a", fg="#666", font=("Segoe UI", 9)).pack()
            val = tk.Label(f, text="--", bg="#12121a", fg="#00d4ff", font=("Segoe UI", 16, "bold"))
            val.pack()
            self._metrics[name] = val

        # Events log
        log_frame = tk.Frame(self.root, bg="#0a0a0f")
        log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        self._log = scrolledtext.ScrolledText(log_frame, height=15, bg="#12121a", fg="#e0e0e0",
                                               font=("Consolas", 9))
        self._log.pack(fill=tk.BOTH, expand=True)

        # Controls
        ctrl = tk.Frame(self.root, bg="#0a0a0f")
        ctrl.pack(fill=tk.X, padx=10, pady=10)

        self._start_btn = tk.Button(ctrl, text="Start Strazh", bg="#00ff88", fg="#000",
                                     font=("Segoe UI", 10, "bold"), command=self._start, relief=tk.FLAT)
        self._start_btn.pack(side=tk.LEFT, padx=5)

        self._stop_btn = tk.Button(ctrl, text="Stop", bg="#333", fg="#fff",
                                    font=("Segoe UI", 10), command=self._stop, relief=tk.FLAT, state=tk.DISABLED)
        self._stop_btn.pack(side=tk.LEFT, padx=5)

    def _log_msg(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self._log.insert(tk.END, f"[{ts}] {msg}\n")
        self._log.see(tk.END)
        lines = int(self._log.index(tk.END).split(".")[0])
        if lines > 500:
            self._log.delete("1.0", "100.0")

    def _start(self):
        self._running = True
        self._start_btn.config(state=tk.DISABLED)
        self._stop_btn.config(state=tk.NORMAL)
        self._status_label.config(text="Running", fg="#00ff88")
        self._log_msg("Strazh started — monitoring system")
        self._daemon_task = asyncio.ensure_future(self._monitor_loop())

    def _stop(self):
        self._running = False
        self._start_btn.config(state=tk.NORMAL)
        self._stop_btn.config(state=tk.DISABLED)
        self._status_label.config(text="Stopped", fg="#666")
        self._log_msg("Strazh stopped")
        if self._daemon_task:
            self._daemon_task.cancel()

    async def _monitor_loop(self):
        """Monitor system metrics and generate events."""
        event_count = 0
        start_time = datetime.now()

        # Thresholds
        CPU_HIGH = 80
        RAM_HIGH = 80
        check_interval = 3  # seconds

        self._log_msg(f"Thresholds: CPU>{CPU_HIGH}%, RAM>{RAM_HIGH}%")

        try:
            while self._running:
                await asyncio.sleep(check_interval)

                # Read system metrics
                cpu = await asyncio.to_thread(psutil.cpu_percent, interval=1)
                ram = psutil.virtual_memory().percent
                disk = psutil.disk_usage("/").percent if hasattr(psutil, "disk_usage") else 0

                # Uptime
                uptime = datetime.now() - start_time
                uptime_str = str(uptime).split(".")[0]

                # Update metrics display
                self._metrics["CPU"].config(text=f"{cpu:.0f}%")
                self._metrics["RAM"].config(text=f"{ram:.0f}%")
                self._metrics["Events"].config(text=str(event_count))
                self._metrics["Uptime"].config(text=uptime_str)

                # Color metrics based on thresholds
                self._metrics["CPU"].config(fg="#ff4444" if cpu > CPU_HIGH else "#00ff88")
                self._metrics["RAM"].config(fg="#ff4444" if ram > RAM_HIGH else "#00ff88")

                # Generate events for anomalies
                if cpu > CPU_HIGH:
                    event_count += 1
                    self._log_msg(f"HIGH CPU: {cpu:.0f}%")

                if ram > RAM_HIGH:
                    event_count += 1
                    self._log_msg(f"HIGH RAM: {ram:.0f}%")

                # Log periodic status
                if event_count == 0 or event_count % 10 == 0:
                    self._log_msg(f"Status: CPU {cpu:.0f}% | RAM {ram:.0f}% | Events {event_count}")

                # Check for new processes
                try:
                    procs = await asyncio.to_thread(lambda: [p.info['name'] for p in psutil.process_iter(['name'])][:5])
                    # Just log top processes
                    if event_count % 5 == 0:
                        self._log_msg(f"Top processes: {', '.join(procs[:3])}")
                except Exception:
                    pass

        except asyncio.CancelledError:
            pass
        except Exception as e:
            self._log_msg(f"Error: {e}")

    def _on_close(self):
        self._running = False
        if self._daemon_task:
            self._daemon_task.cancel()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    window = StrazhWindow()
    window.run()


if __name__ == "__main__":
    main()
