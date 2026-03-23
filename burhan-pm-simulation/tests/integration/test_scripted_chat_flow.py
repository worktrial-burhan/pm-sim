from __future__ import annotations

from pathlib import Path


def _configure_test_db(tmp_path: Path, monkeypatch):
    database_path = tmp_path / "pm_sim_test.db"
    monkeypatch.setenv("PM_SIM_DATABASE_URL", f"sqlite+pysqlite:///{database_path}")
    monkeypatch.setenv("PM_SIM_ENABLE_TEMPORAL", "false")

    from app.services.config import get_settings

    get_settings.cache_clear()
    settings = get_settings()

    from app.services.db import reset_db

    reset_db(settings.database_url)
    return settings


def test_scripted_chat_reply_loop(tmp_path, monkeypatch):
    _configure_test_db(tmp_path, monkeypatch)

    from app.services import delivery_service, event_store, run_service, state_store
    from app.services.db import session_scope
    from app.services.tick_loop import TickLoop

    with session_scope() as session:
        run = run_service.create_run(
            session,
            scenario_id="smoke_test",
            controller_profile="scripted_demo",
        )
        run_id = run.id

    with session_scope() as session:
        run_service.start_run(session, run_id=run_id)

    loop = TickLoop()
    for _ in range(20):
        loop.run_once()

    with session_scope() as session:
        run = state_store.get_run(session, run_id)
        assert run is not None

        events = event_store.list_events(session, run_id=run_id, limit=100)
        chat_events = [event for event in events if event.event_type == "ChatMessageSent"]
        assert len(chat_events) >= 2
        assert any(event.event_type == "TaskStatusUpdated" for event in events)
        assert any(event.event_type == "DocumentUpdated" for event in events)
        assert any(event.event_type == "MeetingScheduled" for event in events)
        assert any(event.event_type == "EmailSent" for event in events)

        project = state_store.get_world_object(session, run_id, "obj_project_landing")
        assert project is not None
