"""Camera registry — manages all camera sources (local, RTSP, remote)."""

import logging
import secrets
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional

logger = logging.getLogger("fortress.camera_registry")


class CameraType(str, Enum):
    LOCAL = "local"       # USB webcam via cv2.VideoCapture(N)
    RTSP = "rtsp"         # IP camera via RTSP stream
    REMOTE = "remote"     # Remote camera pushing via WebSocket


class CameraStatus(str, Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    ERROR = "error"


@dataclass
class Camera:
    id: str
    name: str
    camera_type: CameraType
    url: str                          # "0" for local, "rtsp://..." for RTSP
    token: str                        # Auth token for remote cameras
    status: CameraStatus = CameraStatus.OFFLINE
    last_frame_time: float = 0.0
    resolution: str = ""              # "640x480"
    fps: float = 0.0
    created_at: float = 0.0
    ip_address: str = ""              # Client IP for remote cameras

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "type": self.camera_type.value,
            "url": self.url if self.camera_type != CameraType.REMOTE else "***",
            "status": self.status.value, "resolution": self.resolution,
            "fps": round(self.fps, 1), "last_frame": self.last_frame_time,
            "ip": self.ip_address,
        }


class CameraRegistry:
    """In-memory registry of all cameras with auth."""

    def __init__(self):
        self._cameras: dict[str, Camera] = {}

    def register(self, name: str, camera_type: CameraType, url: str,
                 ip_whitelist: list[str] = None) -> Camera:
        """Register a new camera. Returns camera with generated token."""
        camera_id = f"cam_{secrets.token_hex(4)}"
        token = secrets.token_urlsafe(32)
        camera = Camera(
            id=camera_id, name=name, camera_type=camera_type, url=url,
            token=token, created_at=time.time(),
        )
        self._cameras[camera_id] = camera
        logger.info(f"Camera registered: {name} ({camera_type.value}) → {camera_id}")
        return camera

    def get(self, camera_id: str) -> Optional[Camera]:
        return self._cameras.get(camera_id)

    def get_by_token(self, token: str) -> Optional[Camera]:
        """Find camera by auth token (for remote WS connections)."""
        for cam in self._cameras.values():
            if secrets.compare_digest(cam.token, token):
                return cam
        return None

    def validate_token(self, token: str) -> Optional[Camera]:
        """Validate a camera token and return the camera."""
        cam = self.get_by_token(token)
        if cam is None:
            return None
        if cam.camera_type != CameraType.REMOTE:
            return None  # Only remote cameras connect via token
        return cam

    def update_status(self, camera_id: str, status: CameraStatus,
                      resolution: str = "", fps: float = 0) -> None:
        cam = self._cameras.get(camera_id)
        if cam:
            cam.status = status
            if resolution:
                cam.resolution = resolution
            if fps:
                cam.fps = fps
            cam.last_frame_time = time.time()

    def update_frame(self, camera_id: str, resolution: str = "", fps: float = 0) -> None:
        """Update last frame timestamp and stats."""
        cam = self._cameras.get(camera_id)
        if cam:
            cam.last_frame_time = time.time()
            cam.status = CameraStatus.ONLINE
            if resolution:
                cam.resolution = resolution
            if fps:
                cam.fps = fps

    def remove(self, camera_id: str) -> bool:
        if camera_id in self._cameras:
            del self._cameras[camera_id]
            return True
        return False

    def list_all(self) -> list[dict]:
        return [cam.to_dict() for cam in self._cameras.values()]

    def list_by_type(self, camera_type: CameraType) -> list[Camera]:
        return [c for c in self._cameras.values() if c.camera_type == camera_type]

    def count(self) -> int:
        return len(self._cameras)
