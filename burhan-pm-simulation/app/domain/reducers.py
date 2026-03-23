from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.orm import Session

from app.domain.models import ObjectKind
from app.services import state_store
from app.services.db import EventRecord, WorldObjectRecord

_NOOP_EVENT_TYPES = {
    "ActorCreated",
    "AssignmentFinished",
    "ClockAdvanced",
    "InboxItemsRead",
    "ReminderScheduled",
    "RunCompleted",
    "RunCreated",
    "RunPaused",
    "RunResumed",
    "RunStarted",
    "RunStopped",
    "ScenarioTriggerFired",
    "StatePatchSkipped",
    "WorldObjectCreated",
}

Reducer = Callable[[Session, str, EventRecord, dict], None]
_EVENT_REDUCERS: dict[str, Reducer] = {}


def _reducer(*event_types: str) -> Callable[[Reducer], Reducer]:
    def decorator(fn: Reducer) -> Reducer:
        for event_type in event_types:
            _EVENT_REDUCERS[event_type] = fn
        return fn

    return decorator


def apply_event(session: Session, *, run_id: str, event_record: EventRecord) -> None:
    data = event_record.data_json or {}
    handler = _EVENT_REDUCERS.get(event_record.event_type)
    if handler is not None:
        handler(session, run_id, event_record, data)
        return

    if data.get("reducer_hint") == "noop":
        return

    if event_record.event_type in _NOOP_EVENT_TYPES:
        return

    raise ValueError(f"no reducer registered for event type: {event_record.event_type}")


@_reducer("ThreadCreated", "EmailThreadCreated")
def _create_thread(session: Session, run_id: str, event_record: EventRecord, data: dict) -> None:
    if state_store.get_world_object(session, run_id, event_record.object_id) is not None:
        return
    state_store.create_world_object(
        session,
        WorldObjectRecord(
            id=event_record.object_id,
            run_id=run_id,
            kind=ObjectKind.THREAD.value,
            title=data.get("title", "Thread"),
            owner_actor_id=event_record.actor_id,
            visibility_json=event_record.visibility_json,
            state_json={
                "surface": data.get("surface", "chat"),
                "subject": data.get("subject", data.get("title", "Thread")),
                "participant_actor_ids": data.get("participant_actor_ids", []),
                "message_count": 0,
            },
        ),
    )


@_reducer("ChatMessageSent", "EmailSent")
def _thread_message_sent(session: Session, run_id: str, event_record: EventRecord, data: dict) -> None:
    _update_thread_message_state(session, run_id, event_record, data)


@_reducer("TaskCreated")
def _create_task(session: Session, run_id: str, event_record: EventRecord, data: dict) -> None:
    if state_store.get_world_object(session, run_id, event_record.object_id) is not None:
        return
    state_store.create_world_object(
        session,
        WorldObjectRecord(
            id=event_record.object_id,
            run_id=run_id,
            kind=ObjectKind.TASK.value,
            title=data.get("title", "Task"),
            owner_actor_id=data.get("assignee_actor_id"),
            visibility_json=event_record.visibility_json,
            state_json={
                "status": data.get("status", "todo"),
                "assignee_actor_id": data.get("assignee_actor_id"),
                "project_id": data.get("project_id"),
                "description": data.get("description", ""),
                "priority": data.get("priority", "medium"),
                "due_at": data.get("due_at"),
                "blocker_reason": None,
            },
        ),
    )


@_reducer("DocumentCreated")
def _create_document(session: Session, run_id: str, event_record: EventRecord, data: dict) -> None:
    if state_store.get_world_object(session, run_id, event_record.object_id) is not None:
        return
    state_store.create_world_object(
        session,
        WorldObjectRecord(
            id=event_record.object_id,
            run_id=run_id,
            kind=ObjectKind.DOCUMENT.value,
            title=data.get("title", "Document"),
            owner_actor_id=event_record.actor_id,
            visibility_json=event_record.visibility_json,
            state_json={
                "body": data.get("body", ""),
                "last_updated_at": event_record.sim_time.isoformat(),
                "last_updated_by_actor_id": event_record.actor_id,
            },
        ),
    )


@_reducer("ObligationCreated")
def _create_obligation(session: Session, run_id: str, event_record: EventRecord, data: dict) -> None:
    if state_store.get_world_object(session, run_id, event_record.object_id) is not None:
        return
    state_store.create_world_object(
        session,
        WorldObjectRecord(
            id=event_record.object_id,
            run_id=run_id,
            kind=ObjectKind.OBLIGATION.value,
            title=data.get("title", "Obligation"),
            owner_actor_id=data.get("owner_actor_id"),
            visibility_json=event_record.visibility_json,
            state_json=data.get("state", {}),
        ),
    )


@_reducer("TaskStatusUpdated")
def _update_task_status(session: Session, run_id: str, event_record: EventRecord, data: dict) -> None:
    task = state_store.get_world_object(session, run_id, event_record.object_id)
    if task is None:
        return
    next_state = dict(task.state_json or {})
    next_state["status"] = data.get("status")
    if "blocker_reason" in data:
        next_state["blocker_reason"] = data.get("blocker_reason")
    task.state_json = next_state
    session.flush()


@_reducer("TaskAssigneeUpdated")
def _update_task_assignee(session: Session, run_id: str, event_record: EventRecord, data: dict) -> None:
    task = state_store.get_world_object(session, run_id, event_record.object_id)
    if task is None:
        return
    next_state = dict(task.state_json or {})
    next_state["assignee_actor_id"] = data.get("assignee_actor_id")
    task.owner_actor_id = data.get("assignee_actor_id")
    task.state_json = next_state
    session.flush()


@_reducer("ProjectPriorityUpdated")
def _update_project_priority(session: Session, run_id: str, event_record: EventRecord, data: dict) -> None:
    project = state_store.get_world_object(session, run_id, event_record.object_id)
    if project is None:
        return
    next_state = dict(project.state_json or {})
    next_state["priority"] = data.get("priority")
    project.state_json = next_state
    session.flush()


@_reducer("DocumentUpdated")
def _update_document(session: Session, run_id: str, event_record: EventRecord, data: dict) -> None:
    document = state_store.get_world_object(session, run_id, event_record.object_id)
    if document is None:
        return
    next_state = dict(document.state_json or {})
    existing_body = next_state.get("body", "")
    incoming_body = data.get("body", "")
    if data.get("append"):
        next_state["body"] = f"{existing_body}\n{incoming_body}".strip()
    else:
        next_state["body"] = incoming_body
    next_state["last_updated_at"] = event_record.sim_time.isoformat()
    next_state["last_updated_by_actor_id"] = event_record.actor_id
    document.state_json = next_state
    session.flush()


@_reducer("ObligationUpdated", "ObligationCompleted")
def _update_obligation(session: Session, run_id: str, event_record: EventRecord, data: dict) -> None:
    obligation = state_store.get_world_object(session, run_id, event_record.object_id)
    if obligation is None:
        return
    next_state = dict(obligation.state_json or {})
    next_state.update(data.get("state", {}))
    obligation.state_json = next_state
    session.flush()


@_reducer("MeetingScheduled")
def _schedule_meeting(session: Session, run_id: str, event_record: EventRecord, data: dict) -> None:
    if state_store.get_world_object(session, run_id, event_record.object_id) is not None:
        return
    state_store.create_world_object(
        session,
        WorldObjectRecord(
            id=event_record.object_id,
            run_id=run_id,
            kind=ObjectKind.MEETING.value,
            title=data.get("title", "Meeting"),
            owner_actor_id=event_record.actor_id,
            visibility_json=event_record.visibility_json,
            state_json={
                "agenda": data.get("agenda", ""),
                "attendee_actor_ids": data.get("attendee_actor_ids", []),
                "scheduled_start_at": data.get("scheduled_start_at"),
                "scheduled_end_at": data.get("scheduled_end_at"),
                "related_object_id": data.get("related_object_id"),
                "status": "scheduled",
                "notes": [],
                "transcript": [],
                "decisions": [],
            },
        ),
    )


@_reducer("MeetingStarted")
def _meeting_started(session: Session, run_id: str, event_record: EventRecord, data: dict) -> None:
    _update_meeting_status(session, run_id, event_record.object_id, status="in_progress")


@_reducer("MeetingEnded")
def _meeting_ended(session: Session, run_id: str, event_record: EventRecord, data: dict) -> None:
    _update_meeting_status(session, run_id, event_record.object_id, status="completed")


@_reducer("MeetingNoteRecorded")
def _record_meeting_note(session: Session, run_id: str, event_record: EventRecord, data: dict) -> None:
    meeting = state_store.get_world_object(session, run_id, event_record.object_id)
    if meeting is None:
        return
    next_state = dict(meeting.state_json or {})
    notes = list(next_state.get("notes", []))
    notes.append(
        {
            "actor_id": event_record.actor_id,
            "note": data.get("note"),
            "sim_time": event_record.sim_time.isoformat(),
        }
    )
    next_state["notes"] = notes
    meeting.state_json = next_state
    session.flush()


@_reducer("MeetingSpoken")
def _record_meeting_speech(session: Session, run_id: str, event_record: EventRecord, data: dict) -> None:
    meeting = state_store.get_world_object(session, run_id, event_record.object_id)
    if meeting is None:
        return
    next_state = dict(meeting.state_json or {})
    transcript = list(next_state.get("transcript", []))
    transcript.append(
        {
            "actor_id": event_record.actor_id,
            "message": data.get("message"),
            "sim_time": event_record.sim_time.isoformat(),
        }
    )
    next_state["transcript"] = transcript
    meeting.state_json = next_state
    session.flush()


def _update_thread_message_state(
    session: Session, run_id: str, event_record: EventRecord, data: dict
) -> None:
    thread = state_store.get_world_object(session, run_id, event_record.object_id)
    if thread is None:
        return
    next_state = dict(thread.state_json or {})
    next_state["last_message_body"] = data.get("body")
    next_state["last_message_at"] = event_record.sim_time.isoformat()
    next_state["last_sender_actor_id"] = event_record.actor_id
    next_state["message_count"] = int(next_state.get("message_count", 0)) + 1
    if "subject" in data and data.get("subject"):
        next_state["subject"] = data["subject"]
    thread.state_json = next_state
    session.flush()


def _update_meeting_status(session: Session, run_id: str, meeting_id: str | None, *, status: str) -> None:
    meeting = state_store.get_world_object(session, run_id, meeting_id)
    if meeting is None:
        return
    next_state = dict(meeting.state_json or {})
    next_state["status"] = status
    meeting.state_json = next_state
    session.flush()
