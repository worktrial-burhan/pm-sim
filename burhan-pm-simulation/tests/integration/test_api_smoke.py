from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient


def _configure_test_db(tmp_path: Path, monkeypatch):
    database_path = tmp_path / "pm_sim_api_test.db"
    monkeypatch.setenv("PM_SIM_DATABASE_URL", f"sqlite+pysqlite:///{database_path}")
    monkeypatch.setenv("PM_SIM_ENABLE_TEMPORAL", "false")

    from app.services.config import get_settings

    get_settings.cache_clear()
    settings = get_settings()

    from app.services.db import reset_db

    reset_db(settings.database_url)
    return settings


def test_api_and_ui_smoke(tmp_path, monkeypatch):
    _configure_test_db(tmp_path, monkeypatch)

    from app.api.main import create_app
    from app.services.tick_loop import TickLoop

    app = create_app()

    with TestClient(app) as client:
        home_response = client.get("/")
        assert home_response.status_code == 200
        assert "smoke_test" in home_response.text

        create_response = client.post(
            "/api/runs",
            json={"scenario_id": "smoke_test", "controller_profile": "scripted_demo"},
        )
        assert create_response.status_code == 200
        run_id = create_response.json()["id"]
        assert create_response.json()["assignment"]["title"] == "Coordinate the Landing Page Redesign"

        start_response = client.post(f"/api/runs/{run_id}/start")
        assert start_response.status_code == 200
        assert start_response.json()["status"] == "running"

        loop = TickLoop()
        for _ in range(20):
            loop.run_once()

        run_response = client.get(f"/api/runs/{run_id}")
        assert run_response.status_code == 200
        assert run_response.json()["status"] == "running"
        assert run_response.json()["assignment_state"] == "in_progress"

        events_response = client.get(f"/api/runs/{run_id}/events")
        assert events_response.status_code == 200
        events = events_response.json()
        assert len(events) > 0

        actors_response = client.get(f"/api/runs/{run_id}/actors")
        assert actors_response.status_code == 200
        actors = actors_response.json()
        assert len(actors) == 3

        run_page_response = client.get(f"/ui/runs/{run_id}")
        assert run_page_response.status_code == 200
        assert "window.location.reload()" in run_page_response.text

        actor_detail_response = client.get(f"/api/runs/{run_id}/actors/actor_pm")
        assert actor_detail_response.status_code == 200
        actor_payload = actor_detail_response.json()
        assert actor_payload["assignment"]["title"] == "Coordinate the Landing Page Redesign"
        assert len(actor_payload["traces"]) >= 1
