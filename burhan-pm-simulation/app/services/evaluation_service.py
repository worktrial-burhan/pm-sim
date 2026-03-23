from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.scenarios.loader import load_scenario
from app.scenarios.schemas import RubricCheck
from app.services.assignment_service import public_assignment
from app.services import state_store
from app.services.checks import evaluate_check
from app.services.db import EvaluationRecord
from app.domain.models import new_id


def compute_evaluation(session: Session, *, run_id: str) -> dict[str, Any]:
    run = state_store.get_run(session, run_id)
    if run is None:
        raise ValueError(f"run not found: {run_id}")

    scenario = load_scenario(run.scenario_id)
    rubric = scenario["rubric"]
    checks = rubric.checks
    assignment = dict((run.config_json or {}).get("assignment") or {})

    details: list[dict[str, Any]] = []
    total_weight = sum(check.weight for check in checks) or 1.0
    earned = 0.0
    for check in checks:
        passed, detail = _evaluate_check(session, run_id=run_id, check=check)
        if passed:
            earned += check.weight
        details.append(
            {
                "id": check.id,
                "label": check.label,
                "type": check.type,
                "weight": check.weight,
                "passed": passed,
                "detail": detail,
            }
        )

    score = round((earned / total_weight) * 100, 2)
    objective_state = _objective_state(run_status=run.status, score=score, assignment=assignment)
    closure_assessment = _closure_assessment(run_status=run.status, score=score, assignment=assignment)
    result = {
        "run_id": run_id,
        "status": "computed",
        "score": score,
        "earned_weight": earned,
        "total_weight": total_weight,
        "assignment": public_assignment(assignment),
        "objective_state": objective_state,
        "closure_assessment": closure_assessment,
        "details": details,
        "hidden_truth": rubric.hidden_truth,
        "notes": rubric.notes,
    }
    _persist_evaluation(session, run_id=run_id, sim_time=run.current_sim_time, result=result)
    return result


def _evaluate_check(session: Session, *, run_id: str, check: RubricCheck) -> tuple[bool, dict[str, Any]]:
    return evaluate_check(session, run_id=run_id, check=check)


def _persist_evaluation(
    session: Session, *, run_id: str, sim_time: datetime, result: dict[str, Any]
) -> None:
    record = EvaluationRecord(
        id=new_id("eval"),
        run_id=run_id,
        checkpoint_name="on_demand",
        sim_time=sim_time,
        score_json={
            "score": result["score"],
            "earned_weight": result["earned_weight"],
            "total_weight": result["total_weight"],
            "objective_state": result["objective_state"],
        },
        rationale_json={
            "details": result["details"],
            "notes": result["notes"],
            "closure_assessment": result["closure_assessment"],
        },
    )
    session.add(record)
    session.flush()


def _objective_state(*, run_status: str, score: float, assignment: dict[str, Any]) -> str:
    if assignment.get("end_reason") == "completed_by_pm":
        if score >= 80:
            return "achieved"
        if score >= 50:
            return "partially_achieved"
        return "premature_finish"
    if run_status == "completed":
        return "completed_without_pm_finish"
    if score >= 80:
        return "on_track_but_open"
    if score >= 50:
        return "in_progress"
    return "at_risk"


def _closure_assessment(*, run_status: str, score: float, assignment: dict[str, Any]) -> dict[str, Any]:
    if assignment.get("end_reason") == "completed_by_pm":
        if score >= 80:
            judgment = "pm_finished_at_a_reasonable_point"
        elif score >= 50:
            judgment = "pm_finished_with_partial_progress"
        else:
            judgment = "pm_finished_too_early"
        return {
            "judgment": judgment,
            "finish_summary": assignment.get("finish_summary"),
            "remaining_risks": assignment.get("remaining_risks", []),
            "confidence": assignment.get("confidence"),
        }
    if run_status == "completed":
        return {"judgment": "run_completed_without_pm_finish"}
    return {"judgment": "run_still_open"}
