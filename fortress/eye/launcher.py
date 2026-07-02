"""Fortress Eye — standalone launcher with GUI window."""

import asyncio
import sys
import tkinter as tk
from tkinter import ttk, scrolledtext
from datetime import datetime
from pathlib import Path


class EyeWindow:
    """Tkinter window for Fortress Eye — camera feeds and detections."""

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Fortress Eye — Video Surveillance")
        self.root.geometry("800x600")
        self.root.configure(bg="#0a0a0f")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._running = False
        self._daemon_task = None

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
        ttk.Label(header, text="Fortress Eye", style="Header.TLabel").pack(side=tk.LEFT)
        self._status_label = ttk.Label(header, text="Stopped", style="Status.TLabel")
        self._status_label.pack(side=tk.RIGHT)

        # Cameras grid
        cam_frame = tk.Frame(self.root, bg="#0a0a0f")
        cam_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        self._cam_labels = {}
        for i in range(4):
            row, col = divmod(i, 2)
            frame = tk.Frame(cam_frame, bg="#12121a", relief=tk.RAISED, bd=1)
            frame.grid(row=row, column=col, padx=5, pady=5, sticky="nsew")
            cam_frame.grid_rowconfigure(row, weight=1)
            cam_frame.grid_columnconfigure(col, weight=1)

            label = tk.Label(frame, text=f"Camera {i+1}\nNo signal", bg="#12121a", fg="#666", font=("Segoe UI", 10))
            label.pack(fill=tk.BOTH, expand=True)
            self._cam_labels[i] = label

        # Events log
        log_frame = tk.Frame(self.root, bg="#0a0a0f")
        log_frame.pack(fill=tk.BOTH, padx=10, pady=5)

        tk.Label(log_frame, text="Events", bg="#0a0a0f", fg="#666", font=("Segoe UI", 10)).pack(anchor=tk.W)
        self._log = scrolledtext.ScrolledText(log_frame, height=8, bg="#12121a", fg="#e0e0e0",
                                               font=("Consolas", 9), insertbackground="#00d4ff")
        self._log.pack(fill=tk.BOTH, expand=True)

        # Controls
        ctrl = tk.Frame(self.root, bg="#0a0a0f")
        ctrl.pack(fill=tk.X, padx=10, pady=10)

        self._start_btn = tk.Button(ctrl, text="Start Eye", bg="#00d4ff", fg="#000",
                                     font=("Segoe UI", 10, "bold"), command=self._start, relief=tk.FLAT)
        self._start_btn.pack(side=tk.LEFT, padx=5)

        self._stop_btn = tk.Button(ctrl, text="Stop", bg="#333", fg="#fff",
                                    font=("Segoe UI", 10), command=self._stop, relief=tk.FLAT, state=tk.DISABLED)
        self._stop_btn.pack(side=tk.LEFT, padx=5)

        tk.Button(ctrl, text="Snapshot All", bg="#222", fg="#fff",
                  font=("Segoe UI", 10), command=self._snapshot_all, relief=tk.FLAT).pack(side=tk.RIGHT, padx=5)

    def _log_msg(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self._log.insert(tk.END, f"[{ts}] {msg}\n")
        self._log.see(tk.END)
        if self._log.index(tk.END).split(".")[0] > 200:
            self._log.delete("1.0", "50.0")

    def _start(self):
        self._running = True
        self._start_btn.config(state=tk.DISABLED)
        self._stop_btn.config(state=tk.NORMAL)
        self._status_label.config(text="Running")
        self._log_msg("Eye started")

        # Start camera capture in background
        self._daemon_task = asyncio.ensure_future(self._capture_loop())

    def _stop(self):
        self._running = False
        self._start_btn.config(state=tk.NORMAL)
        self._stop_btn.config(state=tk.DISABLED)
        self._status_label.config(text="Stopped")
        self._log_msg("Eye stopped")
        if self._daemon_task:
            self._daemon_task.cancel()

    async def _capture_loop(self):
        """Camera capture loop — reads from local webcam."""
        try:
            import cv2
        except ImportError:
            self._log_msg("ERROR: opencv-python not installed")
            return

        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            self._log_msg("ERROR: No webcam detected")
            self._cam_labels[0].config(text="Camera 1\nNo webcam", fg="#ff4444")
            return

        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self._log_msg(f"Webcam: {w}x{h}")

        # Try to load YOLO
        yolo = None
        try:
            from fortress.plugins.yolo_detector import YOLODetector
            yolo = YOLODetector("yolov8n.pt", 0.5)
            await asyncio.to_thread(yolo.load)
            if yolo.available:
                self._log_msg("YOLO detector ready")
        except Exception as e:
            self._log_msg(f"YOLO not available: {e}")

        import numpy as np

        try:
            while self._running:
                ret, frame = await asyncio.to_thread(cap.read)
                if not ret:
                    await asyncio.sleep(1)
                    continue

                # Run YOLO
                detections = []
                if yolo and yolo.available:
                    detections = await asyncio.to_thread(yolo.detect, frame)
                    if detections:
                        for d in detections:
                            self._log_msg(f"Detected: {d['class_name']} ({d['confidence']:.0%})")

                # Update camera label with frame info
                label = self._cam_labels[0]
                det_text = f"{len(detections)} objects" if detections else "No objects"
                label.config(text=f"Camera 1\n{w}x{h}\n{det_text}", fg="#00ff88" if detections else "#666")

                await asyncio.sleep(1)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            self._log_msg(f"Error: {e}")
        finally:
            cap.release()

    def _snapshot_all(self):
        self._log_msg("Snapshot: not yet implemented")

    def _on_close(self):
        self._running = False
        if self._daemon_task:
            self._daemon_task.cancel()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    """Entry point for fortress-eye command."""
    window = EyeWindow()
    window.run()


if __name__ == "__main__":
    main()
