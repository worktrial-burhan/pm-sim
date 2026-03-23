"""Post-run analysis: LLM-generated story summary and qualitative judges.

Execution model:
- Temporal enabled  -> starts an AnalysisWorkflow (durable, parallel, retryable)
- Temporal disabled -> fans out summary + judges via a bounded ThreadPoolExecutor
"""
from __future__ import annotations

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from app.domain.models import new_id
from app.services.config import get_settings
from app.services.db import RunAnalysisRecord, session_scope

logger = logging.getLogger(__name__)

ANALYSIS_MODEL = "claude-sonnet-4-6"

# Bounded thread pool for non-Temporal parallel analysis.
# Shared across all runs — provides natural backpressure.
_analysis_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="analysis")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def trigger_post_run_analysis(run_id: str) -> None:
    """Fire-and-forget: launch summary + judges for a completed/stopped run.

    Always non-blocking.  Dispatches via a short-lived thread so that callers
    inside a running asyncio event loop (e.g. a Temporal activity executing
    finish_assignment) never hit the "asyncio.run() cannot be called from a
    running event loop" error.
    """
    thread = threading.Thread(
        target=_dispatch_analysis,
        args=(run_id,),
        daemon=True,
        name=f"analysis-dispatch-{run_id}",
    )
    thread.start()


def _dispatch_analysis(run_id: str) -> None:
    """Route to Temporal or local execution."""
    from app.services.temporal_service import temporal_enabled

    if temporal_enabled():
        try:
            from app.services.temporal_service import ensure_analysis_workflow_sync
            ensure_analysis_workflow_sync(run_id)
        except Exception:
            logger.exception(
                "failed to start analysis workflow, falling back to local execution",
                extra={"run_id": run_id},
            )
            _run_analysis_parallel(run_id)
    else:
        _run_analysis_parallel(run_id)


# ---------------------------------------------------------------------------
# Non-Temporal parallel path
# ---------------------------------------------------------------------------

def _run_analysis_parallel(run_id: str) -> None:
    """Run summary + all judges in parallel, then final score."""
    from app.judges.runner import get_judge_analysis_types_for_run, run_single_judge

    judge_types = get_judge_analysis_types_for_run(run_id)

    # Phase 1: summary + all regular judges in parallel
    futures = [_analysis_pool.submit(_safe_generate_summary, run_id)]
    for analysis_type in judge_types:
        futures.append(
            _analysis_pool.submit(_safe_run_judge, run_id, analysis_type)
        )

    # Wait for all regular judges to finish
    for future in as_completed(futures):
        try:
            future.result()
        except Exception:
            pass

    # Phase 2: final score meta-judge (needs all other results)
    try:
        from app.judges.runner import run_final_score_judge
        run_final_score_judge(run_id)
    except Exception:
        logger.exception("final score judge failed", extra={"run_id": run_id})


def _safe_generate_summary(run_id: str) -> None:
    try:
        generate_summary(run_id)
    except Exception:
        logger.exception("failed to generate summary", extra={"run_id": run_id})


def _safe_run_judge(run_id: str, analysis_type: str) -> None:
    try:
        from app.judges.runner import run_single_judge
        run_single_judge(run_id, analysis_type)
    except Exception:
        logger.exception(
            "judge %s failed", analysis_type, extra={"run_id": run_id}
        )


# ---------------------------------------------------------------------------
# Summary generation (also used as a Temporal activity)
# ---------------------------------------------------------------------------

def generate_summary(run_id: str) -> None:
    """Generate narrative summary for a completed run.  Idempotent."""
    from sqlalchemy import select

    with session_scope() as session:
        existing = session.scalar(
            select(RunAnalysisRecord).where(
                RunAnalysisRecord.run_id == run_id,
                RunAnalysisRecord.analysis_type == "summary",
            )
        )
        if existing:
            logger.info("summary already exists for run %s", run_id)
            return

    from app.judges.runner import collect_run_data

    data = collect_run_data(run_id)
    if not data:
        return

    prompt = f"""You are a narrator writing a story about what happened during a workplace simulation.

Below is the complete data from a PM simulation run. Write a clean, engaging narrative of what happened \u2014 like you are telling the story of this PM's day to someone who wasn't there.

Rules:
- Write in past tense, third person.
- Use people's first names.
- Include actual message content and decisions, not just "they communicated."
- Show the internal reasoning and thoughts where they reveal something interesting.
- Do NOT mention any simulation mechanics (triggers, events, ticks, sim time, tool calls, etc.).
- It should read like a story about real people at a real company.
- No headers or sections. Just flowing prose.

Run data:
{json.dumps(data, indent=2, default=str)}"""

    narrative = _call_llm(prompt)

    with session_scope() as session:
        record = RunAnalysisRecord(
            id=new_id("analysis"),
            run_id=run_id,
            analysis_type="summary",
            content_json={"narrative": narrative},
        )
        session.add(record)


# ---------------------------------------------------------------------------
# LLM client
# ---------------------------------------------------------------------------

def _call_llm(prompt: str) -> str:
    """Call Claude via the Anthropic SDK with streaming."""
    import anthropic

    settings = get_settings()
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    collected: list[str] = []
    with client.messages.stream(
        model=ANALYSIS_MODEL,
        max_tokens=16384,
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        for text in stream.text_stream:
            collected.append(text)
    return "".join(collected)
