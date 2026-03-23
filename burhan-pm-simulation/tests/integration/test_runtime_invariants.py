from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.domain.commands import IntentCommand
from app.domain.engine import CommandRejected, apply_command
from app.domain.models import ActorStatus, ActorTurnStatus, ObjectKind, TriggerStatus, TriggerType, new_id
from app.services.db import (
    ActorRecord,
    ActorStateRecord,
    ActorTurnRecord,
    DeliveryRecord,
    TriggerRecord,
    WorldObjectRecord,
)


def _configure_test_db(tmp_path: Path, monkeypatch):
    database_path = tmp_path / "pm_sim_invariants.db"
    monkeypatch.setenv("PM_SIM_DATABASE_URL", f"sqlite+pysqlite:///{database_path}")
    monkeypatch.setenv("PM_SIM_ENABLE_TEMPORAL", "false")

    from app.services.config import get_settings

    get_settings.cache_clear()
    settings = get_settings()

    from app.services.db import reset_db

    reset_db(settings.database_url)
    return settings


def _create_and_start_run():
    from app.services import run_service
    from app.services.db import session_scope

    with session_scope() as session:
        run = run_service.create_run(
            session,
            scenario_id="smoke_test",
            controller_profile="scripted_demo",
        )
        run_id = run.id

    with session_scope() as session:
        run_service.start_run(session, run_id=run_id)

    return run_id


def _make_toolkit(run_id, actor_id):
    from app.agents.toolkit import ControllerToolkit
    from app.services import state_store
    from app.services.db import session_scope

    with session_scope() as session:
        run = state_store.get_run(session, run_id)
        actor = state_store.get_actor(session, run_id, actor_id)
        assert run is not None
        assert actor is not None

    return ControllerToolkit(
        run_id=run_id,
        actor=actor,
        current_sim_time=run.current_sim_time,
    )


def test_same_scenario_can_be_created_twice(tmp_path, monkeypatch):
    _configure_test_db(tmp_path, monkeypatch)

    from app.services import run_service, state_store
    from app.services.db import session_scope

    with session_scope() as session:
        run_one = run_service.create_run(
            session,
            scenario_id="smoke_test",
            controller_profile="scripted_demo",
        )
        run_two = run_service.create_run(
            session,
            scenario_id="smoke_test",
            controller_profile="scripted_demo",
        )

        assert run_one.id != run_two.id
        assert len(state_store.list_actors(session, run_one.id)) == 3
        assert len(state_store.list_actors(session, run_two.id)) == 3


def test_inbox_reads_stay_unread_until_explicit_ack(tmp_path, monkeypatch):
    _configure_test_db(tmp_path, monkeypatch)

    from app.services import delivery_service, state_store
    from app.services.db import session_scope
    from app.services.tick_loop import TickLoop

    run_id = _create_and_start_run()

    loop = TickLoop()
    loop.run_once()

    toolkit = _make_toolkit(run_id, "actor_sam")
    inbox = toolkit.get_my_inbox(limit=20)
    assert len(inbox) == 1
    assert inbox[0]["status"] == "unread"

    with session_scope() as session:
        run = state_store.get_run(session, run_id)
        unread_actor_ids = delivery_service.actor_ids_with_unread_deliveries(
            session, run_id=run_id, current_sim_time=run.current_sim_time,
        )
        assert "actor_sam" in unread_actor_ids

    # mark_inbox_items_read now executes directly
    result = toolkit.mark_inbox_items_read([inbox[0]["delivery_id"]])
    assert result.get("ok") is True

    with session_scope() as session:
        run = state_store.get_run(session, run_id)
        unread_actor_ids = delivery_service.actor_ids_with_unread_deliveries(
            session, run_id=run_id, current_sim_time=run.current_sim_time,
        )
        assert "actor_sam" not in unread_actor_ids


def test_actor_cannot_mark_another_actors_delivery_read(tmp_path, monkeypatch):
    _configure_test_db(tmp_path, monkeypatch)

    from app.services import delivery_service, state_store
    from app.services.db import session_scope
    from app.services.tick_loop import TickLoop

    run_id = _create_and_start_run()

    loop = TickLoop()
    loop.run_once()

    eng_toolkit = _make_toolkit(run_id, "actor_sam")
    eng_inbox = eng_toolkit.get_my_inbox(limit=20)
    assert len(eng_inbox) == 1
    target_delivery_id = eng_inbox[0]["delivery_id"]

    # PM tries to mark eng's delivery read — should not affect eng's inbox
    pm_toolkit = _make_toolkit(run_id, "actor_pm")
    pm_toolkit.mark_inbox_items_read([target_delivery_id])

    with session_scope() as session:
        run = state_store.get_run(session, run_id)
        eng_inbox_after = delivery_service.list_inbox(
            session,
            run_id=run_id,
            actor_id="actor_sam",
            current_sim_time=run.current_sim_time,
            limit=20,
        )
        assert eng_inbox_after[0]["status"] == "unread"


def test_toolkit_resolves_human_references_for_tasks_and_email_threads(tmp_path, monkeypatch):
    _configure_test_db(tmp_path, monkeypatch)

    from app.services import state_store
    from app.services.db import session_scope

    run_id = _create_and_start_run()

    with session_scope() as session:
        state_store.create_world_object(
            session,
            WorldObjectRecord(
                id="thread_email_resolution",
                run_id=run_id,
                kind=ObjectKind.THREAD.value,
                title="Landing page analytics spec — need clarification on event names",
                owner_actor_id="actor_pm",
                visibility_json={"scope": "actors", "actor_ids": ["actor_pm", "actor_sam"]},
                state_json={
                    "surface": "email",
                    "subject": "Landing page analytics spec — need clarification on event names",
                    "participant_actor_ids": ["actor_pm", "actor_sam"],
                    "message_count": 1,
                },
            ),
        )

    toolkit = _make_toolkit(run_id, "actor_pm")

    task_result = toolkit.update_task_status(
        task="Clarify analytics events for new landing page",
        status="in_progress",
        blocker_reason="",
    )
    assert task_result.get("ok") is True

    email_result = toolkit.reply_email_thread(
        conversation="Landing page analytics spec — need clarification on event names",
        message="Acknowledged.",
    )
    assert email_result.get("ok") is True


@pytest.mark.skip(reason="state_patch triggers removed with old scenarios — smoke_test has no deadline trigger")
def test_deadline_state_patch_with_custom_event_type_does_not_crash(tmp_path, monkeypatch):
    pass


def test_nonparticipant_cannot_post_into_thread(tmp_path, monkeypatch):
    _configure_test_db(tmp_path, monkeypatch)

    from app.services import state_store
    from app.services.db import session_scope
    from app.services.tick_loop import TickLoop

    run_id = _create_and_start_run()

    loop = TickLoop()
    loop.run_once()

    with session_scope() as session:
        state_store.create_actor(
            session,
            ActorRecord(
                id="actor_outsider",
                run_id=run_id,
                name="Pat Outsider",
                role="designer",
                team="design",
                controller_type="scripted",
                timezone="America/Los_Angeles",
                permissions_json={"can_chat": True},
                working_hours_json={},
                profile_json={},
            ),
        )
        state_store.create_actor_state(
            session,
            ActorStateRecord(
                actor_id="actor_outsider",
                run_id=run_id,
                status=ActorStatus.ACTIVE.value,
                goals_json={},
                beliefs_json={},
                relationships_json={},
                commitments_json={},
                workload_json={},
                focus_state_json={},
            ),
        )

        run = state_store.get_run(session, run_id)
        outsider = state_store.get_actor(session, run_id, "actor_outsider")
        thread = next(iter(state_store.list_world_objects(session, run_id, kind="thread")), None)
        assert run is not None
        assert outsider is not None
        assert thread is not None

        with pytest.raises(CommandRejected):
            apply_command(
                session,
                run=run,
                actor=outsider,
                command=IntentCommand(
                    command_type="communicate.reply_thread",
                    actor_id=outsider.id,
                    issued_at_sim=run.current_sim_time,
                    target_ref={"thread_id": thread.id},
                    payload={"body": "I should not be here."},
                ),
            )

        with pytest.raises(CommandRejected):
            apply_command(
                session,
                run=run,
                actor=outsider,
                command=IntentCommand(
                    command_type="communicate.send_chat",
                    actor_id=outsider.id,
                    issued_at_sim=run.current_sim_time,
                    target_ref={"recipient_actor_id": "actor_pm", "thread_id": thread.id},
                    payload={"body": "Intruding into a private thread."},
                ),
            )


def test_run_api_returns_404_and_409_for_client_errors(tmp_path, monkeypatch):
    _configure_test_db(tmp_path, monkeypatch)

    from app.api.main import create_app

    app = create_app()

    with TestClient(app) as client:
        missing_scenario = client.post("/api/runs", json={"scenario_id": "does_not_exist"})
        assert missing_scenario.status_code == 404

        create_response = client.post(
            "/api/runs",
            json={"scenario_id": "smoke_test", "controller_profile": "scripted_demo"},
        )
        run_id = create_response.json()["id"]

        first_start = client.post(f"/api/runs/{run_id}/start")
        assert first_start.status_code == 200

        second_start = client.post(f"/api/runs/{run_id}/start")
        assert second_start.status_code == 409

        pause_response = client.post(f"/api/runs/{run_id}/pause")
        assert pause_response.status_code == 200

        resume_response = client.post(f"/api/runs/{run_id}/resume")
        assert resume_response.status_code == 200

        stop_response = client.post(f"/api/runs/{run_id}/stop")
        assert stop_response.status_code == 200

        restart_response = client.post(f"/api/runs/{run_id}/start")
        assert restart_response.status_code == 409


def test_run_time_scale_can_be_updated_via_api(tmp_path, monkeypatch):
    _configure_test_db(tmp_path, monkeypatch)

    from app.api.main import create_app
    from app.services import simulation_service

    app = create_app()

    with TestClient(app) as client:
        create_response = client.post(
            "/api/runs",
            json={"scenario_id": "smoke_test", "controller_profile": "scripted_demo"},
        )
        run_id = create_response.json()["id"]

        update_response = client.post(f"/api/runs/{run_id}/time-scale", json={"multiplier": 100})
        assert update_response.status_code == 200
        payload = update_response.json()
        assert payload["time_scale_multiplier"] == 100
        assert payload["effective_tick_wall_seconds"] == 0.01

    runtime_state = simulation_service.get_run_runtime_state(run_id)
    assert runtime_state is not None
    assert runtime_state["time_scale_multiplier"] == 100
    assert runtime_state["tick_wall_seconds"] == 0.01


def test_paused_runs_do_not_advance_time_or_fire_triggers(tmp_path, monkeypatch):
    _configure_test_db(tmp_path, monkeypatch)

    from app.services import run_service, simulation_service, state_store
    from app.services.db import session_scope

    with session_scope() as session:
        run = run_service.create_run(
            session,
            scenario_id="smoke_test",
            controller_profile="scripted_demo",
        )
        run_id = run.id
        original_time = run.current_sim_time

    turn_ids = simulation_service.process_run_tick(run_id)
    with session_scope() as session:
        turns = [state_store.get_actor_turn(session, turn_id) for turn_id in turn_ids]
        eng_turns = [turn for turn in turns if turn is not None and turn.actor_id == "actor_sam"]
        assert eng_turns == []

    with session_scope() as session:
        run = state_store.get_run(session, run_id)
        trigger = next(iter(state_store.list_pending_triggers(session, run_id)), None)
        assert run is not None
        assert run.current_sim_time.isoformat() == original_time.replace(tzinfo=None).isoformat()
        assert trigger is not None
        assert trigger.status == TriggerStatus.PENDING.value


def test_replaying_same_turn_does_not_duplicate_side_effects(tmp_path, monkeypatch):
    _configure_test_db(tmp_path, monkeypatch)

    from app.services import event_store, simulation_service, state_store
    from app.services.db import session_scope

    run_id = _create_and_start_run()

    turn_ids = simulation_service.process_run_tick(run_id)
    assert len(turn_ids) == 1
    turn_id = turn_ids[0]

    simulation_service.run_actor_turn(turn_id)

    with session_scope() as session:
        events = event_store.list_events(session, run_id=run_id, limit=200)
        count_after_first = len(events)

    # Second call should be idempotent — no new events
    simulation_service.run_actor_turn(turn_id)

    with session_scope() as session:
        events = event_store.list_events(session, run_id=run_id, limit=200)
        count_after_second = len(events)
        turn = state_store.get_actor_turn(session, turn_id)
        assert count_after_second == count_after_first, (
            f"replay added {count_after_second - count_after_first} extra events"
        )
        assert turn is not None
        assert turn.status == ActorTurnStatus.APPLIED.value


def test_replaying_same_tick_token_does_not_advance_twice(tmp_path, monkeypatch):
    _configure_test_db(tmp_path, monkeypatch)

    from app.services import simulation_service, state_store
    from app.services.db import session_scope

    run_id = _create_and_start_run()

    first_turn_ids = simulation_service.process_run_tick(run_id, tick_token="tick-1")

    with session_scope() as session:
        run_after_first = state_store.get_run(session, run_id)
        assert run_after_first is not None
        first_time = run_after_first.current_sim_time

    second_turn_ids = simulation_service.process_run_tick(run_id, tick_token="tick-1")

    with session_scope() as session:
        run_after_second = state_store.get_run(session, run_id)
        assert run_after_second is not None
        assert second_turn_ids == first_turn_ids
        assert run_after_second.current_sim_time == first_time


def test_temporal_start_failure_leaves_run_truthful(tmp_path, monkeypatch):
    _configure_test_db(tmp_path, monkeypatch)
    monkeypatch.setenv("PM_SIM_ENABLE_TEMPORAL", "true")

    from app.api.main import create_app
    from app.services.db import session_scope
    from app.services.config import get_settings
    from app.services import state_store

    get_settings.cache_clear()
    app = create_app()

    def _boom(_run_id: str):
        raise RuntimeError("temporal unavailable")

    monkeypatch.setattr("app.services.orchestration_service.ensure_run_workflow_sync", _boom)

    with TestClient(app) as client:
        create_response = client.post(
            "/api/runs",
            json={"scenario_id": "smoke_test", "controller_profile": "scripted_demo"},
        )
        run_id = create_response.json()["id"]

        start_response = client.post(f"/api/runs/{run_id}/start")
        assert start_response.status_code == 503

    with session_scope() as session:
        run = state_store.get_run(session, run_id)
        assert run is not None
        assert run.status == "paused"
        assert run.orchestration_status == "error"
        assert "temporal unavailable" in (run.orchestration_error or "")


def test_pm_can_finish_without_completing_checks(tmp_path, monkeypatch):
    """PM can finish the assignment at any time — mechanical checks no longer block."""
    _configure_test_db(tmp_path, monkeypatch)

    run_id = _create_and_start_run()

    toolkit = _make_toolkit(run_id, "actor_pm")
    result = toolkit.finish_assignment(
        summary="Finishing early — PM decides when done.",
        remaining_risks=[],
        confidence="high",
    )
    # Should succeed — no mechanical blocking
    assert not result.get("error"), f"Unexpected error: {result.get('error')}"


def test_pm_can_finish_assignment_and_complete_run(tmp_path, monkeypatch):
    _configure_test_db(tmp_path, monkeypatch)

    from app.services import event_store, state_store
    from app.services.db import session_scope

    run_id = _create_and_start_run()

    # Satisfy the visible completion checks:
    # 1) blocker_recorded — update task or document
    # 2) stakeholder_update_sent — chat/email to revenue lead
    # 3) decision_path_set — schedule meeting or create/update task
    # 4) engineering_input_received — NPC sends message to PM
    toolkit = _make_toolkit(run_id, "actor_pm")
    toolkit.update_task_status(task="obj_task_implementation", status="in_progress", blocker_reason="")
    toolkit.send_chat(coworker="Sam", message="Mockups reviewed, you are good to start building.")
    toolkit.schedule_meeting(
        title="Landing page handoff sync",
        attendees=["actor_sam", "actor_pat"],
        starts_in_minutes=30,
        duration_minutes=30,
        agenda="Handoff from design to engineering.",
    )

    # 4) Simulate engineering replying to PM
    eng_toolkit = _make_toolkit(run_id, "actor_sam")
    eng_toolkit.send_chat(coworker="Riley", message="Got the mockups and analytics spec. Starting implementation now.")

    # PM must read inbox before finishing (eng reply created an unread delivery)
    inbox = toolkit.get_my_inbox()
    unread_ids = [item["delivery_id"] for item in inbox if not item.get("read")]
    if unread_ids:
        toolkit.mark_inbox_items_read(unread_ids)

    result = toolkit.finish_assignment(
        summary="Mockups reviewed, analytics spec clarified, handoff to Sam is in progress.",
        remaining_risks=["Implementation timeline depends on revision scope."],
        confidence="high",
    )
    assert result.get("ok") is True

    with session_scope() as session:
        refreshed_run = state_store.get_run(session, run_id)
        assert refreshed_run is not None
        assert refreshed_run.status == "completed"
        assignment = (refreshed_run.config_json or {}).get("assignment") or {}
        assert assignment["state"] == "finished_by_pm"
        assert assignment["finished_by_actor_id"] == "actor_pm"
        assert assignment["finish_summary"]

        events = event_store.list_events(session, run_id=run_id, limit=50)
        event_types = {event.event_type for event in events}
        assert "AssignmentFinished" in event_types
        assert "RunCompleted" in event_types


def test_review_completion_readiness_returns_snapshot(tmp_path, monkeypatch):
    """Review completion readiness still returns a snapshot, but doesn't block finishing."""
    _configure_test_db(tmp_path, monkeypatch)

    run_id = _create_and_start_run()

    toolkit = _make_toolkit(run_id, "actor_pm")
    readiness = toolkit.review_completion_readiness()

    # Should return a readiness dict (even if not all checks pass)
    assert isinstance(readiness, dict)
    assert "visible_completion_checks" in readiness or "ready_to_finish" in readiness


def test_finish_assignment_succeeds_and_completes_run(tmp_path, monkeypatch):
    """finish_assignment always succeeds now — PM decides when done, no mechanical gates."""
    _configure_test_db(tmp_path, monkeypatch)

    from app.services import state_store
    from app.services.db import session_scope

    run_id = _create_and_start_run()

    with session_scope() as session:
        run = state_store.get_run(session, run_id)
        actor = state_store.get_actor(session, run_id, "actor_pm")
        assert run is not None
        assert actor is not None

        command = IntentCommand(
            command_type="system.finish_assignment",
            actor_id=actor.id,
            issued_at_sim=run.current_sim_time,
            target_ref={"actor_id": actor.id},
            payload={
                "summary": "Finishing the assignment.",
                "remaining_risks": "Migration script complexity could still expand.",
                "confidence": "medium",
            },
        )
        # Should not raise — mechanical blocking was removed
        apply_command(session, run=run, actor=actor, command=command)

        refreshed_run = state_store.get_run(session, run_id)
        assert refreshed_run is not None
        assert refreshed_run.status == "completed"


def test_pm_can_send_chat_to_team_member(tmp_path, monkeypatch):
    _configure_test_db(tmp_path, monkeypatch)

    from app.services.db import session_scope

    run_id = _create_and_start_run()

    toolkit = _make_toolkit(run_id, "actor_pm")
    result = toolkit.send_chat(
        coworker="Sam",
        message="Quick update: reviewed the mockups, analytics spec is page_view, cta_click, scroll_depth.",
    )
    assert result.get("ok") is True


def test_thread_history_and_colleague_directory_tools_work(tmp_path, monkeypatch):
    _configure_test_db(tmp_path, monkeypatch)

    from app.services import state_store
    from app.services.db import session_scope
    from app.services.tick_loop import TickLoop

    run_id = _create_and_start_run()

    loop = TickLoop()
    loop.run_once()

    with session_scope() as session:
        thread = next(iter(state_store.list_world_objects(session, run_id, kind="thread")), None)
        assert thread is not None
        thread_id = thread.id

    toolkit = _make_toolkit(run_id, "actor_sam")
    colleagues = toolkit.list_colleagues()
    thread_messages = toolkit.get_thread_messages(thread_id)

    assert len(colleagues) == 3
    assert any(person["actor_id"] == "actor_pm" for person in colleagues)
    assert len(thread_messages) >= 1


def test_new_thread_titles_use_actor_names(tmp_path, monkeypatch):
    _configure_test_db(tmp_path, monkeypatch)

    from app.services import state_store
    from app.services.db import session_scope

    run_id = _create_and_start_run()

    toolkit = _make_toolkit(run_id, "actor_pm")
    result = toolkit.send_chat(coworker="actor_sam", message="Need a quick update.")
    assert result.get("ok") is True

    with session_scope() as session:
        threads = state_store.list_world_objects(session, run_id, kind="thread")
        assert any(
            "Sam" in thread.title and "actor_sam" not in thread.title
            for thread in threads
        )


def test_create_document_command_creates_world_object(tmp_path, monkeypatch):
    _configure_test_db(tmp_path, monkeypatch)

    from app.services import event_store, state_store
    from app.services.db import session_scope

    run_id = _create_and_start_run()

    toolkit = _make_toolkit(run_id, "actor_pm")
    result = toolkit.create_document(
        title="Launch status note",
        content="Initial PM synthesis of launch risk and next steps.",
    )
    assert result.get("ok") is True

    with session_scope() as session:
        documents = state_store.list_world_objects(session, run_id, kind="document")
        assert any(doc.title == "Launch status note" for doc in documents)
        events = event_store.list_events(session, run_id=run_id, limit=20)
        assert any(event.event_type == "DocumentCreated" for event in events)


def test_create_document_rejects_admin_visibility(tmp_path, monkeypatch):
    _configure_test_db(tmp_path, monkeypatch)

    from app.services import state_store
    from app.services.db import session_scope

    run_id = _create_and_start_run()

    with session_scope() as session:
        run = state_store.get_run(session, run_id)
        actor = state_store.get_actor(session, run_id, "actor_pm")
        assert run is not None
        assert actor is not None

        with pytest.raises(CommandRejected):
            apply_command(
                session,
                run=run,
                actor=actor,
                command=IntentCommand(
                    command_type="documents.create",
                    actor_id=actor.id,
                    issued_at_sim=run.current_sim_time,
                    target_ref={},
                    payload={
                        "title": "Bad visibility",
                        "body": "Should be rejected.",
                        "visibility": {"scope": "admin"},
                    },
                ),
            )


def test_response_delay_trigger_is_scoped_to_its_delivery(tmp_path, monkeypatch):
    _configure_test_db(tmp_path, monkeypatch)

    from app.domain.events import DomainEvent
    from app.domain.models import DeliveryStatus, TriggerStatus, TriggerType, new_id
    from app.services import delivery_service, event_store, simulation_service, state_store
    from app.services.db import TriggerRecord, WorldObjectRecord, session_scope

    run_id = _create_and_start_run()

    with session_scope() as session:
        run = state_store.get_run(session, run_id)
        assert run is not None
        for trigger in state_store.list_pending_triggers(session, run_id):
            state_store.update_trigger_due_time(
                session,
                run_id=run_id,
                trigger_id=trigger.id,
                due_sim_time=run.current_sim_time + timedelta(hours=8),
            )

        first_event = event_store.append_event(
            session,
            run_id=run_id,
            sim_time=run.current_sim_time,
            event=DomainEvent(
                event_type="ChatMessageSent",
                actor_id="actor_pm",
                object_id="thread_test_chat",
                visibility={"scope": "actors", "actor_ids": ["actor_pm", "actor_sam"]},
                data={"body": "First ping", "surface": "chat"},
            ),
        )
        first_delivery = delivery_service.create_delivery(
            session,
            run_id=run_id,
            event_id=first_event.id,
            actor_id="actor_sam",
            surface="chat",
            summary_text="First ping",
            delivered_at_sim=run.current_sim_time,
            metadata={"thread_id": "thread_test_chat"},
        )
        second_event = event_store.append_event(
            session,
            run_id=run_id,
            sim_time=run.current_sim_time,
            event=DomainEvent(
                event_type="EmailSent",
                actor_id="actor_pm",
                object_id="thread_test_email",
                visibility={"scope": "actors", "actor_ids": ["actor_pm", "actor_sam"]},
                data={"body": "Later email", "surface": "email", "subject": "Need eyes"},
            ),
        )
        second_delivery = delivery_service.create_delivery(
            session,
            run_id=run_id,
            event_id=second_event.id,
            actor_id="actor_sam",
            surface="email",
            summary_text="Later email",
            delivered_at_sim=run.current_sim_time,
            metadata={"thread_id": "thread_test_email"},
        )
        first_delivery.status = DeliveryStatus.READ.value
        first_delivery.read_at_sim = run.current_sim_time

        state_store.create_world_object(
            session,
            WorldObjectRecord(
                id="obl_first_reply",
                run_id=run_id,
                kind=ObjectKind.OBLIGATION.value,
                title="Respond on chat",
                owner_actor_id="actor_sam",
                visibility_json={"scope": "private", "owner_actor_id": "actor_sam"},
                state_json={"category": "reply", "status": "queued", "source_delivery_id": first_delivery.id},
            ),
        )
        state_store.create_world_object(
            session,
            WorldObjectRecord(
                id="obl_second_reply",
                run_id=run_id,
                kind=ObjectKind.OBLIGATION.value,
                title="Respond on email",
                owner_actor_id="actor_sam",
                visibility_json={"scope": "private", "owner_actor_id": "actor_sam"},
                state_json={"category": "reply", "status": "queued", "source_delivery_id": second_delivery.id},
            ),
        )

        state_store.create_trigger(
            session,
            TriggerRecord(
                id=new_id("trg"),
                run_id=run_id,
                trigger_type=TriggerType.RESPONSE_DELAY.value,
                due_sim_time=run.current_sim_time + timedelta(minutes=1),
                actor_id="actor_sam",
                object_id="obl_first_reply",
                status=TriggerStatus.PENDING.value,
                priority=6,
                data_json={"delivery_id": first_delivery.id, "surface": "chat"},
            ),
        )
        state_store.create_trigger(
            session,
            TriggerRecord(
                id=new_id("trg"),
                run_id=run_id,
                trigger_type=TriggerType.RESPONSE_DELAY.value,
                due_sim_time=run.current_sim_time + timedelta(minutes=10),
                actor_id="actor_sam",
                object_id="obl_second_reply",
                status=TriggerStatus.PENDING.value,
                priority=6,
                data_json={"delivery_id": second_delivery.id, "surface": "email"},
            ),
        )

    turn_ids = simulation_service.process_run_tick(run_id)
    with session_scope() as session:
        turns = [state_store.get_actor_turn(session, turn_id) for turn_id in turn_ids]
        eng_turns = [turn for turn in turns if turn is not None and turn.actor_id == "actor_sam"]
        assert eng_turns == []


def test_default_run_profile_uses_live_claude_controllers(tmp_path, monkeypatch):
    _configure_test_db(tmp_path, monkeypatch)

    from app.services import run_service, state_store
    from app.services.db import session_scope

    with session_scope() as session:
        run = run_service.create_run(session, scenario_id="smoke_test")
        actors = state_store.list_actors(session, run.id)
        assert actors
        assert all(actor.controller_type == "claude" for actor in actors)


def test_invalid_controller_override_is_rejected_at_run_creation(tmp_path, monkeypatch):
    _configure_test_db(tmp_path, monkeypatch)

    from app.api.main import create_app

    app = create_app()
    with TestClient(app) as client:
        response = client.post(
            "/api/runs",
            json={
                "scenario_id": "smoke_test",
                "controller_overrides": {"actor_pm": "not_a_real_controller"},
            },
        )
        assert response.status_code == 422
        assert "unsupported controller type override" in response.json()["detail"]


def test_unknown_actor_controller_override_is_rejected_at_run_creation(tmp_path, monkeypatch):
    _configure_test_db(tmp_path, monkeypatch)

    from app.api.main import create_app

    app = create_app()
    with TestClient(app) as client:
        response = client.post(
            "/api/runs",
            json={
                "scenario_id": "smoke_test",
                "controller_overrides": {"actor_does_not_exist": "claude"},
            },
        )
        assert response.status_code == 422
        assert "unknown actor" in response.json()["detail"]


def test_work_outside_working_hours_is_rejected(tmp_path, monkeypatch):
    _configure_test_db(tmp_path, monkeypatch)

    from app.services import state_store
    from app.services.db import session_scope

    run_id = _create_and_start_run()
    # 01:10 UTC = 18:10 Pacific, past the 18:00 working-hours end
    late_sim_time = datetime(2026, 3, 24, 1, 10, tzinfo=UTC)

    with session_scope() as session:
        state_store.update_run_time(session, run_id, late_sim_time)

    toolkit = _make_toolkit(run_id, "actor_pm")
    result = toolkit.create_document(
        title="After hours note",
        content="This should not execute because it is after 6 PM local time.",
    )
    assert result.get("error") == "outside_working_hours"
    assert toolkit.executed_commands == []


def test_message_delivery_creates_attention_trigger(tmp_path, monkeypatch):
    """Sending a chat should schedule a RESPONSE_DELAY trigger (no obligation)."""
    _configure_test_db(tmp_path, monkeypatch)

    from app.services import state_store
    from app.services.db import session_scope
    from app.services.tick_loop import TickLoop

    run_id = _create_and_start_run()

    loop = TickLoop()
    loop.run_once()

    with session_scope() as session:
        # No reply obligations should exist — they are no longer auto-created.
        obligations = state_store.list_actor_world_objects(
            session,
            run_id=run_id,
            owner_actor_id="actor_sam",
            kind=ObjectKind.OBLIGATION.value,
        )
        reply_obligations = [
            item
            for item in obligations
            if (item.state_json or {}).get("category") == "reply"
        ]
        assert reply_obligations == []

        # A RESPONSE_DELAY trigger should exist to wake the actor.
        triggers = state_store.list_pending_triggers(
            session, run_id, actor_id="actor_sam"
        )
        response_delay_triggers = [
            t for t in triggers if t.trigger_type == TriggerType.RESPONSE_DELAY.value
        ]
        assert response_delay_triggers


def test_meeting_contributions_land_in_transcript(tmp_path, monkeypatch):
    _configure_test_db(tmp_path, monkeypatch)

    from app.services import event_store, state_store
    from app.services.db import session_scope
    from app.services.tick_loop import TickLoop

    run_id = _create_and_start_run()

    loop = TickLoop()
    # Meeting is scheduled ~30 min out; needs ~45 ticks to start and get speech
    for _ in range(50):
        loop.run_once()

    with session_scope() as session:
        events = event_store.list_events(session, run_id=run_id, limit=200)
        assert any(event.event_type == "MeetingSpoken" for event in events)
        meetings = state_store.list_world_objects(session, run_id, kind="meeting")
        assert meetings
        assert any((meeting.state_json or {}).get("transcript") for meeting in meetings)


def test_update_actor_turn_can_clear_optional_fields(tmp_path, monkeypatch):
    _configure_test_db(tmp_path, monkeypatch)

    from app.services import state_store
    from app.services.db import session_scope

    run_id = _create_and_start_run()
    turn_id = new_id("turn")

    with session_scope() as session:
        run = state_store.get_run(session, run_id)
        assert run is not None
        state_store.create_actor_turn(
            session,
            ActorTurnRecord(
                id=turn_id,
                run_id=run_id,
                actor_id="actor_pm",
                turn_seq=999,
                sim_time=run.current_sim_time,
                cause_type="test",
                cause_ref_json={},
                request_context_json={"foo": "bar"},
                decision_json={"commands": []},
                error_json={"message": "old error"},
                status=ActorTurnStatus.DECIDED.value,
                prepared_at=run.current_sim_time,
                decided_at=run.current_sim_time,
            ),
        )
        state_store.update_actor_turn(
            session,
            turn_id,
            decision_json=None,
            error_json=None,
            decided_at=None,
        )
        turn = state_store.get_actor_turn(session, turn_id)
        assert turn is not None
        assert turn.decision_json is None
        assert turn.error_json is None
        assert turn.decided_at is None


def test_activity_route_surfaces_reasoning_logs(tmp_path, monkeypatch):
    _configure_test_db(tmp_path, monkeypatch)

    from app.api.main import create_app
    from app.services import trace_store
    from app.services.db import session_scope
    from app.services import run_service

    with session_scope() as session:
        run = run_service.create_run(
            session,
            scenario_id="smoke_test",
            controller_profile="scripted_demo",
        )
        trace_store.append_trace(
            session,
            run_id=run.id,
            sim_time=run.current_sim_time,
            actor_id="actor_pm",
            trace_type="introspection_write",
            data={"kind": "claude_step", "reasoning": "Need to review Pat's mockups before answering Sam's spec question."},
        )
        run_id = run.id

    app = create_app()
    with TestClient(app) as client:
        response = client.get(f"/api/runs/{run_id}/activity")
        assert response.status_code == 200
        activity = response.json()
        assert any("Riley thinking:" in entry["summary"] for entry in activity), [e["summary"] for e in activity]
