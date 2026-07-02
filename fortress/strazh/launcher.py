"""Fortress Strazh — standalone launcher with GUI window."""

import tkinter as tk
from tkinter import ttk, scrolledtext
from datetime import datetime
import threading
import time
import psutil


class StrazhWindow:
    """Tkinter window for Fortress Strazh — monitoring and events."""

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Fortress Strazh — System Monitoring")
        self.root.geometry("800x600")
        self.root.configure(bg="#0a0a0f")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._running = False
        self._thread = None

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
        for name in ["CPU", "RAM", "Events", "Uptime"]:
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
        lines = int(self._log.index(tk.END).split(".")[0])
        if lines > 500:
            self._log.delete("1.0", "100.0")

    def _start(self):
        self._running = True
        self._start_btn.config(state=tk.DISABLED)
        self._stop_btn.config(state=tk.NORMAL)
        self._status_label.config(text="Running")
        self._log_msg("Strazh started — monitoring system")

        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()

    def _stop(self):
        self._running = False
        self._start_btn.config(state=tk.NORMAL)
        self._stop_btn.config(state=tk.DISABLED)
        self._status_label.config(text="Stopped")
        self._log_msg("Strazh stopped")

    def _monitor_loop(self):
        """Monitor system metrics and generate events."""
        event_count = 0
        start_time = datetime.now()

        # Thresholds
        CPU_HIGH = 80
        RAM_HIGH = 80
        check_interval = 3  # seconds

        self.root.after(0, lambda: self._log_msg(f"Thresholds: CPU>{CPU_HIGH}%, RAM>{RAM_HIGH}%"))

        try:
            while self._running:
                time.sleep(check_interval)

                # Read system metrics
                cpu = psutil.cpu_percent(interval=1)
                ram = psutil.virtual_memory().percent

                # Uptime
                uptime = datetime.now() - start_time
                uptime_str = str(uptime).split(".")[0]

                # Update metrics display (thread-safe via root.after)
                self.root.after(0, lambda c=cpu, r=ram, u=uptime_str, ec=event_count:
                    self._update_metrics(c, r, u, ec))

                # Generate events for anomalies
                if cpu > CPU_HIGH:
                    event_count += 1
                    self.root.after(0, lambda c=cpu: self._log_msg(f"HIGH CPU: {c:.0f}%"))

                if ram > RAM_HIGH:
                    event_count += 1
                    self.root.after(0, lambda r=ram: self._log_msg(f"HIGH RAM: {r:.0f}%"))

                # Log periodic status
                if event_count == 0 or event_count % 10 == 0:
                    self.root.after(0, lambda c=cpu, r=ram, ec=event_count:
                        self._log_msg(f"Status: CPU {c:.0f}% | RAM {r:.0f}% | Events {ec}"))

                # Check top processes
                try:
                    procs = [p.info['name'] for p in psutil.process_iter(['name'])][:5]
                    if event_count % 5 == 0:
                        self.root.after(0, lambda p=procs[:3]: self._log_msg(f"Top: {', '.join(p)}"))
                except Exception:
                    pass

        except Exception as e:
            self.root.after(0, lambda: self._log_msg(f"Error: {e}"))

    def _update_metrics(self, cpu, ram, uptime, event_count):
        """Update metrics display (called from main thread)."""
        self._metrics["CPU"].config(text=f"{cpu:.0f}%")
        self._metrics["RAM"].config(text=f"{ram:.0f}%")
        self._metrics["Events"].config(text=str(event_count))
        self._metrics["Uptime"].config(text=uptime)

        # Color metrics based on thresholds
        self._metrics["CPU"].config(fg="#ff4444" if cpu > 80 else "#00ff88")
        self._metrics["RAM"].config(fg="#ff4444" if ram > 80 else "#00ff88")

    def _on_close(self):
        self._running = False
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    """Entry point for fortress-strazh command."""
    window = StrazhWindow()
    window.run()


if __name__ == "__main__":
    main()
