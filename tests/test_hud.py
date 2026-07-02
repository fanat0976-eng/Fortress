"""Tests for HUD — OpenCV overlay and Web camera feed."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# === OpenCV HUD Tests ===

def test_hud_init():
    from fortress.hud.opencv_hud import OpenCVHUD
    from fortress.plugins.camera import CameraPlugin
    from fortress.core.config import PluginConfig
    plugin = CameraPlugin(PluginConfig())
    hud = OpenCVHUD(plugin)
    assert hud._running is False
    assert hud._paused is False
    assert hud._window_name == "Fortress HUD"


def test_hud_stop_without_start():
    from fortress.hud.opencv_hud import OpenCVHUD
    from fortress.plugins.camera import CameraPlugin
    from fortress.core.config import PluginConfig
    import asyncio
    plugin = CameraPlugin(PluginConfig())
    hud = OpenCVHUD(plugin)
    asyncio.run(hud.stop())  # Should not crash
    assert hud._running is False


# === Camera Plugin Detection Cache ===

def test_detection_cache_init():
    from fortress.plugins.camera import CameraPlugin
    from fortress.core.config import PluginConfig
    plugin = CameraPlugin(PluginConfig())
    assert hasattr(plugin, '_last_detections')
    assert isinstance(plugin._last_detections, dict)


def test_detection_cache_update():
    from fortress.plugins.camera import CameraPlugin
    from fortress.core.config import PluginConfig
    plugin = CameraPlugin(PluginConfig())
    plugin._last_detections["cam_test"] = [
        {"class_name": "person", "confidence": 0.9, "class_id": 0, "bbox": {"x1": 10, "y1": 10, "x2": 100, "y2": 100}}
    ]
    assert len(plugin._last_detections["cam_test"]) == 1
    assert plugin._last_detections["cam_test"][0]["class_name"] == "person"


# === Web Dashboard Camera Feed ===

def test_dashboard_html_has_cameras():
    from fortress.web.app import DASHBOARD_HTML
    assert "cams" in DASHBOARD_HTML
    assert "loadCams" in DASHBOARD_HTML
    assert "addCam" in DASHBOARD_HTML


def test_dashboard_html_has_token_support():
    from fortress.web.app import DASHBOARD_HTML
    assert "token" in DASHBOARD_HTML
    assert "encodeURIComponent" in DASHBOARD_HTML
