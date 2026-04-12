"""Tests for GET /api/v1/system/info endpoint."""


import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_API_KEY", "test-admin-key")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("MEETINGS_OUTPUT_DIR", str(tmp_path))
    # Create a couple of clip directories so the count is testable
    clips_dir = tmp_path / "clips"
    clips_dir.mkdir()
    (clips_dir / "100").mkdir()
    (clips_dir / "200").mkdir()


@pytest.fixture()
def client():
    # Import inside fixture so env vars are set first
    import api.server as srv

    # Ensure start time is set (normally done by lifespan)
    srv._server_start_time = srv.time.time()

    with TestClient(srv.app) as c:
        yield c


def test_system_info_returns_expected_fields(client):
    resp = client.get(
        "/api/v1/system/info",
        headers={"X-API-Key": "test-admin-key"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["version"] == "1.0.0"
    assert isinstance(data["tenant_count"], int)
    assert isinstance(data["total_clips_processed"], int)
    assert isinstance(data["uptime_seconds"], (int, float))
    assert "python_version" in data
    assert data["database_backend"] == "sqlite"


def test_system_info_requires_admin(client):
    # No key
    resp = client.get("/api/v1/system/info")
    assert resp.status_code == 403

    # Wrong key
    resp = client.get(
        "/api/v1/system/info",
        headers={"X-API-Key": "wrong-key"},
    )
    assert resp.status_code == 403


def test_system_info_postgresql_backend(client, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/civiclens")
    resp = client.get(
        "/api/v1/system/info",
        headers={"X-API-Key": "test-admin-key"},
    )
    assert resp.status_code == 200
    assert resp.json()["database_backend"] == "postgresql"
