from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db_session
from app.api.view_models import build_run_activity, build_run_events
from app.services import state_store

router = APIRouter(prefix="/api/runs/{run_id}", tags=["events"])


@router.get("/events")
def get_run_events(run_id: str, session: Session = Depends(get_db_session)) -> list[dict]:
    run = state_store.get_run(session, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return build_run_events(session, run_id, limit=200)


@router.get("/activity")
def get_run_activity(run_id: str, session: Session = Depends(get_db_session)) -> list[dict]:
    run = state_store.get_run(session, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return build_run_activity(session, run_id, event_limit=200, trace_limit=200)
