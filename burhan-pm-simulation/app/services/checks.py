from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.scenarios.schemas import RubricCheck
from app.services import state_store
from app.services.db import EventRecord


def evaluate_check(session: Session, *, run_id: str, check: RubricCheck) -> tuple[bool, dict[str, Any]]:
    if check.type == "object_state":
        obj = state_store.get_world_object(session, run_id, check.object_id)
        if obj is None:
            return False, {"reason": "object not found"}
        value = (obj.state_json or {}).get(check.state_key)
        passed = True
        if check.equals is not None:
            passed = value == check.equals
        if check.not_equals is not None:
            passed = passed and value != check.not_equals
        return passed, {"observed": value}

    if check.type in {"event_exists", "event_exists_before"}:
        events = matching_events(session, run_id=run_id, check=check)
        if check.type == "event_exists_before" and check.before_sim_time:
            cutoff = ensure_aware_utc(datetime.fromisoformat(check.before_sim_time))
            events = [event for event in events if ensure_aware_utc(event.sim_time) <= cutoff]
        return bool(events), {"matching_event_count": len(events)}

    return False, {"reason": f"unsupported check type: {check.type}"}


def matching_events(session: Session, *, run_id: str, check: RubricCheck) -> list[EventRecord]:
    stmt = select(EventRecord).where(EventRecord.run_id == run_id)

    event_types = [item for item in check.event_types if item]
    if check.event_type and check.event_type not in event_types:
        event_types.append(check.event_type)
    if event_types:
        stmt = stmt.where(EventRecord.event_type.in_(event_types))
    if check.actor_id:
        stmt = stmt.where(EventRecord.actor_id == check.actor_id)
    if check.object_id:
        stmt = stmt.where(EventRecord.object_id == check.object_id)

    events = list(session.scalars(stmt.order_by(EventRecord.seq.asc())))
    matches: list[EventRecord] = []
    for event in events:
        data = event.data_json or {}
        if check.recipient_actor_id:
            recipients = data.get("recipient_actor_ids") or []
            if check.recipient_actor_id not in recipients:
                continue
        if check.data_contains and not _data_contains(data, check.data_contains):
            continue
        matches.append(event)
    return matches


def ensure_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _data_contains(data: dict[str, Any], expected: dict[str, Any]) -> bool:
    for key, value in expected.items():
        observed = data.get(key)
        if isinstance(observed, list) and not isinstance(value, list):
            if value not in observed:
                return False
            continue
        if observed != value:
            return False
    return True
