"""Fortress V2.1.1D — Orchestrator (Tkinter control panel)."""

import asyncio
import subprocess
import sys
import tkinter as tk
from tkinter import ttk, scrolledtext
from datetime import datetime
from pathlib import Path


class OrchestratorWindow:
    """Main control panel — scans plugins, checks Ollama, launches Eye/Strazh."""

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Fortress V2.1.1D (Dual)")
        self.root.geometry("700x550")
        self.root.configure(bg="#0a0a0f")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._eye_process = None
        self._strazh_process = None
        self._ollama_ok = False
        self._camera_ok = False

        self._setup_ui()
        self._scan_system()

    def _setup_ui(self):
        # Header
        header = tk.Frame(self.root, bg="#12121a", height=50)
        header.pack(fill=tk.X)
        header.pack_propagate(False)

        tk.Label(header, text="Fortress V2.1.1D", bg="#12121a", fg="#00d4ff",
                 font=("Segoe UI", 16, "bold")).pack(side=tk.LEFT, padx=15, pady=10)
        tk.Label(header, text="Dual — Orchestrator", bg="#12121a", fg="#666",
                 font=("Segoe UI", 11)).pack(side=tk.LEFT, pady=10)

        # System status
        status_frame = tk.Frame(self.root, bg="#0a0a0f")
        status_frame.pack(fill=tk.X, padx=15, pady=10)

        tk.Label(status_frame, text="System Status", bg="#0a0a0f", fg="#666",
                 font=("Segoe UI", 11, "bold")).pack(anchor=tk.W)

        self._ollama_label = tk.Label(status_frame, text="Ollama: checking...", bg="#0a0a0f", fg="#666",
                                       font=("Segoe UI", 10))
        self._ollama_label.pack(anchor=tk.W, padx=10)

        self._camera_label = tk.Label(status_frame, text="Camera: checking...", bg="#0a0a0f", fg="#666",
                                       font=("Segoe UI", 10))
        self._camera_label.pack(anchor=tk.W, padx=10)

        self._models_label = tk.Label(status_frame, text="Models: --", bg="#0a0a0f", fg="#666",
                                       font=("Segoe UI", 10))
        self._models_label.pack(anchor=tk.W, padx=10)

        # Modules
        modules_frame = tk.Frame(self.root, bg="#0a0a0f")
        modules_frame.pack(fill=tk.X, padx=15, pady=5)

        tk.Label(modules_frame, text="Modules", bg="#0a0a0f", fg="#666",
                 font=("Segoe UI", 11, "bold")).pack(anchor=tk.W)

        # Eye module
        eye_frame = tk.Frame(modules_frame, bg="#12121a", relief=tk.RAISED, bd=1)
        eye_frame.pack(fill=tk.X, padx=5, pady=5)

        eye_info = tk.Frame(eye_frame, bg="#12121a")
        eye_info.pack(fill=tk.X, padx=10, pady=8)

        tk.Label(eye_info, text="Eye", bg="#12121a", fg="#00d4ff",
                 font=("Segoe UI", 12, "bold")).pack(side=tk.LEFT)
        tk.Label(eye_info, text="Video surveillance", bg="#12121a", fg="#666",
                 font=("Segoe UI", 10)).pack(side=tk.LEFT, padx=10)

        self._eye_status = tk.Label(eye_info, text="Stopped", bg="#12121a", fg="#666",
                                     font=("Segoe UI", 10))
        self._eye_status.pack(side=tk.RIGHT)

        eye_btn = tk.Frame(eye_frame, bg="#12121a")
        eye_btn.pack(fill=tk.X, padx=10, pady=(0, 8))

        self._eye_start = tk.Button(eye_btn, text="Start Eye", bg="#00ff88", fg="#000",
                                     font=("Segoe UI", 10, "bold"), width=12, relief=tk.FLAT,
                                     command=self._start_eye)
        self._eye_start.pack(side=tk.LEFT, padx=5)

        self._eye_stop = tk.Button(eye_btn, text="Stop", bg="#333", fg="#fff",
                                    font=("Segoe UI", 10), width=8, relief=tk.FLAT,
                                    command=self._stop_eye, state=tk.DISABLED)
        self._eye_stop.pack(side=tk.LEFT, padx=5)

        # Strazh module
        strazh_frame = tk.Frame(modules_frame, bg="#12121a", relief=tk.RAISED, bd=1)
        strazh_frame.pack(fill=tk.X, padx=5, pady=5)

        strazh_info = tk.Frame(strazh_frame, bg="#12121a")
        strazh_info.pack(fill=tk.X, padx=10, pady=8)

        tk.Label(strazh_info, text="Strazh", bg="#12121a", fg="#00d4ff",
                 font=("Segoe UI", 12, "bold")).pack(side=tk.LEFT)
        tk.Label(strazh_info, text="System monitoring", bg="#12121a", fg="#666",
                 font=("Segoe UI", 10)).pack(side=tk.LEFT, padx=10)

        self._strazh_status = tk.Label(strazh_info, text="Stopped", bg="#12121a", fg="#666",
                                        font=("Segoe UI", 10))
        self._strazh_status.pack(side=tk.RIGHT)

        strazh_btn = tk.Frame(strazh_frame, bg="#12121a")
        strazh_btn.pack(fill=tk.X, padx=10, pady=(0, 8))

        self._strazh_start = tk.Button(strazh_btn, text="Start Strazh", bg="#00ff88", fg="#000",
                                        font=("Segoe UI", 10, "bold"), width=12, relief=tk.FLAT,
                                        command=self._start_strazh)
        self._strazh_start.pack(side=tk.LEFT, padx=5)

        self._strazh_stop = tk.Button(strazh_btn, text="Stop", bg="#333", fg="#fff",
                                       font=("Segoe UI", 10), width=8, relief=tk.FLAT,
                                       command=self._stop_strazh, state=tk.DISABLED)
        self._strazh_stop.pack(side=tk.LEFT, padx=5)

        # Log
        log_frame = tk.Frame(self.root, bg="#0a0a0f")
        log_frame.pack(fill=tk.BOTH, expand=True, padx=15, pady=(5, 15))

        tk.Label(log_frame, text="Log", bg="#0a0a0f", fg="#666",
                 font=("Segoe UI", 10)).pack(anchor=tk.W)

        self._log = scrolledtext.ScrolledText(log_frame, height=8, bg="#12121a", fg="#e0e0e0",
                                               font=("Consolas", 9), insertbackground="#00d4ff")
        self._log.pack(fill=tk.BOTH, expand=True)

    def _log_msg(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self._log.insert(tk.END, f"[{ts}] {msg}\n")
        self._log.see(tk.END)
        if self._log.index(tk.END).split(".")[0] > 200:
            self._log.delete("1.0", "50.0")

    def _scan_system(self):
        """Check Ollama and camera availability."""
        self._log_msg("Scanning system...")

        # Check Ollama
        try:
            import urllib.request
            req = urllib.request.Request("http://127.0.0.1:11434/api/tags")
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = __import__("json").loads(resp.read())
                models = [m["name"] for m in data.get("models", [])]
                self._ollama_ok = True
                self._ollama_label.config(text="Ollama: running", fg="#00ff88")
                self._models_label.config(text=f"Models: {', '.join(models[:5])}", fg="#e0e0e0")
                self._log_msg(f"Ollama: {len(models)} models")
        except Exception:
            self._ollama_label.config(text="Ollama: not running", fg="#ff4444")
            self._log_msg("Ollama: not running (AI features disabled)")

        # Check camera
        try:
            import cv2
            cap = cv2.VideoCapture(0)
            if cap.isOpened():
                w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                cap.release()
                self._camera_ok = True
                self._camera_label.config(text=f"Camera: detected ({w}x{h})", fg="#00ff88")
                self._log_msg(f"Camera: {w}x{h}")
            else:
                self._camera_label.config(text="Camera: not detected", fg="#ff4444")
        except ImportError:
            self._camera_label.config(text="Camera: opencv not installed", fg="#ff4444")
        except Exception:
            self._camera_label.config(text="Camera: error", fg="#ff4444")

        self._log_msg("System scan complete")

    def _start_eye(self):
        """Launch Eye module in subprocess."""
        if self._eye_process and self._eye_process.poll() is None:
            return

        self._log_msg("Starting Eye...")
        self._eye_status.config(text="Starting...", fg="#ffaa00")

        try:
            self._eye_process = subprocess.Popen(
                [sys.executable, "-m", "fortress.eye.launcher"],
                creationflags=getattr(subprocess, 'CREATE_NEW_WINDOW', 0),
            )
            self._eye_start.config(state=tk.DISABLED)
            self._eye_stop.config(state=tk.NORMAL)
            self._eye_status.config(text="Running", fg="#00ff88")
            self._log_msg("Eye started")
        except Exception as e:
            self._log_msg(f"Eye start failed: {e}")
            self._eye_status.config(text="Error", fg="#ff4444")

    def _stop_eye(self):
        """Stop Eye module."""
        if self._eye_process:
            self._eye_process.terminate()
            self._eye_process = None
        self._eye_start.config(state=tk.NORMAL)
        self._eye_stop.config(state=tk.DISABLED)
        self._eye_status.config(text="Stopped", fg="#666")
        self._log_msg("Eye stopped")

    def _start_strazh(self):
        """Launch Strazh module in subprocess."""
        if self._strazh_process and self._strazh_process.poll() is None:
            return

        self._log_msg("Starting Strazh...")
        self._strazh_status.config(text="Starting...", fg="#ffaa00")

        try:
            self._strazh_process = subprocess.Popen(
                [sys.executable, "-m", "fortress.strazh.launcher"],
                creationflags=getattr(subprocess, 'CREATE_NEW_WINDOW', 0),
            )
            self._strazh_start.config(state=tk.DISABLED)
            self._strazh_stop.config(state=tk.NORMAL)
            self._strazh_status.config(text="Running", fg="#00ff88")
            self._log_msg("Strazh started")
        except Exception as e:
            self._log_msg(f"Strazh start failed: {e}")
            self._strazh_status.config(text="Error", fg="#ff4444")

    def _stop_strazh(self):
        """Stop Strazh module."""
        if self._strazh_process:
            self._strazh_process.terminate()
            self._strazh_process = None
        self._strazh_start.config(state=tk.NORMAL)
        self._strazh_stop.config(state=tk.DISABLED)
        self._strazh_status.config(text="Stopped", fg="#666")
        self._log_msg("Strazh stopped")

    def _on_close(self):
        """Cleanup and close."""
        self._stop_eye()
        self._stop_strazh()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    """Entry point for fortress command."""
    window = OrchestratorWindow()
    window.run()


if __name__ == "__main__":
    main()
