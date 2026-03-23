from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.scenarios.schemas import RubricCheck
from app.services import delivery_service, perception_service, state_store
from app.services.checks import evaluate_check


def compute_completion_readiness(
    session: Session,
    *,
    run_id: str,
    actor_id: str,
) -> dict[str, Any]:
    run = state_store.get_run(session, run_id)
    actor = state_store.get_actor(session, run_id, actor_id)
    if run is None or actor is None:
        raise ValueError("run or actor not found")

    assignment = dict((run.config_json or {}).get("assignment") or {})
    visible_checks = [
        RubricCheck.model_validate(item) for item in assignment.get("visible_completion_checks") or []
    ]
    check_results: list[dict[str, Any]] = []
    failing_check_labels: list[str] = []
    for check in visible_checks:
        passed, detail = evaluate_check(session, run_id=run_id, check=check)
        check_results.append(
            {
                "id": check.id,
                "label": check.label,
                "passed": passed,
                "detail": detail,
            }
        )
        if not passed:
            failing_check_labels.append(check.label)

    inbox = delivery_service.list_inbox(
        session,
        run_id=run_id,
        actor_id=actor_id,
        current_sim_time=run.current_sim_time,
        limit=50,
    )
    unread_items = [item for item in inbox if item.get("status") == "unread"]

    actor_directory = {
        colleague.id: colleague.name for colleague in state_store.list_actors(session, run_id)
    }
    own_open_commitments = perception_service.list_actor_commitments(
        session,
        run_id=run_id,
        actor_id=actor_id,
        actor_directory=actor_directory,
    )

    visible_objects = state_store.list_visible_world_objects(
        session,
        run_id=run_id,
        actor_id=actor.id,
        actor_role=actor.role,
        actor_team=actor.team,
    )
    active_meetings = [
        _summarize_meeting(record)
        for record in visible_objects
        if record.kind == "meeting" and str((record.state_json or {}).get("status") or "") == "in_progress"
    ]
    at_risk_projects = [
        _summarize_project(record)
        for record in visible_objects
        if record.kind == "project"
        and (
            str((record.state_json or {}).get("status") or "") in {"at_risk", "blocked"}
            or str((record.state_json or {}).get("launch_confidence") or "") in {"yellow", "red"}
        )
    ]
    high_priority_open_tasks = [
        _summarize_task(record)
        for record in visible_objects
        if record.kind == "task"
        and str((record.state_json or {}).get("priority") or "") == "high"
        and str((record.state_json or {}).get("status") or "") not in {"done", "cancelled"}
    ]

    blockers: list[str] = []
    blockers.extend(f"Visible completion check still open: {label}" for label in failing_check_labels)
    if unread_items:
        blockers.append(f"You still have {len(unread_items)} unread inbox item(s).")
    if own_open_commitments:
        blockers.append(f"You still have {len(own_open_commitments)} open commitment(s).")
    if active_meetings:
        blockers.append(f"There {'is' if len(active_meetings) == 1 else 'are'} still {len(active_meetings)} active meeting(s).")

    warnings: list[str] = []
    if at_risk_projects:
        warnings.append(
            f"{len(at_risk_projects)} visible project(s) still read as at-risk or yellow."
        )
    if high_priority_open_tasks:
        warnings.append(
            f"{len(high_priority_open_tasks)} high-priority task(s) are still in flight."
        )

    return {
        "ready_to_finish": not blockers,
        "generated_at": run.current_sim_time.isoformat(),
        "visible_completion_checks": check_results,
        "blockers": blockers,
        "warnings": warnings,
        "unread_inbox_items": [_summarize_inbox_item(item) for item in unread_items],
        "open_commitments": own_open_commitments,
        "active_meetings": active_meetings,
        "at_risk_projects": at_risk_projects,
        "high_priority_open_tasks": high_priority_open_tasks,
    }


def _summarize_inbox_item(item: dict[str, Any]) -> dict[str, Any]:
    event = item.get("event") or {}
    return {
        "surface": item.get("surface"),
        "from": event.get("sender_name") or event.get("sender_actor_id"),
        "summary": item.get("summary"),
        "delivered_at_sim": item.get("delivered_at_sim"),
    }

def _summarize_meeting(record) -> dict[str, Any]:
    state = record.state_json or {}
    return {
        "title": record.title,
        "status": state.get("status"),
        "scheduled_end_at": state.get("scheduled_end_at"),
    }


def _summarize_project(record) -> dict[str, Any]:
    state = record.state_json or {}
    return {
        "title": record.title,
        "status": state.get("status"),
        "launch_confidence": state.get("launch_confidence"),
        "priority": state.get("priority"),
    }


def _summarize_task(record) -> dict[str, Any]:
    state = record.state_json or {}
    return {
        "title": record.title,
        "status": state.get("status"),
        "priority": state.get("priority"),
        "blocker_reason": state.get("blocker_reason"),
        "assignee_actor_id": state.get("assignee_actor_id"),
    }
