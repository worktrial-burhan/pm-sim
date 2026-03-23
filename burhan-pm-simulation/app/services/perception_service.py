from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.visibility import actor_can_view
from app.services import delivery_service, state_store
from app.services.db import ActorRecord, ActorStateRecord, EventRecord, SimulationRunRecord

ObservationRenderer = Callable[[str, dict[str, Any], str], tuple[str, str]]
_OBSERVATION_RENDERERS: dict[str, ObservationRenderer] = {}


def _observation_renderer(*event_types: str) -> Callable[[ObservationRenderer], ObservationRenderer]:
    def decorator(fn: ObservationRenderer) -> ObservationRenderer:
        for event_type in event_types:
            _OBSERVATION_RENDERERS[event_type] = fn
        return fn

    return decorator


def list_actor_commitments(
    session: Session,
    *,
    run_id: str,
    actor_id: str,
    actor_directory: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    actor_directory = actor_directory or {}
    records = state_store.list_actor_world_objects(
        session,
        run_id=run_id,
        owner_actor_id=actor_id,
        kind="obligation",
    )
    results: list[dict[str, Any]] = []
    for record in records:
        state = record.state_json or {}
        status = str(state.get("status") or "").strip().lower()
        if status in {"done", "cancelled"}:
            continue
        results.append(_serialize_commitment(record, actor_directory))
    results.sort(key=_commitment_sort_key)
    return results


def serialize_commitment_record(
    record,
    *,
    actor_directory: dict[str, str] | None = None,
) -> dict[str, Any]:
    return _serialize_commitment(record, actor_directory or {})


def list_actor_observations(
    session: Session,
    *,
    run: SimulationRunRecord,
    actor: ActorRecord,
    actor_state: ActorStateRecord,
    actor_directory: dict[str, str] | None = None,
    limit: int = 12,
) -> list[dict[str, Any]]:
    actor_directory = actor_directory or {}
    observations: list[dict[str, Any]] = []
    current_sim_time = _aware(run.current_sim_time)
    since_sim_time = _last_reviewed_sim_time(run, actor_state)

    inbox = delivery_service.list_inbox(
        session,
        run_id=run.id,
        actor_id=actor.id,
        current_sim_time=current_sim_time,
        limit=25,
    )
    observed_delivery_ids: list[str] = []
    for item in inbox:
        if item.get("status") != "unread":
            continue
        observations.append(_delivery_to_observation(item))
        observed_delivery_ids.append(item["delivery_id"])
    if observed_delivery_ids:
        delivery_service.mark_deliveries_read(
            session,
            run_id=run.id,
            actor_id=actor.id,
            delivery_ids=observed_delivery_ids,
            read_at_sim=current_sim_time,
        )

    stmt = (
        select(EventRecord)
        .where(
            EventRecord.run_id == run.id,
            EventRecord.sim_time > since_sim_time,
            EventRecord.sim_time <= current_sim_time,
        )
        .order_by(EventRecord.seq.desc())
        .limit(200)
    )
    for event in session.scalars(stmt):
        if event.actor_id == actor.id:
            continue
        if event.event_type not in _OBSERVATION_RENDERERS:
            continue
        if not actor_can_view(
            actor_id=actor.id,
            actor_role=actor.role,
            actor_team=actor.team,
            visibility=event.visibility_json,
        ):
            continue
        observation = _event_to_observation(
            session=session,
            run_id=run.id,
            event=event,
            actor_directory=actor_directory,
        )
        if observation is not None:
            observations.append(observation)

    observations.sort(key=lambda item: _aware_datetime_string(item.get("at")), reverse=True)
    return observations[:limit]


def visible_work_objects(
    session: Session,
    *,
    run_id: str,
    actor_id: str,
    actor_role: str | None,
    actor_team: str | None,
) -> list:
    records = state_store.list_visible_world_objects(
        session,
        run_id=run_id,
        actor_id=actor_id,
        actor_role=actor_role,
        actor_team=actor_team,
    )
    return [record for record in records if record.kind != "obligation"]


def _serialize_commitment(record, actor_directory: dict[str, str]) -> dict[str, Any]:
    state = record.state_json or {}
    category = str(state.get("category") or "").strip().lower()
    related_actor_id = state.get("related_actor_id")
    related_name = actor_directory.get(related_actor_id, related_actor_id) if related_actor_id else None
    summary = str(state.get("summary") or record.title or "").strip()
    title = _commitment_title(record.title, summary=summary, category=category, related_name=related_name)
    return {
        "id": record.id,
        "title": title,
        "summary": summary,
        "due_at": state.get("due_at"),
        "status": state.get("status") or "queued",
        "kind": category or "follow_up",
        "related_person": related_name,
        "related_actor_id": related_actor_id,
    }


def _commitment_title(
    raw_title: str,
    *,
    summary: str,
    category: str,
    related_name: str | None,
) -> str:
    if category == "reply":
        if related_name:
            return f"Reply to {related_name}"
        return "Reply to a message"
    if category == "meeting":
        if raw_title.startswith("Attend: "):
            return raw_title
        return f"Attend: {raw_title}"
    if category == "reminder":
        if raw_title.startswith("Reminder: "):
            return raw_title
        return f"Reminder: {summary or raw_title}"
    return raw_title or summary or "Follow up"


def _commitment_sort_key(item: dict[str, Any]) -> tuple[datetime, str]:
    due_at = _aware_datetime_string(item.get("due_at"))
    return (due_at, str(item.get("title") or ""))


def _delivery_to_observation(item: dict[str, Any]) -> dict[str, Any]:
    event = item.get("event") or {}
    sender = event.get("sender_name") or event.get("sender_actor_id") or "Someone"
    surface = str(item.get("surface") or "work")
    if surface == "chat":
        headline = f"{sender} sent you a chat."
    elif surface == "email":
        headline = f"{sender} emailed you."
    elif surface == "calendar":
        headline = f"Calendar update: {item.get('summary')}"
    else:
        headline = f"New {surface} activity."
    return {
        "kind": "inbound_message" if surface in {"chat", "email"} else "calendar_update",
        "surface": surface,
        "at": item.get("delivered_at_sim"),
        "headline": headline,
        "summary": item.get("summary"),
        "from": sender,
        "conversation": event.get("thread_id"),
        "delivery_id": item.get("delivery_id"),
        "status": item.get("status"),
    }


def _event_to_observation(
    *,
    session: Session,
    run_id: str,
    event: EventRecord,
    actor_directory: dict[str, str],
) -> dict[str, Any] | None:
    actor_name = actor_directory.get(event.actor_id, event.actor_id or "Someone")
    obj = state_store.get_world_object(session, run_id, event.object_id) if event.object_id else None
    obj_title = obj.title if obj is not None else (event.data_json or {}).get("title") or "work item"
    data = event.data_json or {}

    if event.event_type == "TaskAssigneeUpdated":
        assignee_actor_id = data.get("assignee_actor_id")
        data = dict(data)
        data["assignee_name"] = actor_directory.get(assignee_actor_id, assignee_actor_id or "someone")

    renderer = _OBSERVATION_RENDERERS.get(event.event_type)
    if renderer is None:
        return None
    kind, headline = renderer(actor_name, data, obj_title)

    return {
        "kind": kind,
        "surface": "work",
        "at": event.sim_time.isoformat(),
        "headline": headline,
        "summary": str(data.get("body") or data.get("note") or data.get("message") or "").strip(),
        "from": actor_name,
        "object_title": obj_title,
        "event_type": event.event_type,
        "object_id": event.object_id,
    }


@_observation_renderer("TaskCreated")
def _render_task_created(actor_name: str, data: dict[str, Any], obj_title: str) -> tuple[str, str]:
    return ("work_change", f"{actor_name} created task '{obj_title}'.")


@_observation_renderer("TaskStatusUpdated")
def _render_task_status(actor_name: str, data: dict[str, Any], obj_title: str) -> tuple[str, str]:
    return ("work_change", f"{actor_name} changed '{obj_title}' to {data.get('status')}.")


@_observation_renderer("TaskAssigneeUpdated")
def _render_task_assignee(actor_name: str, data: dict[str, Any], obj_title: str) -> tuple[str, str]:
    return ("work_change", f"{actor_name} reassigned '{obj_title}' to {data.get('assignee_name')}.")


@_observation_renderer("DocumentCreated")
def _render_document_created(actor_name: str, data: dict[str, Any], obj_title: str) -> tuple[str, str]:
    return ("work_change", f"{actor_name} created document '{obj_title}'.")


@_observation_renderer("DocumentUpdated")
def _render_document_updated(actor_name: str, data: dict[str, Any], obj_title: str) -> tuple[str, str]:
    return ("work_change", f"{actor_name} updated document '{obj_title}'.")


@_observation_renderer("ProjectPriorityUpdated")
def _render_project_priority(actor_name: str, data: dict[str, Any], obj_title: str) -> tuple[str, str]:
    return ("work_change", f"{actor_name} changed project priority on '{obj_title}' to {data.get('priority')}.")


@_observation_renderer("MeetingScheduled")
def _render_meeting_scheduled(actor_name: str, data: dict[str, Any], obj_title: str) -> tuple[str, str]:
    return ("meeting_update", f"{actor_name} scheduled meeting '{obj_title}'.")


@_observation_renderer("MeetingStarted")
def _render_meeting_started(actor_name: str, data: dict[str, Any], obj_title: str) -> tuple[str, str]:
    return ("meeting_update", f"Meeting started: '{obj_title}'.")


@_observation_renderer("MeetingEnded")
def _render_meeting_ended(actor_name: str, data: dict[str, Any], obj_title: str) -> tuple[str, str]:
    return ("meeting_update", f"Meeting ended: '{obj_title}'.")


@_observation_renderer("MeetingSpoken")
def _render_meeting_spoken(actor_name: str, data: dict[str, Any], obj_title: str) -> tuple[str, str]:
    return ("meeting_update", f"In '{obj_title}', {actor_name} said something.")


@_observation_renderer("MeetingNoteRecorded")
def _render_meeting_note(actor_name: str, data: dict[str, Any], obj_title: str) -> tuple[str, str]:
    return ("meeting_update", f"{actor_name} added notes to '{obj_title}'.")


@_observation_renderer("LaunchRiskEscalated")
def _render_launch_risk(actor_name: str, data: dict[str, Any], obj_title: str) -> tuple[str, str]:
    return ("risk_signal", str(data.get("reason") or "Launch risk escalated."))


def _last_reviewed_sim_time(run: SimulationRunRecord, actor_state: ActorStateRecord) -> datetime:
    if actor_state.last_decision_completed_at_sim is not None:
        return _aware(actor_state.last_decision_completed_at_sim)
    if run.started_at is not None:
        return _aware(run.started_at)
    return _aware(run.current_sim_time)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _aware_datetime_string(value: str | None) -> datetime:
    if not value:
        return datetime.max.replace(tzinfo=UTC)
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed
