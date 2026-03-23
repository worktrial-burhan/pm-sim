from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy.orm import Session

from app.domain.events import DomainEvent
from app.domain.models import OrchestrationStatus, RunStatus
from app.scenarios.compiler import compile_scenario
from app.services import analysis_service, controller_profiles
from app.services import event_store, state_store

logger = logging.getLogger(__name__)

ALLOWED_TIME_SCALE_MULTIPLIERS = {1, 10, 100}


class RunNotFoundError(ValueError):
    pass


class InvalidRunTransition(ValueError):
    pass


def get_time_scale_multiplier(run) -> int:
    raw = ((getattr(run, "config_json", {}) or {}).get("time_scale_multiplier"))
    try:
        multiplier = int(raw)
    except (TypeError, ValueError):
        multiplier = 1
    return multiplier if multiplier in ALLOWED_TIME_SCALE_MULTIPLIERS else 1


def effective_tick_wall_seconds(run) -> float:
    return max(float(run.tick_wall_seconds) / float(get_time_scale_multiplier(run)), 0.01)


ALLOWED_MODELS = {
    "claude-sonnet-4-6": "Sonnet 4.6",
    "claude-opus-4-6": "Opus 4.6",
}
DEFAULT_MODEL = "claude-sonnet-4-6"


def list_model_choices() -> list[dict[str, str]]:
    return [{"id": model_id, "label": label} for model_id, label in ALLOWED_MODELS.items()]


def create_run(
    session: Session,
    *,
    scenario_id: str,
    controller_profile: str | None = None,
    controller_overrides: dict[str, str] | None = None,
    model: str | None = None,
):
    resolved_overrides = controller_profiles.resolve_controller_overrides(
        scenario_id=scenario_id,
        controller_profile=controller_profile,
        explicit_overrides=controller_overrides,
    )
    resolved_model = (model or DEFAULT_MODEL).strip()
    if resolved_model not in ALLOWED_MODELS:
        raise ValueError(f"unsupported model: {resolved_model}. Choose from: {', '.join(ALLOWED_MODELS)}")
    logger.info("creating run", extra={"scenario_id": scenario_id, "controller_profile": controller_profile, "model": resolved_model})
    return compile_scenario(
        session,
        scenario_id=scenario_id,
        controller_overrides=resolved_overrides,
        model=resolved_model,
    )


def update_run_time_scale(session: Session, *, run_id: str, multiplier: int):
    run = _require_run(session, run_id)
    if run.status in {RunStatus.STOPPED.value, RunStatus.COMPLETED.value}:
        raise InvalidRunTransition("time scale can only be changed for paused or running runs")
    if multiplier not in ALLOWED_TIME_SCALE_MULTIPLIERS:
        raise InvalidRunTransition(
            f"time scale must be one of {sorted(ALLOWED_TIME_SCALE_MULTIPLIERS)}"
        )
    state_store.update_run_config(session, run_id, {"time_scale_multiplier": multiplier})
    return state_store.get_run(session, run_id)


def start_run(session: Session, *, run_id: str):
    run = _require_run(session, run_id)
    _validate_start(run)
    _activate_run(session, run_id=run_id, event_type="RunStarted")
    logger.info("run started", extra={"run_id": run_id})
    return state_store.get_run(session, run_id)


def pause_run(session: Session, *, run_id: str):
    run = _require_run(session, run_id)
    if run.status != RunStatus.RUNNING.value:
        raise InvalidRunTransition("only running runs can be paused")
    state_store.update_run_status(session, run_id, RunStatus.PAUSED.value, now=run.current_sim_time)
    event_store.append_event(
        session,
        run_id=run_id,
        sim_time=run.current_sim_time,
        event=DomainEvent(event_type="RunPaused", visibility={"scope": "admin"}),
    )
    logger.info("run paused", extra={"run_id": run_id})
    return run


def resume_run(session: Session, *, run_id: str):
    run = _require_run(session, run_id)
    _validate_resume(run)
    _activate_run(session, run_id=run_id, event_type="RunResumed")
    logger.info("run resumed", extra={"run_id": run_id})
    return state_store.get_run(session, run_id)


def stop_run(session: Session, *, run_id: str):
    run = _require_run(session, run_id)
    if run.status not in {RunStatus.RUNNING.value, RunStatus.PAUSED.value}:
        raise InvalidRunTransition("only running or paused runs can be stopped")
    state_store.update_run_status(session, run_id, RunStatus.STOPPED.value, now=run.current_sim_time)
    event_store.append_event(
        session,
        run_id=run_id,
        sim_time=run.current_sim_time,
        event=DomainEvent(event_type="RunStopped", visibility={"scope": "admin"}),
    )
    logger.info("run stopped", extra={"run_id": run_id})
    analysis_service.trigger_post_run_analysis(run_id)
    return run


def begin_orchestration_attach(
    session: Session,
    *,
    run_id: str,
    transition: str,
    workflow_id: str,
):
    run = _require_run(session, run_id)
    if transition == "start":
        _validate_start(run)
    elif transition == "resume":
        _validate_resume(run)
    else:
        raise ValueError(f"unsupported transition: {transition}")

    state_store.update_run_orchestration(
        session,
        run_id,
        orchestration_status=OrchestrationStatus.ATTACHING.value,
        orchestration_workflow_id=workflow_id,
        orchestration_error=None,
    )
    return state_store.get_run(session, run_id)


def finalize_orchestration_attach(
    session: Session,
    *,
    run_id: str,
    transition: str,
):
    run = _require_run(session, run_id)
    if transition == "start":
        _validate_start(run)
        event_type = "RunStarted"
    elif transition == "resume":
        _validate_resume(run)
        event_type = "RunResumed"
    else:
        raise ValueError(f"unsupported transition: {transition}")

    _activate_run(
        session,
        run_id=run_id,
        event_type=event_type,
        orchestration_status=OrchestrationStatus.ATTACHED.value,
        orchestration_workflow_id=run.orchestration_workflow_id,
    )
    return state_store.get_run(session, run_id)


def fail_orchestration_attach(session: Session, *, run_id: str, error: str):
    run = _require_run(session, run_id)
    logger.error("orchestration attach failed", extra={"run_id": run_id, "error": error})
    state_store.update_run_orchestration(
        session,
        run_id,
        orchestration_status=OrchestrationStatus.ERROR.value,
        orchestration_workflow_id=run.orchestration_workflow_id,
        orchestration_error=error,
    )
    return state_store.get_run(session, run_id)


def advance_clock(session: Session, *, run_id: str):
    run = _require_run(session, run_id)
    next_sim_time = run.current_sim_time + timedelta(seconds=run.tick_sim_seconds)
    state_store.update_run_time(session, run_id, next_sim_time)
    event_store.append_event(
        session,
        run_id=run_id,
        sim_time=next_sim_time,
        event=DomainEvent(
            event_type="ClockAdvanced",
            visibility={"scope": "admin"},
            data={"current_sim_time": next_sim_time.isoformat()},
        ),
    )
    return state_store.get_run(session, run_id)


def _require_run(session: Session, run_id: str):
    run = state_store.get_run(session, run_id)
    if run is None:
        raise RunNotFoundError(f"run not found: {run_id}")
    return run


def _validate_start(run) -> None:
    if run.status == RunStatus.STOPPED.value:
        raise InvalidRunTransition("stopped runs cannot be started again")
    if run.status == RunStatus.RUNNING.value:
        raise InvalidRunTransition("run is already running")
    if run.status == RunStatus.COMPLETED.value:
        raise InvalidRunTransition("completed runs cannot be started again")
    if run.status == RunStatus.PAUSED.value and run.started_at is not None:
        raise InvalidRunTransition("started runs must be resumed, not started")


def _validate_resume(run) -> None:
    if run.status != RunStatus.PAUSED.value:
        raise InvalidRunTransition("only paused runs can be resumed")
    if run.started_at is None:
        raise InvalidRunTransition("run has not been started yet")


def _activate_run(
    session: Session,
    *,
    run_id: str,
    event_type: str,
    orchestration_status: str = OrchestrationStatus.ATTACHED.value,
    orchestration_workflow_id: str | None = None,
) -> None:
    run = _require_run(session, run_id)
    state_store.update_run_status(session, run_id, RunStatus.RUNNING.value, now=run.current_sim_time)
    state_store.update_run_orchestration(
        session,
        run_id,
        orchestration_status=orchestration_status,
        orchestration_workflow_id=orchestration_workflow_id,
        orchestration_error=None,
    )
    event_store.append_event(
        session,
        run_id=run_id,
        sim_time=run.current_sim_time,
        event=DomainEvent(event_type=event_type, visibility={"scope": "admin"}),
    )
