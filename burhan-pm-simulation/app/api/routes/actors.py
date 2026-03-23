from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db_session
from app.api.view_models import build_actor_cards, build_actor_detail_payload
from app.services import state_store

router = APIRouter(prefix="/api/runs/{run_id}", tags=["actors"])


@router.get("/actors")
def list_run_actors(run_id: str, session: Session = Depends(get_db_session)) -> list[dict]:
    run = state_store.get_run(session, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return build_actor_cards(session, run_id)


@router.get("/actors/{actor_id}")
def get_actor_detail(run_id: str, actor_id: str, session: Session = Depends(get_db_session)) -> dict:
    try:
        return build_actor_detail_payload(session, run_id=run_id, actor_id=actor_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
