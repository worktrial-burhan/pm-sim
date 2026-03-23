from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, Text, UniqueConstraint, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from app.domain.models import utcnow
from app.services.config import get_settings


class Base(DeclarativeBase):
    pass


class SimulationRunRecord(Base):
    __tablename__ = "simulation_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    scenario_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    orchestration_status: Mapped[str] = mapped_column(String(32), nullable=False, default="unattached")
    orchestration_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    orchestration_workflow_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    current_sim_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    tick_wall_seconds: Mapped[float] = mapped_column(nullable=False)
    tick_sim_seconds: Mapped[int] = mapped_column(nullable=False)
    max_actor_invocations_per_tick: Mapped[int] = mapped_column(nullable=False)
    next_event_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    next_turn_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    config_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )


class ActorRecord(Base):
    __tablename__ = "actors"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(128), nullable=False)
    team: Mapped[str | None] = mapped_column(String(128), nullable=True)
    manager_actor_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    controller_type: Mapped[str] = mapped_column(String(32), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    working_hours_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    permissions_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    profile_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )


class ActorStateRecord(Base):
    __tablename__ = "actor_state"

    actor_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    goals_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    beliefs_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    relationships_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    commitments_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    workload_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    sdk_session_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    focus_state_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    next_eligible_wake_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_decision_started_at_sim: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_decision_completed_at_sim: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )


class WorldObjectRecord(Base):
    __tablename__ = "world_objects"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    owner_actor_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parent_object_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    visibility_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    state_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EventRecord(Base):
    __tablename__ = "events"
    __table_args__ = (UniqueConstraint("run_id", "seq", name="uq_events_run_seq"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    sim_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    object_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    visibility_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    data_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class DeliveryRecord(Base):
    __tablename__ = "deliveries"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    event_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    surface: Mapped[str] = mapped_column(String(64), nullable=False)
    summary_text: Mapped[str] = mapped_column(Text, nullable=False)
    delivered_at_sim: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    read_at_sim: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class TriggerRecord(Base):
    __tablename__ = "triggers"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    trigger_type: Mapped[str] = mapped_column(String(64), nullable=False)
    due_sim_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    actor_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    object_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    data_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )


class RunTickRecord(Base):
    __tablename__ = "run_ticks"
    __table_args__ = (UniqueConstraint("run_id", "tick_token", name="uq_run_ticks_run_token"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    tick_token: Mapped[str] = mapped_column(String(128), nullable=False)
    sim_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    turn_ids_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class ActorTurnRecord(Base):
    __tablename__ = "actor_turns"
    __table_args__ = (UniqueConstraint("run_id", "turn_seq", name="uq_actor_turns_run_turn_seq"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    turn_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    sim_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    cause_type: Mapped[str] = mapped_column(String(64), nullable=False)
    cause_ref_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    request_context_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    decision_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    prepared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )


class TraceRecord(Base):
    __tablename__ = "traces"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    actor_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    trace_type: Mapped[str] = mapped_column(String(64), nullable=False)
    sim_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    related_event_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    related_object_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    data_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class RunAnalysisRecord(Base):
    __tablename__ = "run_analyses"
    __table_args__ = (UniqueConstraint("run_id", "analysis_type", name="uq_run_analyses_run_type"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    analysis_type: Mapped[str] = mapped_column(String(32), nullable=False)  # "summary" or "judgment"
    content_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class EvaluationRecord(Base):
    __tablename__ = "evaluations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    checkpoint_name: Mapped[str] = mapped_column(String(128), nullable=False)
    sim_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    score_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    rationale_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


_engine = None
_SessionLocal: sessionmaker[Session] | None = None


def _connect_args(database_url: str) -> dict:
    if database_url.startswith("sqlite"):
        return {"check_same_thread": False}
    return {}


def configure_engine(database_url: str | None = None):
    global _engine, _SessionLocal

    settings = get_settings()
    url = database_url or settings.database_url

    if url.startswith("postgresql"):
        engine_kwargs = {
            "pool_size": 10,
            "max_overflow": 20,
            "pool_pre_ping": True,
        }
    else:
        engine_kwargs = {"pool_pre_ping": True}

    _engine = create_engine(url, future=True, connect_args=_connect_args(url), **engine_kwargs)
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, autoflush=False)
    return _engine


def get_engine():
    global _engine
    if _engine is None:
        return configure_engine()
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _SessionLocal
    if _SessionLocal is None:
        configure_engine()
    assert _SessionLocal is not None
    return _SessionLocal


@contextmanager
def session_scope():
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    Base.metadata.create_all(bind=get_engine())


def reset_db(database_url: str) -> None:
    configure_engine(database_url)
    Base.metadata.drop_all(bind=get_engine())
    Base.metadata.create_all(bind=get_engine())


def init_db_command() -> None:
    init_db()
    print("database initialized")
