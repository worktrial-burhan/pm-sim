from __future__ import annotations

from datetime import datetime
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import DeliveryStatus, new_id
from app.services.db import ActorRecord, DeliveryRecord, EventRecord


def create_delivery(
    session: Session,
    *,
    run_id: str,
    event_id: str,
    actor_id: str,
    surface: str,
    summary_text: str,
    delivered_at_sim: datetime,
    metadata: dict | None = None,
) -> DeliveryRecord:
    record = DeliveryRecord(
        id=new_id("del"),
        run_id=run_id,
        event_id=event_id,
        actor_id=actor_id,
        surface=surface,
        summary_text=summary_text,
        delivered_at_sim=delivered_at_sim,
        status=DeliveryStatus.UNREAD.value,
        metadata_json=metadata or {},
    )
    session.add(record)
    session.flush()
    return record


def list_inbox(
    session: Session,
    *,
    run_id: str,
    actor_id: str,
    current_sim_time: datetime,
    surface: str | None = None,
    limit: int = 20,
) -> list[dict]:
    stmt = (
        select(DeliveryRecord, EventRecord, ActorRecord)
        .join(
            EventRecord,
            (EventRecord.id == DeliveryRecord.event_id) & (EventRecord.run_id == DeliveryRecord.run_id),
        )
        .outerjoin(
            ActorRecord,
            (ActorRecord.run_id == DeliveryRecord.run_id) & (ActorRecord.id == EventRecord.actor_id),
        )
        .where(
            DeliveryRecord.run_id == run_id,
            DeliveryRecord.actor_id == actor_id,
            DeliveryRecord.delivered_at_sim <= current_sim_time,
        )
        .order_by(DeliveryRecord.delivered_at_sim.desc())
    )
    if surface:
        stmt = stmt.where(DeliveryRecord.surface == surface)
    stmt = stmt.limit(limit)

    rows = session.execute(stmt).all()
    results = []
    for delivery, event, sender in rows:
        results.append(
            {
                "delivery_id": delivery.id,
                "surface": delivery.surface,
                "status": delivery.status,
                "delivered_at_sim": delivery.delivered_at_sim.isoformat(),
                "summary": delivery.summary_text,
                "event": {
                    "type": event.event_type,
                    "sender_actor_id": event.actor_id,
                    "sender_name": sender.name if sender is not None else event.actor_id,
                    "thread_id": event.object_id,
                },
                "metadata": delivery.metadata_json,
            }
        )
    return results


def mark_deliveries_read(
    session: Session,
    *,
    run_id: str,
    actor_id: str,
    delivery_ids: Iterable[str],
    read_at_sim: datetime,
) -> list[str]:
    ids = list(delivery_ids)
    if not ids:
        return []
    stmt = select(DeliveryRecord).where(
        DeliveryRecord.id.in_(ids),
        DeliveryRecord.run_id == run_id,
        DeliveryRecord.actor_id == actor_id,
        DeliveryRecord.delivered_at_sim <= read_at_sim,
        DeliveryRecord.status == DeliveryStatus.UNREAD.value,
    )
    marked_ids: list[str] = []
    for record in session.scalars(stmt):
        record.status = DeliveryStatus.READ.value
        record.read_at_sim = read_at_sim
        marked_ids.append(record.id)
    session.flush()
    return marked_ids


def actor_ids_with_unread_deliveries(
    session: Session,
    *,
    run_id: str,
    current_sim_time: datetime,
) -> list[str]:
    stmt = select(DeliveryRecord.actor_id).where(
        DeliveryRecord.run_id == run_id,
        DeliveryRecord.status == DeliveryStatus.UNREAD.value,
        DeliveryRecord.delivered_at_sim <= current_sim_time,
    )
    actor_ids = session.scalars(stmt).all()
    return sorted(set(actor_ids))


def actor_has_unread_deliveries(
    session: Session,
    *,
    run_id: str,
    actor_id: str,
    current_sim_time: datetime,
) -> bool:
    stmt = select(DeliveryRecord.id).where(
        DeliveryRecord.run_id == run_id,
        DeliveryRecord.actor_id == actor_id,
        DeliveryRecord.status == DeliveryStatus.UNREAD.value,
        DeliveryRecord.delivered_at_sim <= current_sim_time,
    )
    return session.scalar(stmt.limit(1)) is not None


def mark_thread_deliveries_read(
    session: Session,
    *,
    run_id: str,
    actor_id: str,
    thread_id: str,
    read_at_sim: datetime,
) -> list[str]:
    stmt = (
        select(DeliveryRecord)
        .join(
            EventRecord,
            (EventRecord.id == DeliveryRecord.event_id) & (EventRecord.run_id == DeliveryRecord.run_id),
        )
        .where(
            DeliveryRecord.run_id == run_id,
            DeliveryRecord.actor_id == actor_id,
            DeliveryRecord.status == DeliveryStatus.UNREAD.value,
            DeliveryRecord.delivered_at_sim <= read_at_sim,
            EventRecord.object_id == thread_id,
        )
    )
    marked_ids: list[str] = []
    for record in session.scalars(stmt):
        record.status = DeliveryStatus.READ.value
        record.read_at_sim = read_at_sim
        marked_ids.append(record.id)
    session.flush()
    return marked_ids


def delivery_is_unread(
    session: Session,
    *,
    run_id: str,
    actor_id: str,
    delivery_id: str,
    current_sim_time: datetime,
) -> bool:
    stmt = select(DeliveryRecord.id).where(
        DeliveryRecord.id == delivery_id,
        DeliveryRecord.run_id == run_id,
        DeliveryRecord.actor_id == actor_id,
        DeliveryRecord.status == DeliveryStatus.UNREAD.value,
        DeliveryRecord.delivered_at_sim <= current_sim_time,
    )
    return session.scalar(stmt.limit(1)) is not None
