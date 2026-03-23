"""Judge runner: executes all applicable judges for a completed run."""
from __future__ import annotations

import logging
from typing import Any

from app.domain.models import new_id
from app.judges.registry import get_judges_for_scenario
from app.services.config import get_settings
from app.services.db import RunAnalysisRecord, session_scope

logger = logging.getLogger(__name__)

JUDGE_MODEL = "claude-sonnet-4-6"


def collect_run_data(run_id: str) -> dict[str, Any]:
    """Gather all run data needed by judges and summary generation.

    Single canonical data-collection function — used by both judges and
    the summary generator so we never duplicate this logic.
    Includes full hawkeye context: scenario metadata, personas, hidden truth.
    """
    from app.services import event_store, state_store, trace_store

    with session_scope() as session:
        run = state_store.get_run(session, run_id)
        if run is None:
            return {}
        actors = state_store.list_actors(session, run_id)
        actor_lookup = {a.id: a.name for a in actors}
        events = event_store.list_events(session, run_id=run_id, limit=None)
        traces = trace_store.list_run_traces(session, run_id=run_id, limit=None)
        world_objects = state_store.list_world_objects(session, run_id)

        config = run.config_json or {}
        assignment = config.get("assignment", {})

        # Hawkeye context — full scenario metadata for judges
        scenario_context: dict[str, Any] = {}
        try:
            from app.scenarios.loader import load_scenario
            bundle = load_scenario(run.scenario_id)
            metadata = bundle["metadata"]
            actors_file = bundle["actors"]
            rubric = bundle["rubric"]
            scenario_context = {
                "scenario_name": metadata.name,
                "scenario_description": metadata.description,
                "deadline_days": metadata.deadline_days,
                "mission": metadata.mission.model_dump() if metadata.mission else {},
                "actor_personas": [
                    {
                        "name": a.name,
                        "role": a.role,
                        "team": a.team,
                        "character_prompt": a.character_prompt,
                        "goals": dict(a.goals) if a.goals else {},
                    }
                    for a in actors_file.actors
                ],
                "hidden_truth": dict(rubric.hidden_truth) if rubric.hidden_truth else {},
                "rubric_notes": list(rubric.notes) if rubric.notes else [],
            }
        except Exception:
            logger.debug("could not load scenario context for %s", run.scenario_id, exc_info=True)

        return {
            "run_id": run_id,
            "scenario_id": run.scenario_id,
            "status": run.status,
            "start_sim_time": config.get("start_sim_time", run.current_sim_time.isoformat()),
            "end_sim_time": run.current_sim_time.isoformat(),
            "assignment": assignment,
            "scenario_context": scenario_context,
            "actors": [
                {"id": a.id, "name": a.name, "role": a.role, "team": a.team}
                for a in actors
            ],
            "events": [
                {
                    "sim_time": e.sim_time.isoformat(),
                    "event_type": e.event_type,
                    "actor": actor_lookup.get(e.actor_id, e.actor_id),
                    "data": e.data_json,
                }
                for e in sorted(events, key=lambda e: (e.sim_time, e.seq))
            ],
            "traces": [
                {
                    "sim_time": t.sim_time.isoformat(),
                    "trace_type": t.trace_type,
                    "actor": actor_lookup.get(t.actor_id, t.actor_id),
                    "data": t.data_json,
                }
                for t in sorted(traces, key=lambda t: t.sim_time)
            ],
            "final_world_state": [
                {
                    "id": obj.id,
                    "kind": obj.kind,
                    "title": obj.title,
                    "state": obj.state_json,
                }
                for obj in world_objects
            ],
        }


def get_judge_analysis_types_for_run(run_id: str) -> list[str]:
    """Get list of judge analysis_types applicable to a run's scenario."""
    from app.services import state_store

    with session_scope() as session:
        run = state_store.get_run(session, run_id)
        if run is None:
            return []
        specs = get_judges_for_scenario(run.scenario_id)
        return [spec.analysis_type for spec in specs]


def run_single_judge(run_id: str, analysis_type: str) -> None:
    """Run a single judge by analysis_type. Idempotent -- skips if result already exists."""
    from sqlalchemy import select
    from app.services import state_store

    # Resolve judge spec
    with session_scope() as session:
        run = state_store.get_run(session, run_id)
        if run is None:
            logger.error("run not found for judge", extra={"run_id": run_id})
            return
        scenario_id = run.scenario_id

    specs = get_judges_for_scenario(scenario_id)
    spec = next((s for s in specs if s.analysis_type == analysis_type), None)
    if spec is None:
        logger.error(
            "judge spec not found",
            extra={"analysis_type": analysis_type, "scenario_id": scenario_id},
        )
        return

    # Idempotency check
    with session_scope() as session:
        existing = session.scalar(
            select(RunAnalysisRecord).where(
                RunAnalysisRecord.run_id == run_id,
                RunAnalysisRecord.analysis_type == analysis_type,
            )
        )
        if existing:
            logger.info("judge %s already completed for run %s", spec.name, run_id)
            return

    # Collect data and evaluate
    run_data = collect_run_data(run_id)
    if not run_data:
        logger.error("cannot collect run data for judge", extra={"run_id": run_id})
        return

    judge = spec.judge_class()
    result = judge.evaluate(run_data)

    with session_scope() as session:
        record = RunAnalysisRecord(
            id=new_id("analysis"),
            run_id=run_id,
            analysis_type=result.analysis_type,
            content_json={
                "judge_name": result.judge_name,
                "narrative": result.narrative,
                "evidence": result.evidence,
            },
        )
        session.add(record)

    logger.info("judge %s completed for run %s", spec.name, run_id)


def run_final_score_judge(run_id: str) -> None:
    """Run the final score meta-judge. Must be called AFTER all other judges complete.

    Collects all existing judge results and passes them to the final score judge.
    """
    from sqlalchemy import select
    from app.judges.registry import get_final_score_spec

    spec = get_final_score_spec()

    # Idempotency check
    with session_scope() as session:
        existing = session.scalar(
            select(RunAnalysisRecord).where(
                RunAnalysisRecord.run_id == run_id,
                RunAnalysisRecord.analysis_type == spec.analysis_type,
            )
        )
        if existing:
            logger.info("final score already exists for run %s", run_id)
            return

    # Collect run data
    run_data = collect_run_data(run_id)
    if not run_data:
        logger.error("cannot collect run data for final score", extra={"run_id": run_id})
        return

    # Load all existing judge results
    with session_scope() as session:
        stmt = select(RunAnalysisRecord).where(
            RunAnalysisRecord.run_id == run_id,
            RunAnalysisRecord.analysis_type.like("judge_%"),
            RunAnalysisRecord.analysis_type != "judge_final_score",
        )
        records = list(session.scalars(stmt))
        judge_results = {
            r.analysis_type: r.content_json
            for r in records
        }

    run_data["judge_results"] = judge_results

    judge = spec.judge_class()
    result = judge.evaluate(run_data)

    with session_scope() as session:
        record = RunAnalysisRecord(
            id=new_id("analysis"),
            run_id=run_id,
            analysis_type=result.analysis_type,
            content_json={
                "judge_name": result.judge_name,
                "narrative": result.narrative,
                "evidence": result.evidence,
            },
        )
        session.add(record)

    logger.info("final score judge completed for run %s", run_id)


def run_judges_for_run(run_id: str) -> None:
    """Run all applicable judges sequentially, then final score. Used by the non-Temporal local fallback."""
    analysis_types = get_judge_analysis_types_for_run(run_id)
    for analysis_type in analysis_types:
        try:
            run_single_judge(run_id, analysis_type)
        except Exception:
            logger.exception("judge %s failed for run %s", analysis_type, run_id)

    # Run final score after all others
    try:
        run_final_score_judge(run_id)
    except Exception:
        logger.exception("final score judge failed for run %s", run_id)


def call_judge_llm(prompt: str) -> str:
    """Call Claude via the Anthropic SDK for judge evaluation."""
    import anthropic

    settings = get_settings()
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    collected: list[str] = []
    with client.messages.stream(
        model=JUDGE_MODEL,
        max_tokens=16384,
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        for text in stream.text_stream:
            collected.append(text)
    return "".join(collected)
