"""Fortress Eye — standalone launcher with live video."""

import asyncio
import tkinter as tk
from tkinter import ttk, scrolledtext
from datetime import datetime
from PIL import Image, ImageTk


class EyeWindow:
    """Tkinter window for Fortress Eye — live camera feeds."""

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Fortress Eye — Video Surveillance")
        self.root.geometry("900x650")
        self.root.configure(bg="#0a0a0f")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._running = False
        self._daemon_task = None
        self._cam_images = [None] * 4  # Keep references to prevent GC

        self._setup_ui()

    def _setup_ui(self):
        # Header
        header = tk.Frame(self.root, bg="#12121a", height=45)
        header.pack(fill=tk.X)
        header.pack_propagate(False)

        tk.Label(header, text="Fortress Eye", bg="#12121a", fg="#00d4ff",
                 font=("Segoe UI", 14, "bold")).pack(side=tk.LEFT, padx=15)
        self._status_label = tk.Label(header, text="Stopped", bg="#12121a", fg="#666",
                                       font=("Segoe UI", 10))
        self._status_label.pack(side=tk.RIGHT, padx=15)

        # Cameras grid
        cam_frame = tk.Frame(self.root, bg="#0a0a0f")
        cam_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        self._cam_canvases = []
        for i in range(4):
            row, col = divmod(i, 2)
            f = tk.Frame(cam_frame, bg="#12121a", relief=tk.RAISED, bd=1)
            f.grid(row=row, column=col, padx=3, pady=3, sticky="nsew")
            cam_frame.grid_rowconfigure(row, weight=1)
            cam_frame.grid_columnconfigure(col, weight=1)

            canvas = tk.Canvas(f, bg="#000", highlightthickness=0)
            canvas.pack(fill=tk.BOTH, expand=True)
            canvas.create_text(160, 90, text=f"Camera {i+1}\nNo signal", fill="#666",
                               font=("Segoe UI", 11), tags="placeholder")
            self._cam_canvases.append(canvas)

        # Log
        log_frame = tk.Frame(self.root, bg="#0a0a0f")
        log_frame.pack(fill=tk.BOTH, padx=10, pady=5)

        self._log = scrolledtext.ScrolledText(log_frame, height=6, bg="#12121a", fg="#e0e0e0",
                                               font=("Consolas", 9))
        self._log.pack(fill=tk.BOTH, expand=True)

        # Controls
        ctrl = tk.Frame(self.root, bg="#0a0a0f")
        ctrl.pack(fill=tk.X, padx=10, pady=10)

        self._start_btn = tk.Button(ctrl, text="Start Eye", bg="#00ff88", fg="#000",
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
        if lines > 200:
            self._log.delete("1.0", "50.0")

    def _start(self):
        self._running = True
        self._start_btn.config(state=tk.DISABLED)
        self._stop_btn.config(state=tk.NORMAL)
        self._status_label.config(text="Running", fg="#00ff88")
        self._log_msg("Eye started — scanning cameras...")
        self._daemon_task = asyncio.ensure_future(self._capture_loop())

    def _stop(self):
        self._running = False
        self._start_btn.config(state=tk.NORMAL)
        self._stop_btn.config(state=tk.DISABLED)
        self._status_label.config(text="Stopped", fg="#666")
        self._log_msg("Eye stopped")
        if self._daemon_task:
            self._daemon_task.cancel()

    async def _capture_loop(self):
        """Capture from all available cameras and display in canvas."""
        try:
            import cv2
            import numpy as np
        except ImportError:
            self._log_msg("ERROR: opencv-python not installed")
            return

        # Find available cameras
        cameras = []
        for i in range(4):
            cap = cv2.VideoCapture(i)
            if cap.isOpened():
                w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                cameras.append((i, cap, w, h))
                self._log_msg(f"Camera {i}: {w}x{h}")
            else:
                cap.release()

        if not cameras:
            self._log_msg("No cameras found")
            self._cam_canvases[0].create_text(160, 90, text="No cameras detected", fill="#ff4444",
                                               font=("Segoe UI", 11), tags="error")
            return

        self._log_msg(f"Found {len(cameras)} camera(s)")

        # Load YOLO
        yolo = None
        try:
            from fortress.plugins.yolo_detector import YOLODetector
            yolo = YOLODetector("yolov8n.pt", 0.5)
            await asyncio.to_thread(yolo.load)
            if yolo.available:
                self._log_msg("YOLO ready")
        except Exception:
            pass

        try:
            while self._running:
                for cam_idx, (cam_id, cap, w, h) in enumerate(cameras):
                    if cam_idx >= 4:
                        break

                    ret, frame = await asyncio.to_thread(cap.read)
                    if not ret:
                        continue

                    # YOLO detection
                    detections = []
                    if yolo and yolo.available:
                        detections = await asyncio.to_thread(yolo.detect, frame)
                        if detections:
                            for d in detections:
                                self._log_msg(f"Cam{cam_idx}: {d['class_name']} ({d['confidence']:.0%})")

                    # Draw bounding boxes
                    for d in detections:
                        bbox = d.get("bbox", {})
                        x1, y1 = int(bbox.get("x1", 0)), int(bbox.get("y1", 0))
                        x2, y2 = int(bbox.get("x2", 0)), int(bbox.get("y2", 0))
                        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                        label = f"{d['class_name']} {d['confidence']:.0%}"
                        cv2.putText(frame, label, (x1, y1 - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)

                    # Resize for display
                    canvas = self._cam_canvases[cam_idx]
                    cw = max(canvas.winfo_width(), 200)
                    ch = max(canvas.winfo_height(), 150)
                    scale = min(cw / w, ch / h)
                    new_w, new_h = int(w * scale), int(h * scale)
                    frame_resized = cv2.resize(frame, (new_w, new_h))

                    # Convert to PhotoImage
                    frame_rgb = cv2.cvtColor(frame_resized, cv2.COLOR_BGR2RGB)
                    img = Image.fromarray(frame_rgb)
                    photo = ImageTk.PhotoImage(img)

                    # Update canvas
                    canvas.delete("all")
                    canvas.create_image(cw // 2, ch // 2, image=photo, anchor=tk.CENTER)
                    self._cam_images[cam_idx] = photo  # Prevent GC

                await asyncio.sleep(0.1)  # ~10 FPS

        except asyncio.CancelledError:
            pass
        except Exception as e:
            self._log_msg(f"Error: {e}")
        finally:
            for _, cap, _, _ in cameras:
                cap.release()

    def _on_close(self):
        self._running = False
        if self._daemon_task:
            self._daemon_task.cancel()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    window = EyeWindow()
    window.run()


if __name__ == "__main__":
    main()
