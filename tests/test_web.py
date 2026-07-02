"""Tests for Fortress V2 web dashboard and CLI."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from fortress.web.app import create_dashboard_app
from fortress.core.event_bus import EventBus, Event
from fortress.core.metrics import Metrics


# === Dashboard App Tests ===

def test_dashboard_app_creates():
    app = create_dashboard_app()
    assert app is not None
    assert app.title == "Fortress V2 Dashboard"


def test_dashboard_with_deps():
    bus = EventBus()
    metrics = Metrics()
    db = AsyncMock()
    app = create_dashboard_app(event_bus=bus, db=db, metrics=metrics)
    assert app is not None


@pytest.mark.asyncio
async def test_dashboard_status_endpoint():
    from httpx import AsyncClient, ASGITransport
    bus = EventBus()
    metrics = Metrics()
    db = AsyncMock()
    db.get_stats.return_value = {"events": 5, "decisions": 3, "actions": 2}

    # No auth = open (dev mode)
    app = create_dashboard_app(event_bus=bus, db=db, metrics=metrics)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "metrics" in data
        assert "database" in data


@pytest.mark.asyncio
async def test_dashboard_events_endpoint():
    from httpx import AsyncClient, ASGITransport
    db = AsyncMock()
    db.get_recent_events.return_value = [
        {"id": "1", "type": "test.event", "source": "test", "severity": 0, "timestamp": 1.0}
    ]

    app = create_dashboard_app(db=db)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/events")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["events"]) == 1


@pytest.mark.asyncio
async def test_dashboard_test_event():
    from httpx import AsyncClient, ASGITransport
    bus = EventBus()
    app = create_dashboard_app(event_bus=bus)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/test-event")
        assert resp.status_code == 200


def test_dashboard_index():
    from fastapi.testclient import TestClient
    app = create_dashboard_app()
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Fortress V2" in resp.text


@pytest.mark.asyncio
async def test_dashboard_auth_rejected():
    """With auth configured, requests without token get 401."""
    from httpx import AsyncClient, ASGITransport
    from fortress.core.auth import AuthManager
    auth = AuthManager()
    app = create_dashboard_app(auth=auth)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/status")
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_dashboard_auth_accepted():
    """With auth configured, valid token works."""
    from httpx import AsyncClient, ASGITransport
    from fortress.core.auth import AuthManager
    auth = AuthManager()
    token = auth.master_token
    app = create_dashboard_app(auth=auth)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/status", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200


# === CLI Tests ===

def test_cli_help(capsys):
    from fortress.cli import main
    import sys
    sys.argv = ["fortress", "--help"]
    try:
        main()
    except SystemExit:
        pass
    output = capsys.readouterr().out
    assert "Fortress" in output or "fortress" in output


def test_cli_version(capsys):
    from fortress.cli import main
    import sys
    sys.argv = ["fortress", "--version"]
    try:
        main()
    except SystemExit:
        pass
    output = capsys.readouterr().out
    assert "0.1.0" in output
