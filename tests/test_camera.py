"""Tests for Camera Gateway — registry, multi-camera, remote streaming."""

import time
import pytest
from fortress.core.camera_registry import (
    CameraRegistry, Camera, CameraType, CameraStatus,
)


# === Camera Registry Tests ===

def test_registry_register():
    reg = CameraRegistry()
    cam = reg.register("Test Cam", CameraType.LOCAL, "0")
    assert cam.id.startswith("cam_")
    assert cam.name == "Test Cam"
    assert cam.camera_type == CameraType.LOCAL
    assert len(cam.token) > 20


def test_registry_get():
    reg = CameraRegistry()
    cam = reg.register("Cam1", CameraType.RTSP, "rtsp://10.0.0.1/stream")
    found = reg.get(cam.id)
    assert found is not None
    assert found.name == "Cam1"


def test_registry_get_by_token():
    reg = CameraRegistry()
    cam = reg.register("Remote", CameraType.REMOTE, "ws://remote:8080")
    found = reg.get_by_token(cam.token)
    assert found is not None
    assert found.id == cam.id


def test_registry_validate_token_remote():
    """Only REMOTE cameras can validate by token."""
    reg = CameraRegistry()
    cam = reg.register("Remote", CameraType.REMOTE, "")
    assert reg.validate_token(cam.token) is not None


def test_registry_validate_token_local_rejected():
    """LOCAL/RTSP cameras should not validate by token (they don't connect via WS)."""
    reg = CameraRegistry()
    cam = reg.register("Local", CameraType.LOCAL, "0")
    assert reg.validate_token(cam.token) is None


def test_registry_remove():
    reg = CameraRegistry()
    cam = reg.register("ToDelete", CameraType.LOCAL, "0")
    assert reg.remove(cam.id) is True
    assert reg.get(cam.id) is None
    assert reg.remove(cam.id) is False


def test_registry_list_all():
    reg = CameraRegistry()
    reg.register("A", CameraType.LOCAL, "0")
    reg.register("B", CameraType.RTSP, "rtsp://x")
    assert reg.count() == 2
    cameras = reg.list_all()
    assert len(cameras) == 2
    assert cameras[0]["name"] in ("A", "B")


def test_registry_list_by_type():
    reg = CameraRegistry()
    reg.register("Local", CameraType.LOCAL, "0")
    reg.register("RTSP", CameraType.RTSP, "rtsp://x")
    reg.register("Remote", CameraType.REMOTE, "")
    assert len(reg.list_by_type(CameraType.LOCAL)) == 1
    assert len(reg.list_by_type(CameraType.RTSP)) == 1
    assert len(reg.list_by_type(CameraType.REMOTE)) == 1


def test_registry_update_status():
    reg = CameraRegistry()
    cam = reg.register("Test", CameraType.LOCAL, "0")
    reg.update_status(cam.id, CameraStatus.ONLINE, "1920x1080", 30.0)
    found = reg.get(cam.id)
    assert found.status == CameraStatus.ONLINE
    assert found.resolution == "1920x1080"
    assert found.fps == 30.0


def test_registry_to_dict():
    reg = CameraRegistry()
    cam = reg.register("Test", CameraType.REMOTE, "ws://x")
    d = cam.to_dict()
    assert d["name"] == "Test"
    assert d["type"] == "remote"
    assert d["url"] == "***"  # Hidden for remote cameras


# === Camera Plugin Init Tests ===

def test_camera_plugin_init():
    from fortress.plugins.camera import CameraPlugin
    from fortress.core.config import PluginConfig
    plugin = CameraPlugin(PluginConfig())
    assert plugin.name == "camera"
    assert plugin.registry.count() == 0


def test_camera_plugin_default_cameras():
    from fortress.plugins.camera import CameraPlugin
    from fortress.core.config import PluginConfig
    plugin = CameraPlugin(PluginConfig())
    plugin._register_default_cameras()
    # Should have at least 1 local webcam
    assert plugin.registry.count() >= 1
    locals = plugin.registry.list_by_type(CameraType.LOCAL)
    assert len(locals) >= 1


def test_camera_plugin_remote_frame_buffer():
    from fortress.plugins.camera import CameraPlugin
    from fortress.core.config import PluginConfig
    from fortress.core.camera_registry import CameraType
    plugin = CameraPlugin(PluginConfig())
    cam = plugin.registry.register("Remote", CameraType.REMOTE, "")
    # Add frame
    result = plugin.add_remote_frame(cam.id, b"fake_jpeg_data")
    assert result is True
    # Get frame
    frame = plugin.get_frame_for_client(cam.id)
    assert frame == b"fake_jpeg_data"


def test_camera_plugin_unknown_camera_frame():
    from fortress.plugins.camera import CameraPlugin
    from fortress.core.config import PluginConfig
    plugin = CameraPlugin(PluginConfig())
    result = plugin.add_remote_frame("nonexistent", b"data")
    assert result is False
    frame = plugin.get_frame_for_client("nonexistent")
    assert frame is None


# === Camera API Tests ===

@pytest.mark.asyncio
async def test_camera_api_list():
    from httpx import AsyncClient, ASGITransport
    from fortress.web.app import create_dashboard_app
    from fortress.plugins.camera import CameraPlugin
    from fortress.core.config import PluginConfig
    from fortress.web.app import _find_camera_plugin

    plugin = CameraPlugin(PluginConfig())
    _find_camera_plugin._instance = plugin

    app = create_dashboard_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/cameras")
        assert resp.status_code == 200
        data = resp.json()
        assert "cameras" in data

    _find_camera_plugin._instance = None
