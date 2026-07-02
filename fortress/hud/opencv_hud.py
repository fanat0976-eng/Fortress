"""OpenCV HUD — local window with YOLO bounding boxes and live feed."""

import asyncio
import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fortress.plugins.camera import CameraPlugin

logger = logging.getLogger("fortress.hud.opencv")

# Colors (BGR)
COLOR_BBOX = (0, 255, 0)       # Green for normal detections
COLOR_SUSPICIOUS = (0, 0, 255) # Red for suspicious
COLOR_TEXT_BG = (0, 0, 0)      # Black background for text
COLOR_TEXT = (255, 255, 255)   # White text
COLOR_STATUS = (0, 200, 255)   # Orange for status bar
COLOR_MOTION = (255, 255, 0)   # Cyan for motion

SUSPICIOUS_IDS = {0, 1, 2, 3, 5, 7}  # person, vehicles


class OpenCVHUD:
    """Local overlay window showing camera feeds with AI detections."""

    def __init__(self, camera_plugin: "CameraPlugin", window_name: str = "Fortress HUD"):
        self._cam = camera_plugin
        self._window_name = window_name
        self._running = False
        self._cv2 = None
        self._paused = False
        self._show_stats = True
        self._selected_camera = 0  # Index for multi-camera view

    async def start(self) -> None:
        """Start HUD in background thread."""
        try:
            import cv2
            self._cv2 = cv2
        except ImportError:
            logger.error("opencv not installed — HUD disabled")
            return

        self._running = True
        asyncio.create_task(self._render_loop())
        logger.info("OpenCV HUD started — Q=quit, S=snapshot, P=pause, 1-9=select camera")

    async def stop(self) -> None:
        self._running = False
        if self._cv2:
            try:
                self._cv2.destroyAllWindows()
            except Exception:
                pass
        logger.info("OpenCV HUD stopped")

    async def _render_loop(self) -> None:
        """Main render loop: read frames, draw overlays, display."""
        cv2 = self._cv2

        try:
            while self._running:
                if self._paused:
                    await asyncio.sleep(0.1)
                    continue

                # Get frames from all cameras
                frames = self._get_all_frames()
                if not frames:
                    await asyncio.sleep(0.5)
                    continue

                # Compose display
                display = self._compose_display(frames, cv2)

                # Show
                cv2.imshow(self._window_name, display)

                # Handle keys
                key = cv2.waitKey(1) & 0xFF
                if key == ord('q') or key == 27:  # Q or ESC
                    self._running = False
                    break
                elif key == ord('p'):
                    self._paused = True
                    logger.info("HUD paused")
                elif key == ord('s'):
                    await self._save_hud_screenshot(display, cv2)
                elif key == ord('1'):
                    self._selected_camera = 0
                elif key == ord('2'):
                    self._selected_camera = 1
                elif key == ord('3'):
                    self._selected_camera = 2
                elif key == ord('4'):
                    self._selected_camera = 3
                elif key == ord('h'):
                    self._show_stats = not self._show_stats
                elif key != 255:
                    # Any other key unpauses
                    if self._paused:
                        self._paused = False
                        logger.info("HUD resumed")

                await asyncio.sleep(0.033)  # ~30 FPS

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"HUD render error: {e}")
        finally:
            try:
                cv2.destroyAllWindows()
            except Exception:
                pass

    def _get_all_frames(self) -> list[tuple[str, bytes, list]]:
        """Get frames from all cameras with their detections."""
        result = []
        for cam_dict in self._cam.registry.list_all():
            cam_id = cam_dict["id"]
            frame_bytes = self._cam.get_frame_for_client(cam_id)
            if frame_bytes is None:
                continue

            # Get cached detections (if any)
            detections = self._cam._last_detections.get(cam_id, [])
            result.append((cam_dict["name"], frame_bytes, detections))

        return result

    def _compose_display(self, frames: list[tuple[str, bytes, list]], cv2) -> object:
        """Compose multi-camera display with overlays."""
        import numpy as np

        if len(frames) == 1:
            # Single camera: full size
            name, jpeg, dets = frames[0]
            arr = np.frombuffer(jpeg, dtype=np.uint8)
            frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if frame is None:
                frame = np.zeros((480, 640, 3), dtype=np.uint8)
            self._draw_overlay(frame, name, dets, cv2)
            return frame

        # Multi-camera: 2x2 grid
        grid = np.zeros((960, 1280, 3), dtype=np.uint8)
        positions = [(0, 0), (0, 640), (480, 0), (480, 640)]

        for i, (name, jpeg, dets) in enumerate(frames[:4]):
            arr = np.frombuffer(jpeg, dtype=np.uint8)
            frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if frame is None:
                frame = np.zeros((480, 640, 3), dtype=np.uint8)
            else:
                frame = cv2.resize(frame, (640, 480))

            self._draw_overlay(frame, name, dets, cv2)
            y, x = positions[i]
            grid[y:y+480, x:x+640] = frame

        # Draw grid lines
        cv2.line(grid, (640, 0), (640, 960), (50, 50, 50), 1)
        cv2.line(grid, (0, 480), (1280, 480), (50, 50, 50), 1)

        return grid

    def _draw_overlay(self, frame: object, camera_name: str,
                      detections: list, cv2) -> None:
        """Draw YOLO bounding boxes and status bar on frame."""
        h, w = frame.shape[:2]

        # Draw bounding boxes
        for det in detections:
            bbox = det.get("bbox", {})
            x1, y1 = int(bbox.get("x1", 0)), int(bbox.get("y1", 0))
            x2, y2 = int(bbox.get("x2", 0)), int(bbox.get("y2", 0))
            class_name = det.get("class_name", "?")
            conf = det.get("confidence", 0)
            cls_id = det.get("class_id", -1)

            color = COLOR_SUSPICIOUS if cls_id in SUSPICIOUS_IDS else COLOR_BBOX
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)

            # Label
            label = f"{class_name} {conf:.0%}"
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
            cv2.rectangle(frame, (x1, y1 - th - 8), (x1 + tw + 4, y1), color, -1)
            cv2.putText(frame, label, (x1 + 2, y1 - 4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, COLOR_TEXT, 1)

        # Status bar at bottom
        if self._show_stats:
            bar_h = 30
            cv2.rectangle(frame, (0, h - bar_h), (w, h), COLOR_TEXT_BG, -1)

            # Camera name
            cv2.putText(frame, camera_name, (10, h - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, COLOR_STATUS, 1)

            # Detection count
            det_count = len(detections)
            det_text = f"Objects: {det_count}"
            cv2.putText(frame, det_text, (w - 150, h - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, COLOR_TEXT, 1)

            # Status
            status = "PAUSED" if self._paused else "LIVE"
            color = COLOR_SUSPICIOUS if self._paused else COLOR_BBOX
            cv2.putText(frame, status, (w - 60, h - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)

        # Title bar at top
        cv2.rectangle(frame, (0, 0), (w, 25), COLOR_TEXT_BG, -1)
        cv2.putText(frame, f"Fortress HUD — {camera_name}", (10, 18),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, COLOR_STATUS, 1)

    async def _save_hud_screenshot(self, frame: object, cv2) -> None:
        """Save current HUD frame as screenshot."""
        from pathlib import Path
        from datetime import datetime
        hud_dir = Path.home() / ".fortress" / "hud_screenshots"
        hud_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = hud_dir / f"hud_{ts}.jpg"
        cv2.imwrite(str(path), frame)
        logger.info(f"HUD screenshot: {path}")
