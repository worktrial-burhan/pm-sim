from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.domain.events import DomainEvent
from app.domain.models import ObjectKind, TriggerStatus, TriggerType, new_id
from app.domain.engine_common import CommandRejected, append_and_apply, require_object_kind
from app.services import attention_service, delivery_service, state_store
from app.services.db import SimulationRunRecord, TriggerRecord, WorldObjectRecord


def create_delivery_with_attention(
    session: Session,
    *,
    run: SimulationRunRecord,
    actor_id: str,
    event_id: str,
    surface: str,
    summary_text: str,
    delivered_at_sim: datetime,
    metadata: dict[str, Any] | None = None,
) -> None:
    delivery = delivery_service.create_delivery(
        session,
        run_id=run.id,
        event_id=event_id,
        actor_id=actor_id,
        surface=surface,
        summary_text=summary_text,
        delivered_at_sim=delivered_at_sim,
        metadata=metadata or {},
    )
    actor = state_store.get_actor(session, run.id, actor_id)
    actor_state = state_store.get_actor_state(session, run.id, actor_id)
    if actor is None or surface == "calendar":
        return

    # Schedule a trigger to wake the actor so they see the message.
    # No reply obligation is created — the actor reads the message and
    # decides for themselves whether a response is warranted.
    due_time = attention_service.next_response_due_time(
        actor,
        delivered_at_sim=delivered_at_sim,
        surface=surface,
        workload=None if actor_state is None else actor_state.workload_json,
    )
    _schedule_attention_trigger(
        session,
        run=run,
        actor_id=actor_id,
        due_at=due_time,
        delivery_id=delivery.id,
        surface=surface,
        metadata=metadata or {},
    )


def list_open_actor_obligations(
    session: Session,
    *,
    run_id: str,
    actor_id: str,
    category: str | None = None,
) -> list[WorldObjectRecord]:
    obligations = state_store.list_actor_world_objects(
        session,
        run_id=run_id,
        owner_actor_id=actor_id,
        kind=ObjectKind.OBLIGATION.value,
    )
    results: list[WorldObjectRecord] = []
    for obligation in obligations:
        state = obligation.state_json or {}
        status = str(state.get("status") or "").strip().lower()
        if status in {"done", "cancelled"}:
            continue
        if category is not None and str(state.get("category") or "") != category:
            continue
        results.append(obligation)
    return results


def require_actor_obligation(
    session: Session,
    run_id: str,
    actor_id: str,
    obligation_id: str | None,
) -> WorldObjectRecord:
    obligation = require_object_kind(session, run_id, obligation_id, ObjectKind.OBLIGATION.value)
    if obligation.owner_actor_id != actor_id:
        raise CommandRejected("actor does not own this obligation")
    return obligation


def create_or_refresh_obligation(
    session: Session,
    *,
    run: SimulationRunRecord,
    actor_id: str,
    title: str,
    category: str,
    summary: str,
    due_at: datetime,
    visibility: dict[str, Any],
    state_updates: dict[str, Any],
    dedupe_key: str,
    trigger_type: str,
    trigger_priority: int,
    trigger_metadata: dict[str, Any] | None = None,
) -> WorldObjectRecord:
    base_state = {
        "category": category,
        "status": "queued",
        "summary": summary,
        "due_at": due_at.isoformat(),
        "dedupe_key": dedupe_key,
        **state_updates,
    }
    existing = _find_obligation_by_dedupe_key(
        session,
        run_id=run.id,
        actor_id=actor_id,
        dedupe_key=dedupe_key,
    )
    if existing is None:
        obligation_id = new_id("obl")
        append_and_apply(
            session,
            run_id=run.id,
            sim_time=run.current_sim_time,
            events=[
                DomainEvent(
                    event_type="ObligationCreated",
                    actor_id=actor_id,
                    object_id=obligation_id,
                    visibility=visibility,
                    data={
                        "title": title,
                        "owner_actor_id": actor_id,
                        "state": base_state,
                    },
                )
            ],
        )
        obligation = state_store.get_world_object(session, run.id, obligation_id)
        assert obligation is not None
        schedule_obligation_trigger(
            session,
            run=run,
            actor_id=actor_id,
            obligation_id=obligation_id,
            due_at=due_at,
            trigger_type=trigger_type,
            priority=trigger_priority,
            metadata=trigger_metadata,
        )
        return obligation

    update_obligation(
        session,
        run=run,
        obligation=existing,
        actor_id=actor_id,
        current_sim_time=run.current_sim_time,
        state_updates=base_state,
        trigger_type=trigger_type,
        trigger_priority=trigger_priority,
        resolution_note="refreshed",
        trigger_metadata=trigger_metadata,
    )
    refreshed = state_store.get_world_object(session, run.id, existing.id)
    assert refreshed is not None
    return refreshed


def update_obligation(
    session: Session,
    *,
    run: SimulationRunRecord,
    obligation: WorldObjectRecord,
    actor_id: str,
    current_sim_time: datetime,
    state_updates: dict[str, Any],
    trigger_type: str,
    trigger_priority: int,
    resolution_note: str,
    trigger_metadata: dict[str, Any] | None = None,
    event_type: str = "ObligationUpdated",
) -> None:
    next_state = dict(obligation.state_json or {})
    next_state.update(state_updates)
    append_and_apply(
        session,
        run_id=run.id,
        sim_time=current_sim_time,
        events=[
            DomainEvent(
                event_type=event_type,
                actor_id=actor_id,
                object_id=obligation.id,
                visibility=obligation.visibility_json,
                data={"state": next_state, "resolution_note": resolution_note},
            )
        ],
    )
    if str(next_state.get("status") or "") in {"done", "cancelled"}:
        cancel_pending_obligation_triggers(session, run_id=run.id, obligation_id=obligation.id)
        return

    due_at_raw = next_state.get("due_at")
    due_at = datetime.fromisoformat(due_at_raw) if due_at_raw else current_sim_time
    schedule_obligation_trigger(
        session,
        run=run,
        actor_id=obligation.owner_actor_id or actor_id,
        obligation_id=obligation.id,
        due_at=due_at,
        trigger_type=trigger_type,
        priority=trigger_priority,
        metadata=trigger_metadata,
    )


def complete_obligation(
    session: Session,
    *,
    run: SimulationRunRecord,
    obligation: WorldObjectRecord,
    actor_id: str,
    current_sim_time: datetime,
    resolution_note: str,
) -> None:
    update_obligation(
        session,
        run=run,
        obligation=obligation,
        actor_id=actor_id,
        current_sim_time=current_sim_time,
        state_updates={
            "status": "done",
            "completed_at": current_sim_time.isoformat(),
            "completed_by_actor_id": actor_id,
            "resolution_note": resolution_note,
        },
        trigger_type=TriggerType.OBLIGATION_DUE.value,
        trigger_priority=0,
        resolution_note=resolution_note,
        event_type="ObligationCompleted",
    )


def schedule_obligation_trigger(
    session: Session,
    *,
    run: SimulationRunRecord,
    actor_id: str,
    obligation_id: str,
    due_at: datetime,
    trigger_type: str,
    priority: int,
    metadata: dict[str, Any] | None = None,
) -> None:
    existing = None
    for trigger in state_store.list_pending_triggers(session, run.id, actor_id=actor_id):
        if trigger.object_id == obligation_id and trigger.trigger_type == trigger_type:
            existing = trigger
            break
    payload = {"obligation_id": obligation_id, **(metadata or {})}
    if existing is not None:
        state_store.update_trigger_due_time(
            session,
            run_id=run.id,
            trigger_id=existing.id,
            due_sim_time=due_at,
        )
        existing.priority = priority
        existing.data_json = payload
        session.flush()
        return

    state_store.create_trigger(
        session,
        TriggerRecord(
            id=new_id("trg"),
            run_id=run.id,
            trigger_type=trigger_type,
            due_sim_time=due_at,
            actor_id=actor_id,
            object_id=obligation_id,
            status=TriggerStatus.PENDING.value,
            priority=priority,
            data_json=payload,
        ),
    )


def cancel_pending_obligation_triggers(session: Session, *, run_id: str, obligation_id: str) -> None:
    for trigger in state_store.list_pending_triggers(session, run_id):
        if trigger.object_id != obligation_id:
            continue
        state_store.update_trigger_status(
            session,
            run_id=run_id,
            trigger_id=trigger.id,
            status=TriggerStatus.CANCELLED.value,
        )


def complete_meeting_obligations(
    session: Session,
    *,
    run: SimulationRunRecord,
    actor_id: str,
    meeting_id: str,
    current_sim_time: datetime,
    resolution_note: str,
) -> None:
    for obligation in list_open_actor_obligations(session, run_id=run.id, actor_id=actor_id, category="meeting"):
        if str((obligation.state_json or {}).get("meeting_id") or "") != meeting_id:
            continue
        complete_obligation(
            session,
            run=run,
            obligation=obligation,
            actor_id=actor_id,
            current_sim_time=current_sim_time,
            resolution_note=resolution_note,
        )


def _schedule_attention_trigger(
    session: Session,
    *,
    run: SimulationRunRecord,
    actor_id: str,
    due_at: datetime,
    delivery_id: str,
    surface: str,
    metadata: dict[str, Any],
) -> None:
    """Schedule a RESPONSE_DELAY trigger so the actor wakes and reads the delivery.

    No obligation world-object is created — the actor decides for themselves
    whether a reply is warranted.
    """
    state_store.create_trigger(
        session,
        TriggerRecord(
            id=new_id("trg"),
            run_id=run.id,
            trigger_type=TriggerType.RESPONSE_DELAY.value,
            due_sim_time=due_at,
            actor_id=actor_id,
            status=TriggerStatus.PENDING.value,
            priority=6,
            data_json={
                "delivery_id": delivery_id,
                "surface": surface,
                **metadata,
            },
        ),
    )


def _find_obligation_by_dedupe_key(
    session: Session,
    *,
    run_id: str,
    actor_id: str,
    dedupe_key: str,
) -> WorldObjectRecord | None:
    for obligation in list_open_actor_obligations(session, run_id=run_id, actor_id=actor_id):
        if str((obligation.state_json or {}).get("dedupe_key") or "") == dedupe_key:
            return obligation
    return None
