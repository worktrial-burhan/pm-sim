from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db_session
from app.scenarios.loader import load_scenario
from app.services import state_store
from app.services.db import TriggerRecord

router = APIRouter(prefix="/api/admin/runs/{run_id}", tags=["admin"])


@router.get("/hidden-truth")
def get_hidden_truth(run_id: str, session: Session = Depends(get_db_session)) -> dict:
    run = state_store.get_run(session, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    scenario = load_scenario(run.scenario_id)
    return scenario["rubric"].model_dump()


@router.get("/triggers")
def get_triggers(run_id: str, session: Session = Depends(get_db_session)) -> list[dict]:
    run = state_store.get_run(session, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    stmt = select(TriggerRecord).where(TriggerRecord.run_id == run_id).order_by(
        TriggerRecord.due_sim_time.asc()
    )
    triggers = session.scalars(stmt).all()
    return [
        {
            "id": trigger.id,
            "trigger_type": trigger.trigger_type,
            "due_sim_time": trigger.due_sim_time.isoformat(),
            "actor_id": trigger.actor_id,
            "status": trigger.status,
            "data": trigger.data_json,
        }
        for trigger in triggers
    ]

