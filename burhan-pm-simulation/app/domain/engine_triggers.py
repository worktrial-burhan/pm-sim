from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.domain.events import DomainEvent
from app.domain.models import ObjectKind, TriggerStatus, TriggerType, new_id
from app.domain.scheduler import mark_trigger_fired
from app.domain.engine_attention import (
    complete_meeting_obligations,
    create_delivery_with_attention,
    create_or_refresh_obligation,
)
from app.domain.engine_common import append_and_apply, conditions_match, require_object_kind
from app.services import delivery_service, state_store
from app.services.db import SimulationRunRecord, TriggerRecord

TriggerHandler = Callable[[Session, SimulationRunRecord, TriggerRecord, datetime], list[str]]
_TRIGGER_HANDLERS: dict[str, TriggerHandler] = {}


def _trigger_handler(*trigger_types: str) -> Callable[[TriggerHandler], TriggerHandler]:
    def decorator(fn: TriggerHandler) -> TriggerHandler:
        for trigger_type in trigger_types:
            _TRIGGER_HANDLERS[trigger_type] = fn
        return fn

    return decorator


def apply_trigger(
    session: Session,
    *,
    run: SimulationRunRecord,
    trigger: TriggerRecord,
    current_sim_time: datetime,
) -> list[str]:
    mark_trigger_fired(session, trigger)
    handler = _TRIGGER_HANDLERS.get(trigger.trigger_type)
    if handler is None:
        _record_trigger_fired_event(session, run=run, trigger=trigger, current_sim_time=current_sim_time)
        return []
    return handler(session, run, trigger, current_sim_time)


def _record_trigger_fired_event(
    session: Session,
    *,
    run: SimulationRunRecord,
    trigger: TriggerRecord,
    current_sim_time: datetime,
) -> None:
    append_and_apply(
        session,
        run_id=run.id,
        sim_time=current_sim_time,
        events=[
            DomainEvent(
                event_type="ScenarioTriggerFired",
                actor_id=trigger.actor_id,
                object_id=trigger.object_id,
                visibility={"scope": "admin"},
                data={"trigger_type": trigger.trigger_type, "trigger_id": trigger.id},
            )
        ],
    )


@_trigger_handler(TriggerType.ACTOR_ROUTINE_WAKE.value)
def handle_actor_routine_trigger(
    session: Session,
    run: SimulationRunRecord,
    trigger: TriggerRecord,
    current_sim_time: datetime,
) -> list[str]:
    if not trigger.actor_id:
        return []
    recurring_interval = int((trigger.data_json or {}).get("recurring_interval_minutes") or 0)
    if recurring_interval > 0:
        state_store.create_trigger(
            session,
            TriggerRecord(
                id=new_id("trg"),
                run_id=run.id,
                trigger_type=TriggerType.ACTOR_ROUTINE_WAKE.value,
                due_sim_time=current_sim_time + timedelta(minutes=recurring_interval),
                actor_id=trigger.actor_id,
                object_id=trigger.object_id,
                status=TriggerStatus.PENDING.value,
                priority=trigger.priority,
                data_json=trigger.data_json or {},
            ),
        )
    _record_trigger_fired_event(session, run=run, trigger=trigger, current_sim_time=current_sim_time)
    return [trigger.actor_id]


@_trigger_handler(TriggerType.RESPONSE_DELAY.value)
def handle_response_delay_trigger(
    session: Session,
    run: SimulationRunRecord,
    trigger: TriggerRecord,
    current_sim_time: datetime,
) -> list[str]:
    if not trigger.actor_id:
        return []
    _record_trigger_fired_event(session, run=run, trigger=trigger, current_sim_time=current_sim_time)
    delivery_id = str((trigger.data_json or {}).get("delivery_id") or "").strip()
    if not delivery_id:
        return []
    if not delivery_service.delivery_is_unread(
        session,
        run_id=run.id,
        actor_id=trigger.actor_id,
        delivery_id=delivery_id,
        current_sim_time=current_sim_time,
    ):
        return []
    return [trigger.actor_id]


@_trigger_handler(TriggerType.OBLIGATION_DUE.value)
def handle_obligation_due_trigger(
    session: Session,
    run: SimulationRunRecord,
    trigger: TriggerRecord,
    current_sim_time: datetime,
) -> list[str]:
    if not trigger.actor_id:
        return []
    _record_trigger_fired_event(session, run=run, trigger=trigger, current_sim_time=current_sim_time)
    obligation = state_store.get_world_object(session, run.id, trigger.object_id)
    if obligation is None or obligation.kind != ObjectKind.OBLIGATION.value:
        return []
    status = str((obligation.state_json or {}).get("status") or "").strip().lower()
    if status in {"done", "cancelled"}:
        return []
    return [trigger.actor_id]


@_trigger_handler(TriggerType.MEETING_START.value)
def handle_meeting_start_trigger(
    session: Session,
    run: SimulationRunRecord,
    trigger: TriggerRecord,
    current_sim_time: datetime,
) -> list[str]:
    meeting = require_object_kind(session, run.id, trigger.object_id, ObjectKind.MEETING.value)
    attendees = list((meeting.state_json or {}).get("attendee_actor_ids", []))
    events = append_and_apply(
        session,
        run_id=run.id,
        sim_time=current_sim_time,
        events=[
            DomainEvent(
                event_type="MeetingStarted",
                object_id=meeting.id,
                visibility=meeting.visibility_json,
                data={"trigger_id": trigger.id},
            )
        ],
    )
    for attendee_actor_id in attendees:
        create_or_refresh_obligation(
            session,
            run=run,
            actor_id=attendee_actor_id,
            title=f"Attend: {meeting.title}",
            category="meeting",
            summary=f"Participate in meeting '{meeting.title}'.",
            due_at=current_sim_time,
            visibility={"scope": "private", "owner_actor_id": attendee_actor_id},
            state_updates={
                "meeting_id": meeting.id,
                "related_object_id": (meeting.state_json or {}).get("related_object_id"),
            },
            dedupe_key=f"meeting:{meeting.id}:{attendee_actor_id}",
            trigger_type=TriggerType.OBLIGATION_DUE.value,
            trigger_priority=15,
        )
        create_delivery_with_attention(
            session,
            run=run,
            actor_id=attendee_actor_id,
            event_id=events[0].id,
            surface="calendar",
            summary_text=f"Meeting started: {meeting.title}",
            delivered_at_sim=current_sim_time,
            metadata={"meeting_id": meeting.id},
        )
    return attendees


@_trigger_handler(TriggerType.MEETING_END.value)
def handle_meeting_end_trigger(
    session: Session,
    run: SimulationRunRecord,
    trigger: TriggerRecord,
    current_sim_time: datetime,
) -> list[str]:
    meeting = require_object_kind(session, run.id, trigger.object_id, ObjectKind.MEETING.value)
    attendees = list((meeting.state_json or {}).get("attendee_actor_ids", []))
    append_and_apply(
        session,
        run_id=run.id,
        sim_time=current_sim_time,
        events=[
            DomainEvent(
                event_type="MeetingEnded",
                object_id=meeting.id,
                visibility=meeting.visibility_json,
                data={"trigger_id": trigger.id},
            )
        ],
    )
    for attendee_actor_id in attendees:
        complete_meeting_obligations(
            session,
            run=run,
            actor_id=attendee_actor_id,
            meeting_id=meeting.id,
            current_sim_time=current_sim_time,
            resolution_note="Meeting window ended.",
        )
    return []


@_trigger_handler("state_patch")
def handle_state_patch_trigger(
    session: Session,
    run: SimulationRunRecord,
    trigger: TriggerRecord,
    current_sim_time: datetime,
) -> list[str]:
    data = trigger.data_json or {}
    if not conditions_match(session, run.id, data.get("conditions", [])):
        append_and_apply(
            session,
            run_id=run.id,
            sim_time=current_sim_time,
            events=[
                DomainEvent(
                    event_type="StatePatchSkipped",
                    actor_id=trigger.actor_id,
                    object_id=trigger.object_id,
                    visibility={"scope": "admin"},
                    data={"trigger_id": trigger.id},
                )
            ],
        )
        return []

    emitted_events = [
        DomainEvent(
            event_type=data.get("event_type", "StatePatchApplied"),
            actor_id=trigger.actor_id,
            object_id=trigger.object_id,
            visibility=data.get("visibility", {"scope": "admin"}),
            data={
                "trigger_id": trigger.id,
                "reducer_hint": "noop",
                **(data.get("event_data") or {}),
            },
        )
    ]
    accepted_events = append_and_apply(
        session,
        run_id=run.id,
        sim_time=current_sim_time,
        events=emitted_events,
    )

    for update in data.get("updates", []):
        state_store.update_world_object_state(
            session,
            run_id=run.id,
            object_id=update["object_id"],
            state_updates=update.get("state_updates", {}),
        )

    for delivery in data.get("deliveries", []):
        create_delivery_with_attention(
            session,
            run=run,
            actor_id=delivery["actor_id"],
            event_id=accepted_events[0].id,
            surface=delivery.get("surface", "chat"),
            summary_text=delivery.get("summary_text", accepted_events[0].event_type),
            delivered_at_sim=current_sim_time,
            metadata=delivery.get("metadata", {}),
        )
    return []
