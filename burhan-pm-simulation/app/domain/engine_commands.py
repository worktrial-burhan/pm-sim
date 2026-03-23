from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

from sqlalchemy.orm import Session

from app.domain.commands import IntentCommand
from app.domain.events import DomainEvent
from app.domain.models import ObjectKind, RunStatus, TriggerStatus, TriggerType, new_id
from app.domain.engine_attention import (
    cancel_pending_obligation_triggers,
    complete_meeting_obligations,
    complete_obligation,
    create_delivery_with_attention,
    create_or_refresh_obligation,
    list_open_actor_obligations,
    require_actor_obligation,
    update_obligation,
)
from app.domain.engine_common import (
    AppliedCommandResult,
    CommandRejected,
    append_and_apply,
    get_thread_and_participants,
    require_object_kind,
    require_permission,
    validate_actor_created_visibility,
)
from app.services import closure_service, state_store
from app.services.config import get_settings
from app.services.db import ActorRecord, SimulationRunRecord, TriggerRecord

CommandHandler = Callable[[Session, SimulationRunRecord, ActorRecord, IntentCommand], AppliedCommandResult]
_COMMAND_HANDLERS: dict[str, CommandHandler] = {}


def _command_handler(*command_types: str) -> Callable[[CommandHandler], CommandHandler]:
    def decorator(fn: CommandHandler) -> CommandHandler:
        for command_type in command_types:
            _COMMAND_HANDLERS[command_type] = fn
        return fn

    return decorator


def apply_command(
    session: Session,
    *,
    run: SimulationRunRecord,
    actor: ActorRecord,
    command: IntentCommand,
) -> AppliedCommandResult:
    handler = _COMMAND_HANDLERS.get(command.command_type)
    if handler is None:
        raise CommandRejected(f"unsupported command type: {command.command_type}")
    return handler(session, run, actor, command)


@_command_handler("inbox.mark_read")
def apply_mark_read(
    session: Session,
    run: SimulationRunRecord,
    actor: ActorRecord,
    command: IntentCommand,
) -> AppliedCommandResult:
    from app.services import delivery_service

    delivery_ids = list(command.payload.get("delivery_ids") or [])
    if not delivery_ids:
        raise CommandRejected("missing delivery_ids")

    marked_ids = delivery_service.mark_deliveries_read(
        session,
        run_id=run.id,
        actor_id=actor.id,
        delivery_ids=delivery_ids,
        read_at_sim=run.current_sim_time,
    )
    accepted_events = append_and_apply(
        session,
        run_id=run.id,
        sim_time=run.current_sim_time,
        events=[
            DomainEvent(
                event_type="InboxItemsRead",
                actor_id=actor.id,
                visibility={"scope": "actors", "actor_ids": [actor.id]},
                data={"requested_delivery_ids": delivery_ids, "marked_delivery_ids": marked_ids},
            )
        ],
    )
    return AppliedCommandResult(events=accepted_events)


@_command_handler("communicate.send_chat")
def apply_send_chat(
    session: Session,
    run: SimulationRunRecord,
    actor: ActorRecord,
    command: IntentCommand,
) -> AppliedCommandResult:
    return _apply_threaded_send(
        session,
        run=run,
        actor=actor,
        command=command,
        surface="chat",
        send_permission_key="can_chat",
        thread_created_event_type="ThreadCreated",
        message_event_type="ChatMessageSent",
    )


@_command_handler("communicate.reply_thread")
def apply_reply_thread(
    session: Session,
    run: SimulationRunRecord,
    actor: ActorRecord,
    command: IntentCommand,
) -> AppliedCommandResult:
    return _apply_threaded_reply(
        session,
        run=run,
        actor=actor,
        command=command,
        reply_permission_key="can_chat",
        message_event_type="ChatMessageSent",
    )


@_command_handler("communicate.send_email")
def apply_send_email(
    session: Session,
    run: SimulationRunRecord,
    actor: ActorRecord,
    command: IntentCommand,
) -> AppliedCommandResult:
    return _apply_threaded_send(
        session,
        run=run,
        actor=actor,
        command=command,
        surface="email",
        send_permission_key="can_email",
        thread_created_event_type="EmailThreadCreated",
        message_event_type="EmailSent",
    )


@_command_handler("communicate.reply_email_thread")
def apply_reply_email_thread(
    session: Session,
    run: SimulationRunRecord,
    actor: ActorRecord,
    command: IntentCommand,
) -> AppliedCommandResult:
    return _apply_threaded_reply(
        session,
        run=run,
        actor=actor,
        command=command,
        reply_permission_key="can_email",
        message_event_type="EmailSent",
    )


def _apply_threaded_send(
    session: Session,
    *,
    run: SimulationRunRecord,
    actor: ActorRecord,
    command: IntentCommand,
    surface: str,
    send_permission_key: str,
    thread_created_event_type: str,
    message_event_type: str,
) -> AppliedCommandResult:
    require_permission(actor, send_permission_key)

    recipient_actor_id = command.target_ref.get("recipient_actor_id")
    thread_id = command.target_ref.get("thread_id")
    body = (command.payload.get("body") or "").strip()
    subject = (command.payload.get("subject") or "").strip()

    if not recipient_actor_id:
        raise CommandRejected("missing recipient_actor_id")
    if recipient_actor_id == actor.id:
        raise CommandRejected(f"cannot send {surface} to self")
    if not body:
        raise CommandRejected(f"{surface} body cannot be empty")
    recipient_actor = state_store.get_actor(session, run.id, recipient_actor_id)
    if recipient_actor is None:
        raise CommandRejected("recipient actor does not exist")

    accepted_domain_events: list[DomainEvent] = []

    if not thread_id:
        thread_id = new_id("thread")
        title = subject or f"{surface.title()}: {actor.name} <> {recipient_actor.name}"
        visibility = {"scope": "actors", "actor_ids": [actor.id, recipient_actor_id]}
        accepted_domain_events.append(
            DomainEvent(
                event_type=thread_created_event_type,
                actor_id=actor.id,
                object_id=thread_id,
                visibility=visibility,
                data={
                    "title": title,
                    "surface": surface,
                    "participant_actor_ids": [actor.id, recipient_actor_id],
                    "subject": subject or title,
                },
            )
        )
    else:
        thread, participants = get_thread_and_participants(
            session,
            run.id,
            thread_id,
            expected_kind=ObjectKind.THREAD.value,
        )
        if actor.id not in participants:
            raise CommandRejected("actor is not a participant in this thread")
        if recipient_actor_id not in participants:
            raise CommandRejected("recipient is not a participant in this thread")
        visibility = thread.visibility_json

    event_data = {"body": body, "recipient_actor_ids": [recipient_actor_id], "surface": surface}
    if subject:
        event_data["subject"] = subject

    accepted_domain_events.append(
        DomainEvent(
            event_type=message_event_type,
            actor_id=actor.id,
            object_id=thread_id,
            visibility=visibility,
            data=event_data,
        )
    )
    accepted_events = append_and_apply(
        session,
        run_id=run.id,
        sim_time=run.current_sim_time,
        events=accepted_domain_events,
    )
    message_event = accepted_events[-1]

    create_delivery_with_attention(
        session,
        run=run,
        actor_id=recipient_actor_id,
        event_id=message_event.id,
        surface=surface,
        summary_text=subject or body,
        delivered_at_sim=run.current_sim_time,
        metadata={"thread_id": thread_id, "sender_actor_id": actor.id, "subject": subject},
    )
    return AppliedCommandResult(events=accepted_events, deliveries_created=1)


def _apply_threaded_reply(
    session: Session,
    *,
    run: SimulationRunRecord,
    actor: ActorRecord,
    command: IntentCommand,
    reply_permission_key: str,
    message_event_type: str,
) -> AppliedCommandResult:
    require_permission(actor, reply_permission_key)
    thread_id = command.target_ref.get("thread_id")
    body = (command.payload.get("body") or "").strip()
    if not thread_id:
        raise CommandRejected("missing thread_id")
    if not body:
        raise CommandRejected("reply body cannot be empty")

    thread, participants = get_thread_and_participants(
        session,
        run.id,
        thread_id,
        expected_kind=ObjectKind.THREAD.value,
    )
    if actor.id not in participants:
        raise CommandRejected("actor is not a participant in this thread")

    recipients = [participant for participant in participants if participant != actor.id]
    if not recipients:
        raise CommandRejected("thread has no recipients")

    accepted_events = append_and_apply(
        session,
        run_id=run.id,
        sim_time=run.current_sim_time,
        events=[
            DomainEvent(
                event_type=message_event_type,
                actor_id=actor.id,
                object_id=thread_id,
                visibility=thread.visibility_json,
                data={
                    "body": body,
                    "recipient_actor_ids": recipients,
                    "surface": (thread.state_json or {}).get("surface"),
                },
            )
        ],
    )

    for recipient_actor_id in recipients:
        create_delivery_with_attention(
            session,
            run=run,
            actor_id=recipient_actor_id,
            event_id=accepted_events[0].id,
            surface=(thread.state_json or {}).get("surface", "chat"),
            summary_text=body,
            delivered_at_sim=run.current_sim_time,
            metadata={"thread_id": thread_id, "sender_actor_id": actor.id},
        )

    return AppliedCommandResult(events=accepted_events, deliveries_created=len(recipients))


@_command_handler("tasks.create")
def apply_create_task(
    session: Session,
    run: SimulationRunRecord,
    actor: ActorRecord,
    command: IntentCommand,
) -> AppliedCommandResult:
    require_permission(actor, "can_edit_tasks")
    title = (command.payload.get("title") or "").strip()
    if not title:
        raise CommandRejected("task title cannot be empty")

    assignee_actor_id = command.payload.get("assignee_actor_id")
    project_id = command.payload.get("project_id")
    if assignee_actor_id and state_store.get_actor(session, run.id, assignee_actor_id) is None:
        raise CommandRejected("assignee actor does not exist")
    if project_id and state_store.get_world_object(session, run.id, project_id) is None:
        raise CommandRejected("project does not exist")

    task_id = new_id("task")
    accepted_events = append_and_apply(
        session,
        run_id=run.id,
        sim_time=run.current_sim_time,
        events=[
            DomainEvent(
                event_type="TaskCreated",
                actor_id=actor.id,
                object_id=task_id,
                visibility={"scope": "company"},
                data={
                    "title": title,
                    "assignee_actor_id": assignee_actor_id,
                    "project_id": project_id,
                    "description": command.payload.get("description", ""),
                    "priority": command.payload.get("priority", "medium"),
                    "due_at": command.payload.get("due_at"),
                    "status": "todo",
                },
            )
        ],
    )
    return AppliedCommandResult(events=accepted_events)


@_command_handler("tasks.update_status")
def apply_update_task_status(
    session: Session,
    run: SimulationRunRecord,
    actor: ActorRecord,
    command: IntentCommand,
) -> AppliedCommandResult:
    require_permission(actor, "can_edit_tasks")
    task_id = command.target_ref.get("task_id")
    status = (command.payload.get("status") or "").strip()
    if not task_id:
        raise CommandRejected("missing task_id")
    if not status:
        raise CommandRejected("missing status")
    task = require_object_kind(session, run.id, task_id, ObjectKind.TASK.value)

    accepted_events = append_and_apply(
        session,
        run_id=run.id,
        sim_time=run.current_sim_time,
        events=[
            DomainEvent(
                event_type="TaskStatusUpdated",
                actor_id=actor.id,
                object_id=task.id,
                visibility=task.visibility_json,
                data={
                    "status": status,
                    "blocker_reason": command.payload.get("blocker_reason"),
                },
            )
        ],
    )
    return AppliedCommandResult(events=accepted_events)


@_command_handler("tasks.update_assignee")
def apply_update_task_assignee(
    session: Session,
    run: SimulationRunRecord,
    actor: ActorRecord,
    command: IntentCommand,
) -> AppliedCommandResult:
    require_permission(actor, "can_edit_tasks")
    task_id = command.target_ref.get("task_id")
    assignee_actor_id = command.payload.get("assignee_actor_id")
    if not task_id:
        raise CommandRejected("missing task_id")
    if not assignee_actor_id:
        raise CommandRejected("missing assignee_actor_id")
    if state_store.get_actor(session, run.id, assignee_actor_id) is None:
        raise CommandRejected("assignee actor does not exist")
    task = require_object_kind(session, run.id, task_id, ObjectKind.TASK.value)

    accepted_events = append_and_apply(
        session,
        run_id=run.id,
        sim_time=run.current_sim_time,
        events=[
            DomainEvent(
                event_type="TaskAssigneeUpdated",
                actor_id=actor.id,
                object_id=task.id,
                visibility=task.visibility_json,
                data={"assignee_actor_id": assignee_actor_id},
            )
        ],
    )
    return AppliedCommandResult(events=accepted_events)


@_command_handler("projects.update_priority")
def apply_update_project_priority(
    session: Session,
    run: SimulationRunRecord,
    actor: ActorRecord,
    command: IntentCommand,
) -> AppliedCommandResult:
    require_permission(actor, "can_manage_projects")
    project_id = command.target_ref.get("project_id")
    priority = command.payload.get("priority")
    if not project_id:
        raise CommandRejected("missing project_id")
    if not priority:
        raise CommandRejected("missing priority")
    project = require_object_kind(session, run.id, project_id, ObjectKind.PROJECT.value)

    accepted_events = append_and_apply(
        session,
        run_id=run.id,
        sim_time=run.current_sim_time,
        events=[
            DomainEvent(
                event_type="ProjectPriorityUpdated",
                actor_id=actor.id,
                object_id=project.id,
                visibility=project.visibility_json,
                data={"priority": priority},
            )
        ],
    )
    return AppliedCommandResult(events=accepted_events)


@_command_handler("documents.create")
def apply_create_document(
    session: Session,
    run: SimulationRunRecord,
    actor: ActorRecord,
    command: IntentCommand,
) -> AppliedCommandResult:
    require_permission(actor, "can_edit_docs")
    title = (command.payload.get("title") or "").strip()
    body = str(command.payload.get("body", ""))
    visibility = validate_actor_created_visibility(
        session,
        run=run,
        actor=actor,
        raw_visibility=command.payload.get("visibility"),
    )
    if not title:
        raise CommandRejected("document title cannot be empty")

    document_id = new_id("doc")
    accepted_events = append_and_apply(
        session,
        run_id=run.id,
        sim_time=run.current_sim_time,
        events=[
            DomainEvent(
                event_type="DocumentCreated",
                actor_id=actor.id,
                object_id=document_id,
                visibility=visibility,
                data={"title": title, "body": body},
            )
        ],
    )
    return AppliedCommandResult(events=accepted_events)


@_command_handler("documents.update")
def apply_update_document(
    session: Session,
    run: SimulationRunRecord,
    actor: ActorRecord,
    command: IntentCommand,
) -> AppliedCommandResult:
    require_permission(actor, "can_edit_docs")
    document_id = command.target_ref.get("document_id")
    body = command.payload.get("body")
    append = bool(command.payload.get("append", False))
    if not document_id:
        raise CommandRejected("missing document_id")
    if body is None:
        raise CommandRejected("missing body")
    document = require_object_kind(session, run.id, document_id, ObjectKind.DOCUMENT.value)

    accepted_events = append_and_apply(
        session,
        run_id=run.id,
        sim_time=run.current_sim_time,
        events=[
            DomainEvent(
                event_type="DocumentUpdated",
                actor_id=actor.id,
                object_id=document.id,
                visibility=document.visibility_json,
                data={"body": body, "append": append},
            )
        ],
    )
    return AppliedCommandResult(events=accepted_events)


@_command_handler("meetings.schedule")
def apply_schedule_meeting(
    session: Session,
    run: SimulationRunRecord,
    actor: ActorRecord,
    command: IntentCommand,
) -> AppliedCommandResult:
    require_permission(actor, "can_schedule_meetings")
    title = (command.payload.get("title") or "").strip()
    attendee_actor_ids = list(command.payload.get("attendee_actor_ids") or [])
    starts_in_minutes = int(command.payload.get("starts_in_minutes") or 0)
    duration_minutes = int(command.payload.get("duration_minutes") or 30)
    agenda = command.payload.get("agenda", "")
    related_object_id = command.payload.get("related_object_id")

    if not title:
        raise CommandRejected("meeting title cannot be empty")
    if starts_in_minutes < 0:
        raise CommandRejected("meeting cannot start in the past")

    attendee_ids = sorted(set([actor.id, *attendee_actor_ids]))
    for attendee_actor_id in attendee_ids:
        if state_store.get_actor(session, run.id, attendee_actor_id) is None:
            raise CommandRejected(f"meeting attendee does not exist: {attendee_actor_id}")

    meeting_id = new_id("meeting")
    starts_at = run.current_sim_time + timedelta(minutes=starts_in_minutes)
    ends_at = starts_at + timedelta(minutes=duration_minutes)
    visibility = {"scope": "actors", "actor_ids": attendee_ids}

    accepted_events = append_and_apply(
        session,
        run_id=run.id,
        sim_time=run.current_sim_time,
        events=[
            DomainEvent(
                event_type="MeetingScheduled",
                actor_id=actor.id,
                object_id=meeting_id,
                visibility=visibility,
                data={
                    "title": title,
                    "agenda": agenda,
                    "attendee_actor_ids": attendee_ids,
                    "scheduled_start_at": starts_at.isoformat(),
                    "scheduled_end_at": ends_at.isoformat(),
                    "related_object_id": related_object_id,
                },
            )
        ],
    )

    state_store.create_trigger(
        session,
        TriggerRecord(
            id=new_id("trg"),
            run_id=run.id,
            trigger_type=TriggerType.MEETING_START.value,
            due_sim_time=starts_at,
            object_id=meeting_id,
            status=TriggerStatus.PENDING.value,
            priority=20,
            data_json={},
        ),
    )
    state_store.create_trigger(
        session,
        TriggerRecord(
            id=new_id("trg"),
            run_id=run.id,
            trigger_type=TriggerType.MEETING_END.value,
            due_sim_time=ends_at,
            object_id=meeting_id,
            status=TriggerStatus.PENDING.value,
            priority=10,
            data_json={},
        ),
    )
    return AppliedCommandResult(events=accepted_events)


@_command_handler("meetings.record_note")
def apply_record_meeting_note(
    session: Session,
    run: SimulationRunRecord,
    actor: ActorRecord,
    command: IntentCommand,
) -> AppliedCommandResult:
    meeting_id = command.target_ref.get("meeting_id")
    note = (command.payload.get("note") or "").strip()
    if not meeting_id:
        raise CommandRejected("missing meeting_id")
    if not note:
        raise CommandRejected("meeting note cannot be empty")
    meeting = require_object_kind(session, run.id, meeting_id, ObjectKind.MEETING.value)
    attendees = list((meeting.state_json or {}).get("attendee_actor_ids", []))
    if actor.id not in attendees:
        raise CommandRejected("actor is not a participant in this meeting")

    accepted_events = append_and_apply(
        session,
        run_id=run.id,
        sim_time=run.current_sim_time,
        events=[
            DomainEvent(
                event_type="MeetingNoteRecorded",
                actor_id=actor.id,
                object_id=meeting.id,
                visibility=meeting.visibility_json,
                data={"note": note},
            )
        ],
    )
    complete_meeting_obligations(
        session,
        run=run,
        actor_id=actor.id,
        meeting_id=meeting.id,
        current_sim_time=run.current_sim_time,
        resolution_note="Contributed meeting notes.",
    )
    return AppliedCommandResult(events=accepted_events)


@_command_handler("meetings.speak")
def apply_speak_in_meeting(
    session: Session,
    run: SimulationRunRecord,
    actor: ActorRecord,
    command: IntentCommand,
) -> AppliedCommandResult:
    meeting_id = command.target_ref.get("meeting_id")
    message = (command.payload.get("message") or "").strip()
    if not meeting_id:
        raise CommandRejected("missing meeting_id")
    if not message:
        raise CommandRejected("meeting message cannot be empty")
    meeting = require_object_kind(session, run.id, meeting_id, ObjectKind.MEETING.value)
    attendees = list((meeting.state_json or {}).get("attendee_actor_ids", []))
    if actor.id not in attendees:
        raise CommandRejected("actor is not a participant in this meeting")
    if str((meeting.state_json or {}).get("status") or "") != "in_progress":
        raise CommandRejected("meeting is not currently in progress")

    accepted_events = append_and_apply(
        session,
        run_id=run.id,
        sim_time=run.current_sim_time,
        events=[
            DomainEvent(
                event_type="MeetingSpoken",
                actor_id=actor.id,
                object_id=meeting.id,
                visibility=meeting.visibility_json,
                data={"message": message},
            )
        ],
    )
    complete_meeting_obligations(
        session,
        run=run,
        actor_id=actor.id,
        meeting_id=meeting.id,
        current_sim_time=run.current_sim_time,
        resolution_note="Spoke in meeting.",
    )
    return AppliedCommandResult(events=accepted_events)


@_command_handler("system.schedule_self_wake")
def apply_schedule_self_wake(
    session: Session,
    run: SimulationRunRecord,
    actor: ActorRecord,
    command: IntentCommand,
) -> AppliedCommandResult:
    minutes_from_now = int(command.payload.get("minutes_from_now") or 0)
    reason = (command.payload.get("reason") or "").strip()
    if minutes_from_now <= 0:
        raise CommandRejected("minutes_from_now must be positive")

    due_time = run.current_sim_time + timedelta(minutes=minutes_from_now)
    pending_self_wakes = list_open_actor_obligations(
        session,
        run_id=run.id,
        actor_id=actor.id,
        category="reminder",
    )
    if len(pending_self_wakes) >= get_settings().max_outstanding_self_wakes:
        raise CommandRejected("too many outstanding self-wakes")

    if any(str((obligation.state_json or {}).get("summary") or "") == reason for obligation in pending_self_wakes):
        return AppliedCommandResult(events=[])

    create_or_refresh_obligation(
        session,
        run=run,
        actor_id=actor.id,
        title=f"Reminder: {reason}",
        category="reminder",
        summary=reason,
        due_at=due_time,
        visibility={"scope": "private", "owner_actor_id": actor.id},
        state_updates={"source": "self_wake"},
        dedupe_key=f"reminder:{actor.id}:{reason}",
        trigger_type=TriggerType.OBLIGATION_DUE.value,
        trigger_priority=5,
    )
    accepted_events = append_and_apply(
        session,
        run_id=run.id,
        sim_time=run.current_sim_time,
        events=[
            DomainEvent(
                event_type="ReminderScheduled",
                actor_id=actor.id,
                visibility={"scope": "actors", "actor_ids": [actor.id]},
                data={"minutes_from_now": minutes_from_now, "reason": reason},
            )
        ],
    )
    return AppliedCommandResult(events=accepted_events)


@_command_handler("obligations.complete")
def apply_complete_obligation(
    session: Session,
    run: SimulationRunRecord,
    actor: ActorRecord,
    command: IntentCommand,
) -> AppliedCommandResult:
    obligation_id = command.target_ref.get("obligation_id")
    resolution_note = (command.payload.get("resolution_note") or "").strip()
    obligation = require_actor_obligation(session, run.id, actor.id, obligation_id)
    complete_obligation(
        session,
        run=run,
        obligation=obligation,
        actor_id=actor.id,
        current_sim_time=run.current_sim_time,
        resolution_note=resolution_note or "Marked complete.",
    )
    return AppliedCommandResult(events=[])


@_command_handler("obligations.defer")
def apply_defer_obligation(
    session: Session,
    run: SimulationRunRecord,
    actor: ActorRecord,
    command: IntentCommand,
) -> AppliedCommandResult:
    obligation_id = command.target_ref.get("obligation_id")
    minutes_from_now = int(command.payload.get("minutes_from_now") or 0)
    reason = (command.payload.get("reason") or "").strip()
    if minutes_from_now <= 0:
        raise CommandRejected("minutes_from_now must be positive")
    if not reason:
        raise CommandRejected("defer reason cannot be empty")
    obligation = require_actor_obligation(session, run.id, actor.id, obligation_id)
    due_at = run.current_sim_time + timedelta(minutes=minutes_from_now)
    cancel_pending_obligation_triggers(session, run_id=run.id, obligation_id=obligation.id)
    update_obligation(
        session,
        run=run,
        obligation=obligation,
        actor_id=actor.id,
        current_sim_time=run.current_sim_time,
        state_updates={
            "status": "deferred",
            "due_at": due_at.isoformat(),
            "defer_reason": reason,
        },
        trigger_type=TriggerType.OBLIGATION_DUE.value,
        trigger_priority=8,
        resolution_note=reason,
    )
    return AppliedCommandResult(events=[])


@_command_handler("system.finish_assignment")
def apply_finish_assignment(
    session: Session,
    run: SimulationRunRecord,
    actor: ActorRecord,
    command: IntentCommand,
) -> AppliedCommandResult:
    require_permission(actor, "can_finish_assignment")
    summary = (command.payload.get("summary") or "").strip()
    raw_remaining_risks = command.payload.get("remaining_risks", [])
    if isinstance(raw_remaining_risks, str):
        remaining_risks = [raw_remaining_risks.strip()] if raw_remaining_risks.strip() else []
    else:
        remaining_risks = [
            str(item).strip() for item in raw_remaining_risks if str(item).strip()
        ]
    confidence = str(command.payload.get("confidence") or "").strip() or None
    if not summary:
        raise CommandRejected("finish summary cannot be empty")
    if run.status != RunStatus.RUNNING.value:
        raise CommandRejected("assignment can only be finished while the run is running")

    assignment = dict((run.config_json or {}).get("assignment") or {})
    # Snapshot readiness state for the record, but do NOT block — the PM
    # decides when they are done.  Judges evaluate the quality of that decision.
    finish_readiness_snapshot = closure_service.compute_completion_readiness(
        session,
        run_id=run.id,
        actor_id=actor.id,
    )
    assignment.update(
        {
            "state": "finished_by_pm",
            "finished_by_actor_id": actor.id,
            "finished_at": run.current_sim_time.isoformat(),
            "finish_summary": summary,
            "remaining_risks": remaining_risks,
            "confidence": confidence,
            "end_reason": "completed_by_pm",
            "finish_readiness_snapshot": finish_readiness_snapshot,
        }
    )
    state_store.update_run_config(session, run.id, {"assignment": assignment})
    state_store.update_run_status(session, run.id, RunStatus.COMPLETED.value, now=run.current_sim_time)

    accepted_events = append_and_apply(
        session,
        run_id=run.id,
        sim_time=run.current_sim_time,
        events=[
            DomainEvent(
                event_type="AssignmentFinished",
                actor_id=actor.id,
                visibility={"scope": "company"},
                data={
                    "summary": summary,
                    "remaining_risks": remaining_risks,
                    "confidence": confidence,
                    "finish_readiness_snapshot": finish_readiness_snapshot,
                },
            ),
            DomainEvent(
                event_type="RunCompleted",
                actor_id=actor.id,
                visibility={"scope": "admin"},
                data={"reason": "completed_by_pm"},
            ),
        ],
    )
    # NOTE: analysis is triggered from _finalize_turn after traces are persisted,
    # not here — otherwise judges run before traces are in the DB.
    return AppliedCommandResult(events=accepted_events)
