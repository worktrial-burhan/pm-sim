from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db_session
from app.services import evaluation_service, state_store

router = APIRouter(prefix="/api/runs/{run_id}", tags=["evaluation"])


@router.get("/evaluation")
def get_evaluation(run_id: str, session: Session = Depends(get_db_session)) -> dict:
    run = state_store.get_run(session, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return evaluation_service.compute_evaluation(session, run_id=run_id)
