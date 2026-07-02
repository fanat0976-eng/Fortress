"""Camera plugin — multi-source: local USB, RTSP IP cameras, remote WS cameras."""

import asyncio
import base64
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from fortress.core.plugin import BasePlugin
from fortress.core.event_bus import Event
from fortress.core.camera_registry import CameraRegistry, Camera, CameraType, CameraStatus

if TYPE_CHECKING:
    from fortress.core.config import PluginConfig
    from fortress.core.event_bus import EventBus

logger = logging.getLogger("fortress.plugins.camera")

SNAPSHOT_DIR = Path.home() / ".fortress" / "snapshots"


class CameraPlugin(BasePlugin):
    """Multi-camera: local USB + RTSP IP + remote WebSocket push."""

    name = "camera"
    description = "Multi-camera with YOLO detection, AI analysis, snapshots"

    def __init__(self, config: "PluginConfig"):
        self.config = config
        self._bus = None
        self._running = False
        self._tasks: dict[str, asyncio.Task] = {}  # camera_id → task
        self.registry = CameraRegistry()
        self._prev_frames: dict[str, object] = {}  # camera_id → prev gray frame
        self._motion_threshold = getattr(config, "motion_threshold", 5000)
        self._frame_interval = getattr(config, "frame_interval", 1.0)
        self._ai_model = getattr(config, "ai_model", "llava")
        self._yolo_model = getattr(config, "yolo_model", "yolov8n.pt")
        self._yolo_confidence = getattr(config, "yolo_confidence", 0.5)
        self._ollama_url = "http://127.0.0.1:11434"
        self._last_analysis: dict[str, float] = {}  # camera_id → timestamp
        self._analysis_cooldown = 15
        self._yolo = None
        self._cv2 = None
        self._np = None
        # Remote camera frame buffer: camera_id → (frame_bytes, timestamp)
        self._remote_frames: dict[str, tuple[bytes, float]] = {}
        # Cached last frame per camera for client viewing: camera_id → jpeg bytes
        self._last_frames: dict[str, bytes] = {}
        # Cached detections per camera for HUD overlay: camera_id → list[detection]
        self._last_detections: dict[str, list] = {}

    async def start(self, bus: "EventBus") -> None:
        self._bus = bus
        self._running = True
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

        await self._load_yolo()
        self._register_default_cameras()

        # Start capture for each registered camera
        for cam in self.registry.list_all():
            task = asyncio.create_task(self._camera_capture_loop(cam["id"]))
            self._tasks[cam["id"]] = task

        logger.info(f"Camera plugin started ({self.registry.count()} cameras)")

    def _register_default_cameras(self) -> None:
        """Register configured cameras from config."""
        # Local webcam
        self.registry.register("Local Webcam", CameraType.LOCAL, "0")

        # RTSP cameras from config
        rtsp_urls = getattr(self.config, "rtsp_urls", [])
        for i, url in enumerate(rtsp_urls):
            self.registry.register(f"RTSP Camera {i+1}", CameraType.RTSP, url)

    async def _load_yolo(self) -> None:
        try:
            from fortress.plugins.yolo_detector import YOLODetector
            self._yolo = YOLODetector(self._yolo_model, self._yolo_confidence)
            await asyncio.to_thread(self._yolo.load)
            if self._yolo.available:
                logger.info("YOLO detector ready")
            else:
                logger.warning("YOLO not available — motion-only detection")
        except Exception as e:
            logger.warning(f"YOLO init failed: {e}")

    async def stop(self) -> None:
        self._running = False
        for cam_id, task in self._tasks.items():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks.clear()
        self._bus = None
        logger.info("Camera plugin stopped")

    def cancel_camera_task(self, camera_id: str) -> bool:
        """Cancel capture task for a specific camera. Returns True if found and cancelled."""
        task = self._tasks.pop(camera_id, None)
        if task:
            task.cancel()
            return True
        return False

    # === Camera source management ===

    def register_remote_camera(self, camera_id: str, token: str) -> Camera | None:
        """Register a remote camera that connects via WebSocket."""
        cam = self.registry.get(camera_id)
        if cam and cam.camera_type == CameraType.REMOTE:
            return cam
        return None

    def add_remote_frame(self, camera_id: str, frame_bytes: bytes) -> bool:
        """Buffer a frame from a remote camera (called from WS handler)."""
        cam = self.registry.get(camera_id)
        if not cam:
            return False
        self._remote_frames[camera_id] = (frame_bytes, time.time())
        self.registry.update_frame(camera_id)
        return True

    def get_frame_for_client(self, camera_id: str) -> bytes | None:
        """Get latest JPEG frame for dashboard/client viewing."""
        if camera_id in self._remote_frames:
            return self._remote_frames[camera_id][0]
        # For local/RTSP — need to capture on demand (or cache last frame)
        return self._last_frames.get(camera_id)

    # === Capture loops ===

    async def _camera_capture_loop(self, camera_id: str) -> None:
        """Main loop per camera: capture → YOLO → motion → snapshot → AI."""
        cam = self.registry.get(camera_id)
        if not cam:
            return

        if cam.camera_type == CameraType.REMOTE:
            await self._remote_camera_loop(cam)
        else:
            await self._local_rtsp_loop(cam)

    async def _local_rtsp_loop(self, cam: Camera) -> None:
        """Capture loop for local USB and RTSP cameras."""
        try:
            import cv2
            import numpy as np
            self._cv2 = cv2
            self._np = np
        except ImportError:
            logger.error("opencv not installed")
            return

        cv2 = self._cv2
        source = int(cam.url) if cam.url.isdigit() else cam.url
        cap = cv2.VideoCapture(source)

        if not cap.isOpened():
            logger.error(f"Cannot open camera: {cam.name} ({cam.url})")
            self.registry.update_status(cam.id, CameraStatus.ERROR)
            return

        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.registry.update_status(cam.id, CameraStatus.ONLINE, f"{w}x{h}")
        logger.info(f"Camera {cam.name}: {w}x{h}")

        prev_frame = None
        try:
            while self._running:
                ret, frame = await asyncio.to_thread(cap.read)
                if not ret:
                    if cam.camera_type == CameraType.RTSP:
                        logger.warning(f"RTSP reconnect: {cam.name}")
                        await asyncio.sleep(2)
                        cap.release()
                        cap = cv2.VideoCapture(source)
                    else:
                        await asyncio.sleep(1)
                    continue

                # Cache frame for client viewing
                _, jpeg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                self._last_frames[cam.id] = jpeg.tobytes()
                self.registry.update_frame(cam.id, f"{w}x{h}")

                # === YOLO detection ===
                yolo_detections = []
                if self._yolo and self._yolo.available:
                    yolo_detections = await asyncio.to_thread(self._yolo.detect, frame)
                    self._last_detections[cam.id] = yolo_detections
                    if yolo_detections:
                        await self._bus.emit(Event(
                            type="camera.detection",
                            source=f"plugin.camera.{cam.id}",
                            payload={
                                "camera_id": cam.id, "camera_name": cam.name,
                                "detections": yolo_detections,
                                "count": len(yolo_detections),
                                "classes": [d["class_name"] for d in yolo_detections],
                            },
                            severity=1 if self._yolo.has_suspicious(yolo_detections) else 0,
                        ))

                # === Motion detection ===
                small = cv2.resize(frame, (320, 240))
                gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
                gray = cv2.GaussianBlur(gray, (21, 21), 0)

                prev = self._prev_frames.get(cam.id)
                motion_detected = False
                if prev is not None:
                    delta = cv2.absdiff(prev, gray)
                    thresh = cv2.threshold(delta, 25, 255, cv2.THRESH_BINARY)[1]
                    motion_pixels = cv2.countNonZero(thresh)
                    if motion_pixels > self._motion_threshold:
                        motion_detected = True
                        await self._bus.emit(Event(
                            type="camera.motion",
                            source=f"plugin.camera.{cam.id}",
                            payload={
                                "camera_id": cam.id, "camera_name": cam.name,
                                "motion_pixels": motion_pixels,
                            },
                            severity=0,
                        ))
                self._prev_frames[cam.id] = gray

                # === Snapshot + AI (if interesting) ===
                should_analyze = (
                    (self._yolo and self._yolo.available and self._yolo.has_suspicious(yolo_detections))
                    or (not (self._yolo and self._yolo.available) and motion_detected)
                )
                if should_analyze:
                    now = time.time()
                    last = self._last_analysis.get(cam.id, 0)
                    if now - last > self._analysis_cooldown:
                        self._last_analysis[cam.id] = now
                        snapshot_path = await self._save_snapshot(frame, cam.id)
                        asyncio.create_task(self._analyze_frame(
                            frame, snapshot_path, yolo_detections, cam))

                await asyncio.sleep(self._frame_interval)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Camera {cam.name} error: {e}")
            self.registry.update_status(cam.id, CameraStatus.ERROR)
        finally:
            cap.release()

    async def _remote_camera_loop(self, cam: Camera) -> None:
        """Process frames from remote camera (pushed via WebSocket)."""
        logger.info(f"Remote camera {cam.name} waiting for frames...")
        while self._running:
            # Check if we have a fresh frame
            if cam.id in self._remote_frames:
                frame_bytes, ts = self._remote_frames[cam.id]
                if time.time() - ts > 10:
                    self.registry.update_status(cam.id, CameraStatus.OFFLINE)
                else:
                    self.registry.update_status(cam.id, CameraStatus.ONLINE)
                    # Process frame for YOLO/AI if needed
                    await self._process_remote_frame(cam, frame_bytes)
            await asyncio.sleep(1)

    async def _process_remote_frame(self, cam: Camera, frame_bytes: bytes) -> None:
        """Run YOLO/AI on a remote camera frame."""
        if not (self._yolo and self._yolo.available):
            return

        try:
            import cv2
            import numpy as np
            arr = np.frombuffer(frame_bytes, dtype=np.uint8)
            frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if frame is None:
                return

            yolo_detections = await asyncio.to_thread(self._yolo.detect, frame)
            if yolo_detections:
                await self._bus.emit(Event(
                    type="camera.detection",
                    source=f"plugin.camera.{cam.id}",
                    payload={
                        "camera_id": cam.id, "camera_name": cam.name,
                        "detections": yolo_detections,
                        "count": len(yolo_detections),
                        "classes": [d["class_name"] for d in yolo_detections],
                    },
                    severity=1 if self._yolo.has_suspicious(yolo_detections) else 0,
                ))
        except Exception as e:
            logger.debug(f"Remote frame process error: {e}")

    # === Snapshot & AI ===

    async def _save_snapshot(self, frame, camera_id: str) -> Path | None:
        try:
            cv2 = self._cv2
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            cam_dir = SNAPSHOT_DIR / camera_id
            cam_dir.mkdir(parents=True, exist_ok=True)
            path = cam_dir / f"snapshot_{ts}.jpg"
            await asyncio.to_thread(cv2.imwrite, str(path), frame)
            return path
        except Exception as e:
            logger.error(f"Snapshot error: {e}")
            return None

    async def _analyze_frame(self, frame, snapshot_path: Path | None,
                             yolo_detections: list, cam: Camera) -> None:
        """Deep analysis with llava."""
        try:
            cv2 = self._cv2
            yolo_context = ""
            if yolo_detections:
                objects = [f"{d['class_name']}({d['confidence']:.0%})" for d in yolo_detections]
                yolo_context = f"\nYOLO detected: {', '.join(objects)}. "

            small = cv2.resize(frame, (256, 192))
            _, buffer = cv2.imencode('.jpg', small, [cv2.IMWRITE_JPEG_QUALITY, 50])
            img_base64 = base64.b64encode(buffer).decode('utf-8')

            prompt = f"Describe what you see on {cam.name}. {yolo_context}Be brief."

            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(
                    f"{self._ollama_url}/api/generate",
                    json={"model": self._ai_model, "prompt": prompt,
                          "images": [img_base64], "stream": False},
                )
                resp.raise_for_status()
                analysis = resp.json().get("response", "No analysis")

            severity = 1
            if any(kw in analysis.lower() for kw in ["intruder", "threat", "danger", "suspicious"]):
                severity = 2

            await self._bus.emit(Event(
                type="camera.analysis",
                source=f"plugin.camera.{cam.id}",
                payload={
                    "camera_id": cam.id, "camera_name": cam.name,
                    "analysis": analysis,
                    "yolo_objects": [d["class_name"] for d in yolo_detections],
                    "snapshot": str(snapshot_path) if snapshot_path else None,
                },
                severity=severity,
            ))
            logger.info(f"AI [{cam.name}]: {analysis[:120]}")

            # Send photo to Telegram on high severity
            if severity >= 2 and snapshot_path:
                await self._send_snapshot_to_telegram(snapshot_path, cam.name, analysis)

        except Exception as e:
            logger.error(f"AI analysis [{cam.name}]: {type(e).__name__}: {e}")

    async def _send_snapshot_to_telegram(self, snapshot_path: Path, camera_name: str, analysis: str) -> None:
        """Send snapshot photo to Telegram if available."""
        try:
            # Find telegram plugin from bus subscribers
            for pattern, handler in self._bus._subscribers:
                if hasattr(handler, '__self__') and hasattr(handler.__self__, 'send_photo'):
                    telegram = handler.__self__
                    caption = f"🚨 {camera_name}: {analysis[:200]}"
                    await telegram.send_photo(str(snapshot_path), caption)
                    logger.info(f"Snapshot sent to Telegram: {camera_name}")
                    return
        except Exception as e:
            logger.debug(f"Telegram send_photo error: {e}")

    def config_schema(self) -> dict:
        return {
            "motion_threshold": {"type": "integer", "default": 5000},
            "frame_interval": {"type": "number", "default": 1.0},
            "ai_model": {"type": "string", "default": "llava"},
            "yolo_model": {"type": "string", "default": "yolov8n.pt"},
            "yolo_confidence": {"type": "number", "default": 0.5},
            "rtsp_urls": {"type": "array", "items": {"type": "string"}},
        }
