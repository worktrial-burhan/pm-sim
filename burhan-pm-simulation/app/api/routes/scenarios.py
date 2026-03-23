from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.scenarios.loader import list_scenarios, load_scenario

router = APIRouter(prefix="/api/scenarios", tags=["scenarios"])


@router.get("")
def get_scenarios() -> list[dict]:
    return list_scenarios()


@router.get("/{scenario_id}")
def get_scenario(scenario_id: str) -> dict:
    try:
        bundle = load_scenario(scenario_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "metadata": bundle["metadata"].model_dump(),
        "actors": bundle["actors"].model_dump(),
        "world": bundle["world"].model_dump(),
        "triggers": bundle["triggers"].model_dump(),
        "rubric": bundle["rubric"].model_dump(),
    }

