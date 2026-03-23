from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import TraceType, new_id
from app.services.db import TraceRecord


def append_trace(
    session: Session,
    *,
    run_id: str,
    sim_time: datetime,
    trace_type: TraceType | str,
    actor_id: str | None = None,
    related_event_id: str | None = None,
    related_object_id: str | None = None,
    data: dict[str, Any] | None = None,
) -> TraceRecord:
    normalized_type = trace_type.value if isinstance(trace_type, TraceType) else str(trace_type)
    record = TraceRecord(
        id=new_id("trc"),
        run_id=run_id,
        actor_id=actor_id,
        trace_type=normalized_type,
        sim_time=sim_time,
        related_event_id=related_event_id,
        related_object_id=related_object_id,
        data_json=data or {},
    )
    session.add(record)
    session.flush()
    return record


def list_actor_traces(session: Session, *, run_id: str, actor_id: str, limit: int = 100) -> list[TraceRecord]:
    stmt = (
        select(TraceRecord)
        .where(TraceRecord.run_id == run_id, TraceRecord.actor_id == actor_id)
        .order_by(TraceRecord.created_at.desc())
        .limit(limit)
    )
    return list(session.scalars(stmt))


def list_run_traces(
    session: Session,
    *,
    run_id: str,
    limit: int | None = 200,
    trace_types: list[str] | None = None,
) -> list[TraceRecord]:
    stmt = select(TraceRecord).where(TraceRecord.run_id == run_id)
    if trace_types:
        stmt = stmt.where(TraceRecord.trace_type.in_(trace_types))
    stmt = stmt.order_by(TraceRecord.created_at.desc())
    if limit is not None:
        stmt = stmt.limit(limit)
    return list(session.scalars(stmt))
