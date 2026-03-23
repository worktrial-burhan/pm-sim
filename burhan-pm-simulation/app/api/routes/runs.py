from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_db_session
from app.api.presenters import serialize_run
from app.services import orchestration_service, run_service, state_store
from app.services.run_service import InvalidRunTransition, RunNotFoundError
from app.services.temporal_service import temporal_enabled

router = APIRouter(prefix="/api/runs", tags=["runs"])


class CreateRunRequest(BaseModel):
    scenario_id: str
    controller_profile: str | None = None
    controller_overrides: dict[str, str] = Field(default_factory=dict)
    model: str | None = None


class UpdateTimeScaleRequest(BaseModel):
    multiplier: int = Field(..., ge=1)


def _require_run(session: Session, run_id: str):
    run = state_store.get_run(session, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return run


@router.get("")
def list_runs(session: Session = Depends(get_db_session)) -> list[dict[str, Any]]:
    return [serialize_run(run) for run in state_store.list_runs(session)]


@router.post("")
def create_run(
    payload: CreateRunRequest,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    try:
        run = run_service.create_run(
            session,
            scenario_id=payload.scenario_id,
            controller_profile=payload.controller_profile,
            controller_overrides=payload.controller_overrides,
            model=payload.model,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    return serialize_run(run)


@router.get("/{run_id}")
def get_run(run_id: str, session: Session = Depends(get_db_session)) -> dict[str, Any]:
    return serialize_run(_require_run(session, run_id))


@router.post("/{run_id}/start")
def start_run(run_id: str, session: Session = Depends(get_db_session)) -> dict[str, Any]:
    if temporal_enabled():
        try:
            run = orchestration_service.transition_run_with_temporal_sync(
                run_id=run_id,
                transition="start",
            )
        except RunNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except InvalidRunTransition as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail="failed to attach Temporal workflow") from exc
        return serialize_run(run)
    try:
        run = run_service.start_run(session, run_id=run_id)
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidRunTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.commit()
    return serialize_run(run)


@router.post("/{run_id}/pause")
def pause_run(run_id: str, session: Session = Depends(get_db_session)) -> dict[str, Any]:
    try:
        run = run_service.pause_run(session, run_id=run_id)
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidRunTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.commit()
    return serialize_run(run)


@router.post("/{run_id}/resume")
def resume_run(run_id: str, session: Session = Depends(get_db_session)) -> dict[str, Any]:
    if temporal_enabled():
        try:
            run = orchestration_service.transition_run_with_temporal_sync(
                run_id=run_id,
                transition="resume",
            )
        except RunNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except InvalidRunTransition as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail="failed to attach Temporal workflow") from exc
        return serialize_run(run)
    try:
        run = run_service.resume_run(session, run_id=run_id)
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidRunTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.commit()
    return serialize_run(run)


@router.post("/{run_id}/stop")
def stop_run(run_id: str, session: Session = Depends(get_db_session)) -> dict[str, Any]:
    try:
        run = run_service.stop_run(session, run_id=run_id)
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidRunTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.commit()
    return serialize_run(run)


@router.post("/{run_id}/time-scale")
def update_run_time_scale(
    run_id: str,
    payload: UpdateTimeScaleRequest,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    try:
        run = run_service.update_run_time_scale(session, run_id=run_id, multiplier=payload.multiplier)
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidRunTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.commit()
    return serialize_run(run)
