from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.events import DomainEvent
from app.domain.visibility import actor_can_view
from app.domain.models import new_id
from app.services.db import EventRecord, SimulationRunRecord


def reserve_event_sequences(session: Session, run_id: str, count: int) -> list[int]:
    run_stmt = select(SimulationRunRecord).where(SimulationRunRecord.id == run_id).with_for_update()
    run = session.scalar(run_stmt)
    if run is None:
        raise ValueError(f"run not found: {run_id}")

    start_seq = run.next_event_seq
    run.next_event_seq += count
    session.flush()
    return list(range(start_seq, start_seq + count))


def append_event(
    session: Session,
    *,
    run_id: str,
    sim_time: datetime,
    event: DomainEvent,
) -> EventRecord:
    return append_events(session, run_id=run_id, sim_time=sim_time, events=[event])[0]


def append_events(
    session: Session,
    *,
    run_id: str,
    sim_time: datetime,
    events: list[DomainEvent],
) -> list[EventRecord]:
    if not events:
        return []

    seqs = reserve_event_sequences(session, run_id, len(events))
    records: list[EventRecord] = []
    for seq, event in zip(seqs, events):
        record = EventRecord(
            id=new_id("evt"),
            run_id=run_id,
            seq=seq,
            sim_time=sim_time,
            event_type=event.event_type,
            actor_id=event.actor_id,
            object_id=event.object_id,
            visibility_json=event.visibility,
            data_json=event.data,
        )
        session.add(record)
        records.append(record)

    session.flush()
    return records


def list_events(session: Session, *, run_id: str, limit: int | None = 200) -> list[EventRecord]:
    stmt = (
        select(EventRecord)
        .where(EventRecord.run_id == run_id)
        .order_by(EventRecord.seq.desc())
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    return list(session.scalars(stmt))


def list_thread_messages(
    session: Session,
    *,
    run_id: str,
    thread_id: str,
    actor_id: str,
    actor_role: str | None,
    actor_team: str | None,
) -> list[EventRecord]:
    stmt = (
        select(EventRecord)
        .where(
            EventRecord.run_id == run_id,
            EventRecord.object_id == thread_id,
            EventRecord.event_type.in_(["ChatMessageSent", "EmailSent"]),
        )
        .order_by(EventRecord.seq.asc())
    )
    visible_events: list[EventRecord] = []
    for event in session.scalars(stmt):
        if actor_can_view(
            actor_id=actor_id,
            actor_role=actor_role,
            actor_team=actor_team,
            visibility=event.visibility_json,
        ):
            visible_events.append(event)
    return visible_events
