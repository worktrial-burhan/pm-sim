from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.models import ActorTurnStatus, OrchestrationStatus, RunStatus, TriggerStatus
from app.domain.visibility import actor_can_view
from app.services.db import (
    ActorRecord,
    RunTickRecord,
    ActorTurnRecord,
    ActorStateRecord,
    SimulationRunRecord,
    TriggerRecord,
    WorldObjectRecord,
)

_UNSET = object()


def create_run(session: Session, record: SimulationRunRecord) -> SimulationRunRecord:
    session.add(record)
    session.flush()
    return record


def get_run(session: Session, run_id: str) -> SimulationRunRecord | None:
    return session.get(SimulationRunRecord, run_id)


def get_run_for_update(session: Session, run_id: str) -> SimulationRunRecord | None:
    stmt = select(SimulationRunRecord).where(SimulationRunRecord.id == run_id).with_for_update()
    return session.scalar(stmt)


def list_runs(session: Session) -> list[SimulationRunRecord]:
    return list(session.scalars(select(SimulationRunRecord).order_by(SimulationRunRecord.created_at.desc())))


def list_tickable_run_ids(session: Session) -> list[str]:
    stmt = select(SimulationRunRecord.id).where(
        SimulationRunRecord.status == RunStatus.RUNNING.value,
        SimulationRunRecord.orchestration_status == OrchestrationStatus.ATTACHED.value,
    )
    return list(session.scalars(stmt))


def update_run_status(session: Session, run_id: str, status: str, *, now: datetime | None = None) -> None:
    run = get_run(session, run_id)
    if run is None:
        return
    run.status = status
    # started_at / ended_at track real wall-clock time, not sim time.
    wall_now = datetime.now(timezone.utc)
    if status == RunStatus.RUNNING.value and run.started_at is None:
        run.started_at = wall_now
    if status in {RunStatus.STOPPED.value, RunStatus.COMPLETED.value}:
        run.ended_at = wall_now
    session.flush()


def update_run_orchestration(
    session: Session,
    run_id: str,
    *,
    orchestration_status: str,
    orchestration_workflow_id: str | None = None,
    orchestration_error: str | None = None,
) -> None:
    run = get_run(session, run_id)
    if run is None:
        return
    run.orchestration_status = orchestration_status
    run.orchestration_workflow_id = orchestration_workflow_id
    run.orchestration_error = orchestration_error
    session.flush()


def update_run_time(session: Session, run_id: str, current_sim_time: datetime) -> None:
    run = get_run(session, run_id)
    if run is None:
        return
    run.current_sim_time = current_sim_time
    session.flush()


def update_run_config(session: Session, run_id: str, config_updates: dict[str, Any]) -> None:
    run = get_run(session, run_id)
    if run is None:
        return
    next_config = dict(run.config_json or {})
    next_config.update(config_updates)
    run.config_json = next_config
    session.flush()


def create_actor(session: Session, record: ActorRecord) -> ActorRecord:
    session.add(record)
    session.flush()
    return record


def create_actor_state(session: Session, record: ActorStateRecord) -> ActorStateRecord:
    session.add(record)
    session.flush()
    return record


def get_actor(session: Session, run_id: str, actor_id: str) -> ActorRecord | None:
    stmt = select(ActorRecord).where(ActorRecord.run_id == run_id, ActorRecord.id == actor_id)
    return session.scalar(stmt)


def list_actors(session: Session, run_id: str) -> list[ActorRecord]:
    return list(session.scalars(select(ActorRecord).where(ActorRecord.run_id == run_id)))


def get_actor_state(session: Session, run_id: str, actor_id: str) -> ActorStateRecord | None:
    stmt = select(ActorStateRecord).where(
        ActorStateRecord.run_id == run_id,
        ActorStateRecord.actor_id == actor_id,
    )
    return session.scalar(stmt)


def set_actor_turn_window(
    session: Session,
    *,
    run_id: str,
    actor_id: str,
    started_at: datetime | None,
    completed_at: datetime | None,
    next_eligible_wake_time: datetime | None,
) -> None:
    state = get_actor_state(session, run_id, actor_id)
    if state is None:
        return
    state.last_decision_started_at_sim = started_at
    state.last_decision_completed_at_sim = completed_at
    state.next_eligible_wake_time = next_eligible_wake_time
    session.flush()


def update_sdk_session_id(
    session: Session, *, run_id: str, actor_id: str, sdk_session_id: str
) -> None:
    state = get_actor_state(session, run_id, actor_id)
    if state is None:
        return
    state.sdk_session_id = sdk_session_id
    session.flush()


def get_sdk_session_id(session: Session, run_id: str, actor_id: str) -> str | None:
    state = get_actor_state(session, run_id, actor_id)
    if state is None:
        return None
    return state.sdk_session_id


def create_world_object(session: Session, record: WorldObjectRecord) -> WorldObjectRecord:
    session.add(record)
    session.flush()
    return record


def get_world_object(session: Session, run_id: str, object_id: str) -> WorldObjectRecord | None:
    stmt = select(WorldObjectRecord).where(
        WorldObjectRecord.run_id == run_id, WorldObjectRecord.id == object_id
    )
    return session.scalar(stmt)


def list_world_objects(session: Session, run_id: str, kind: str | None = None) -> list[WorldObjectRecord]:
    stmt = select(WorldObjectRecord).where(WorldObjectRecord.run_id == run_id)
    if kind:
        stmt = stmt.where(WorldObjectRecord.kind == kind)
    return list(session.scalars(stmt.order_by(WorldObjectRecord.created_at.asc())))


def list_visible_world_objects(
    session: Session,
    *,
    run_id: str,
    actor_id: str,
    actor_role: str | None,
    actor_team: str | None,
    kind: str | None = None,
) -> list[WorldObjectRecord]:
    records = list_world_objects(session, run_id, kind=kind)
    return [
        record
        for record in records
        if actor_can_view(
            actor_id=actor_id,
            actor_role=actor_role,
            actor_team=actor_team,
            visibility=record.visibility_json,
        )
    ]


def list_actor_world_objects(
    session: Session,
    *,
    run_id: str,
    owner_actor_id: str,
    kind: str | None = None,
    include_archived: bool = False,
) -> list[WorldObjectRecord]:
    stmt = select(WorldObjectRecord).where(
        WorldObjectRecord.run_id == run_id,
        WorldObjectRecord.owner_actor_id == owner_actor_id,
    )
    if kind is not None:
        stmt = stmt.where(WorldObjectRecord.kind == kind)
    if not include_archived:
        stmt = stmt.where(WorldObjectRecord.archived_at.is_(None))
    stmt = stmt.order_by(WorldObjectRecord.created_at.asc())
    return list(session.scalars(stmt))


def update_world_object_state(
    session: Session,
    *,
    run_id: str,
    object_id: str,
    state_updates: dict[str, Any],
) -> None:
    obj = get_world_object(session, run_id, object_id)
    if obj is None:
        return
    next_state = dict(obj.state_json or {})
    next_state.update(state_updates)
    obj.state_json = next_state
    session.flush()


def archive_world_object(
    session: Session,
    *,
    run_id: str,
    object_id: str,
    archived_at: datetime,
) -> None:
    obj = get_world_object(session, run_id, object_id)
    if obj is None:
        return
    obj.archived_at = archived_at
    session.flush()


def create_trigger(session: Session, record: TriggerRecord) -> TriggerRecord:
    session.add(record)
    session.flush()
    return record


def list_pending_triggers(session: Session, run_id: str, *, actor_id: str | None = None) -> list[TriggerRecord]:
    stmt = select(TriggerRecord).where(
        TriggerRecord.run_id == run_id,
        TriggerRecord.status == TriggerStatus.PENDING.value,
    )
    if actor_id is not None:
        stmt = stmt.where(TriggerRecord.actor_id == actor_id)
    return list(session.scalars(stmt))


def update_trigger_due_time(
    session: Session,
    *,
    run_id: str,
    trigger_id: str,
    due_sim_time: datetime,
) -> None:
    stmt = select(TriggerRecord).where(
        TriggerRecord.run_id == run_id,
        TriggerRecord.id == trigger_id,
    )
    record = session.scalar(stmt)
    if record is None:
        return
    record.due_sim_time = due_sim_time
    session.flush()


def earliest_pending_trigger_time(session: Session, run_id: str) -> datetime | None:
    """Return the earliest due_sim_time among all pending triggers for a run."""
    stmt = select(func.min(TriggerRecord.due_sim_time)).where(
        TriggerRecord.run_id == run_id,
        TriggerRecord.status == TriggerStatus.PENDING.value,
    )
    return session.scalar(stmt)


def update_trigger_status(
    session: Session,
    *,
    run_id: str,
    trigger_id: str,
    status: str,
) -> None:
    stmt = select(TriggerRecord).where(
        TriggerRecord.run_id == run_id,
        TriggerRecord.id == trigger_id,
    )
    record = session.scalar(stmt)
    if record is None:
        return
    record.status = status
    session.flush()


def create_actor_turn(session: Session, record: ActorTurnRecord) -> ActorTurnRecord:
    session.add(record)
    session.flush()
    return record


def get_actor_turn(session: Session, turn_id: str) -> ActorTurnRecord | None:
    return session.get(ActorTurnRecord, turn_id)


def get_actor_turn_for_update(session: Session, turn_id: str) -> ActorTurnRecord | None:
    stmt = select(ActorTurnRecord).where(ActorTurnRecord.id == turn_id).with_for_update()
    return session.scalar(stmt)


def actor_ids_with_open_turns(session: Session, run_id: str) -> set[str]:
    stmt = select(ActorTurnRecord.actor_id).where(
        ActorTurnRecord.run_id == run_id,
        ActorTurnRecord.status.in_(
            [
                ActorTurnStatus.PREPARED.value,
                ActorTurnStatus.DECIDING.value,
                ActorTurnStatus.DECIDED.value,
            ]
        ),
    )
    return set(session.scalars(stmt))


def update_actor_turn(
    session: Session,
    turn_id: str,
    *,
    status: str | None | object = _UNSET,
    request_context_json: dict | None | object = _UNSET,
    decision_json: dict | None | object = _UNSET,
    error_json: dict | None | object = _UNSET,
    prepared_at: datetime | None | object = _UNSET,
    decided_at: datetime | None | object = _UNSET,
    applied_at: datetime | None | object = _UNSET,
) -> None:
    record = get_actor_turn(session, turn_id)
    if record is None:
        return
    if status is not _UNSET:
        record.status = status
    if request_context_json is not _UNSET:
        record.request_context_json = request_context_json
    if decision_json is not _UNSET:
        record.decision_json = decision_json
    if error_json is not _UNSET:
        record.error_json = error_json
    if prepared_at is not _UNSET:
        record.prepared_at = prepared_at
    if decided_at is not _UNSET:
        record.decided_at = decided_at
    if applied_at is not _UNSET:
        record.applied_at = applied_at
    session.flush()


def create_run_tick(session: Session, record: RunTickRecord) -> RunTickRecord:
    session.add(record)
    session.flush()
    return record


def get_run_tick(session: Session, run_id: str, tick_token: str) -> RunTickRecord | None:
    stmt = select(RunTickRecord).where(
        RunTickRecord.run_id == run_id,
        RunTickRecord.tick_token == tick_token,
    )
    return session.scalar(stmt)
