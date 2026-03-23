from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import TriggerStatus
from app.services.db import TriggerRecord


def get_due_triggers(session: Session, *, run_id: str, current_sim_time: datetime) -> list[TriggerRecord]:
    stmt = (
        select(TriggerRecord)
        .where(
            TriggerRecord.run_id == run_id,
            TriggerRecord.status == TriggerStatus.PENDING.value,
            TriggerRecord.due_sim_time <= current_sim_time,
        )
        .order_by(TriggerRecord.priority.desc(), TriggerRecord.due_sim_time.asc())
    )
    return list(session.scalars(stmt))


def mark_trigger_fired(session: Session, trigger: TriggerRecord) -> None:
    trigger.status = TriggerStatus.FIRED.value
    session.flush()

