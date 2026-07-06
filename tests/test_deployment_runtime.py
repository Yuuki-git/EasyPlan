from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from app.models.base import Base


def test_health_endpoint_returns_ok():
    client = TestClient(create_app(enable_static=False))

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "easyplan-backend"}


def test_cors_allows_configured_origin(monkeypatch):
    monkeypatch.setenv("EASYPLAN_CORS_ORIGINS", "https://app.easyplan.example,https://admin.easyplan.example")
    client = TestClient(create_app(enable_static=False))

    response = client.options(
        "/api/intents",
        headers={
            "Origin": "https://app.easyplan.example",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://app.easyplan.example"


def test_static_frontend_mount_serves_index(tmp_path: Path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<main>EasyPlan</main>", encoding="utf-8")
    client = TestClient(create_app(static_dir=dist))

    response = client.get("/")

    assert response.status_code == 200
    assert "EasyPlan" in response.text


def test_api_routes_still_win_over_static_mount(tmp_path: Path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<main>EasyPlan</main>", encoding="utf-8")
    client = TestClient(create_app(static_dir=dist))

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_practice_tables_are_registered():
    assert {
        "practice_loops",
        "practice_loop_revisions",
        "practice_loop_logs",
        "phase_reviews",
    }.issubset(Base.metadata.tables)
