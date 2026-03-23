from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db_session
from app.api.presenters import serialize_run
from app.api.view_models import build_actor_cards, build_actor_detail_payload, build_clean_activity, build_run_activity, build_run_events
from app.judges.registry import get_judges_for_scenario
from app.scenarios.loader import list_scenarios, load_scenario
from app.services import controller_profiles, orchestration_service, run_service, state_store
from app.services.run_service import list_model_choices, DEFAULT_MODEL
from app.services.config import get_settings
from app.services.db import RunAnalysisRecord
from app.services.run_service import InvalidRunTransition, RunNotFoundError
from app.services.temporal_service import temporal_enabled

templates = Jinja2Templates(directory=str(get_settings().project_root / "app/ui/templates"))
router = APIRouter(tags=["ui"])


@router.get("/")
def home(request: Request, session: Session = Depends(get_db_session)):
    scenarios = list_scenarios()
    # Build a lookup from scenario_id to human-readable name
    scenario_names = {}
    for s in scenarios:
        scenario_names[s["id"]] = s.get("name", s["id"])
    runs_raw = state_store.list_runs(session)
    runs = []
    for run in runs_raw:
        data = serialize_run(run)
        data["scenario_name"] = scenario_names.get(run.scenario_id, run.scenario_id)
        runs.append(data)
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "scenarios": scenarios,
            "model_choices": list_model_choices(),
            "default_model": DEFAULT_MODEL,
            "runs": runs,
            "refresh_seconds": get_settings().ui_refresh_seconds,
        },
    )


@router.post("/ui/runs")
def create_and_start_run_from_form(
    scenario_id: str = Form(...),
    model: str = Form(DEFAULT_MODEL),
    session: Session = Depends(get_db_session),
):
    try:
        run = run_service.create_run(
            session,
            scenario_id=scenario_id,
            model=model,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()

    # Auto-start: create and run in one step
    run_id = run.id
    if temporal_enabled():
        try:
            orchestration_service.transition_run_with_temporal_sync(
                run_id=run_id, transition="start",
            )
        except Exception:
            from app.services.db import session_scope
            with session_scope() as start_session:
                run_service.start_run(start_session, run_id=run_id)
    else:
        from app.services.db import session_scope
        with session_scope() as start_session:
            run_service.start_run(start_session, run_id=run_id)

    return RedirectResponse(url=f"/ui/runs/{run_id}", status_code=303)


@router.post("/ui/runs/{run_id}/start")
def start_run_from_ui(run_id: str, session: Session = Depends(get_db_session)):
    if temporal_enabled():
        try:
            orchestration_service.transition_run_with_temporal_sync(
                run_id=run_id,
                transition="start",
            )
        except RunNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except InvalidRunTransition as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail="failed to attach Temporal workflow") from exc
        return RedirectResponse(url=f"/ui/runs/{run_id}", status_code=303)
    try:
        run_service.start_run(session, run_id=run_id)
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidRunTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.commit()
    return RedirectResponse(url=f"/ui/runs/{run_id}", status_code=303)


@router.post("/ui/runs/{run_id}/pause")
def pause_run_from_ui(run_id: str, session: Session = Depends(get_db_session)):
    try:
        run_service.pause_run(session, run_id=run_id)
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidRunTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.commit()
    return RedirectResponse(url=f"/ui/runs/{run_id}", status_code=303)


@router.get("/ui/runs/{run_id}")
def run_detail(request: Request, run_id: str, session: Session = Depends(get_db_session)):
    run = state_store.get_run(session, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")

    # Fetch analysis records if the run is done
    summary = None
    judge_results: list[RunAnalysisRecord] = []
    if run.status in {"completed", "stopped"}:
        summary = _get_analysis(session, run_id, "summary")
        # Load all judge results
        stmt = select(RunAnalysisRecord).where(
            RunAnalysisRecord.run_id == run_id,
            RunAnalysisRecord.analysis_type.like("judge_%"),
        )
        judge_results = list(session.scalars(stmt))

    initial_config = run.config_json or {}

    # Load scenario metadata for situation/personas panels
    scenario_description = ""
    actor_personas: list[dict] = []
    judge_specs: list[dict] = []
    try:
        bundle = load_scenario(run.scenario_id)
        metadata = bundle["metadata"]
        scenario_description = metadata.description
        actors_file = bundle["actors"]
        actor_personas = [
            {
                "name": a.name,
                "role": a.role,
                "team": a.team,
                "character_prompt": a.character_prompt,
                "goals": dict(a.goals) if a.goals else {},
            }
            for a in actors_file.actors
        ]
        specs = get_judges_for_scenario(run.scenario_id)
        judge_specs = [
            {"name": s.name, "description": s.description}
            for s in specs
        ]
    except FileNotFoundError:
        pass

    return templates.TemplateResponse(
        request,
        "run_detail.html",
        {
            "run": serialize_run(run),
            "actors": build_actor_cards(session, run_id),
            "activity_entries": build_clean_activity(session, run_id, run.current_sim_time.isoformat()),
            "initial_config": initial_config,
            "summary": summary,
            "judge_results": judge_results,
            "scenario_description": scenario_description,
            "actor_personas": actor_personas,
            "judge_specs": judge_specs,
            "refresh_seconds": get_settings().ui_refresh_seconds,
        },
    )


@router.post("/ui/runs/{run_id}/resume")
def resume_run_from_ui(run_id: str, session: Session = Depends(get_db_session)):
    if temporal_enabled():
        try:
            orchestration_service.transition_run_with_temporal_sync(
                run_id=run_id,
                transition="resume",
            )
        except RunNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except InvalidRunTransition as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail="failed to attach Temporal workflow") from exc
        return RedirectResponse(url=f"/ui/runs/{run_id}", status_code=303)
    try:
        run_service.resume_run(session, run_id=run_id)
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidRunTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.commit()
    return RedirectResponse(url=f"/ui/runs/{run_id}", status_code=303)


@router.post("/ui/runs/{run_id}/time-scale")
def update_run_time_scale_from_ui(
    run_id: str,
    time_scale_multiplier: int = Form(...),
    session: Session = Depends(get_db_session),
):
    try:
        run_service.update_run_time_scale(session, run_id=run_id, multiplier=time_scale_multiplier)
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidRunTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.commit()
    return RedirectResponse(url=f"/ui/runs/{run_id}", status_code=303)


@router.get("/ui/runs/{run_id}/actors/{actor_id}")
def actor_detail(
    request: Request,
    run_id: str,
    actor_id: str,
    session: Session = Depends(get_db_session),
):
    run = state_store.get_run(session, run_id)
    try:
        detail = build_actor_detail_payload(session, run_id=run_id, actor_id=actor_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return templates.TemplateResponse(
        request,
        "actor_detail.html",
        {
            "run": serialize_run(run),
            "actor": detail["actor"],
            "assignment": detail["assignment"],
            "observations": detail["observations"],
            "commitments": detail["commitments"],
            "inbox": detail["inbox"],
            "visible_objects": detail["visible_objects"],
            "work_availability": detail["work_availability"],
            "traces": detail["traces"],
            "refresh_seconds": get_settings().ui_refresh_seconds,
        },
    )



def _get_analysis(session: Session, run_id: str, analysis_type: str) -> RunAnalysisRecord | None:
    stmt = select(RunAnalysisRecord).where(
        RunAnalysisRecord.run_id == run_id,
        RunAnalysisRecord.analysis_type == analysis_type,
    )
    return session.scalar(stmt)
