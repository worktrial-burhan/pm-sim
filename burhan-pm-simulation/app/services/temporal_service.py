from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Any

from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.common import RetryPolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from app.domain.models import RunStatus
from app.services.config import get_settings

with workflow.unsafe.imports_passed_through():
    from app.services import simulation_service


RUN_WORKFLOW_PREFIX = "pm-sim-run"
ANALYSIS_WORKFLOW_PREFIX = "pm-sim-analysis"
logger = logging.getLogger(__name__)


def analysis_workflow_id(run_id: str) -> str:
    return f"{ANALYSIS_WORKFLOW_PREFIX}-{run_id}"


def temporal_enabled() -> bool:
    return get_settings().enable_temporal


def run_workflow_id(run_id: str) -> str:
    return f"{RUN_WORKFLOW_PREFIX}-{run_id}"


async def connect_temporal_client() -> Client:
    settings = get_settings()
    last_error: Exception | None = None
    for _ in range(30):
        try:
            return await Client.connect(settings.temporal_address)
        except Exception as exc:  # pragma: no cover - exercised in live runtime, not unit tests
            last_error = exc
            logger.warning("temporal connection attempt failed", extra={"address": settings.temporal_address})
            await asyncio.sleep(1)
    assert last_error is not None
    raise last_error


@activity.defn
def get_run_runtime_state_activity(run_id: str) -> dict[str, Any] | None:
    return simulation_service.get_run_runtime_state(run_id)


@activity.defn
def process_run_tick_activity(run_id: str, tick_token: str) -> list[str]:
    return simulation_service.process_run_tick(run_id, tick_token=tick_token)


@activity.defn
def run_actor_turn_activity(turn_id: str) -> None:
    simulation_service.run_actor_turn(turn_id)


@workflow.defn
class RunWorkflow:
    @workflow.run
    async def run(self, run_id: str, actor_turn_timeout_seconds: int) -> None:
        tick_index = 0
        while True:
            run_state = await workflow.execute_activity(
                get_run_runtime_state_activity,
                run_id,
                start_to_close_timeout=timedelta(seconds=30),
            )
            if run_state is None:
                return

            status = run_state["status"]
            tick_wall_seconds = max(float(run_state["tick_wall_seconds"]), 0.1)
            max_actor_invocations_per_tick = int(run_state["max_actor_invocations_per_tick"])

            if status in {RunStatus.STOPPED.value, RunStatus.COMPLETED.value}:
                return

            if status != RunStatus.RUNNING.value:
                await workflow.sleep(min(tick_wall_seconds, 5.0))
                continue

            tick_index += 1
            turn_ids = await workflow.execute_activity(
                process_run_tick_activity,
                args=[run_id, f"tick-{tick_index}"],
                start_to_close_timeout=timedelta(seconds=60),
            )
            await asyncio.gather(*[
                workflow.execute_activity(
                    run_actor_turn_activity,
                    args=[turn_id],
                    start_to_close_timeout=timedelta(seconds=actor_turn_timeout_seconds),
                )
                for turn_id in turn_ids[:max_actor_invocations_per_tick]
            ])

            await workflow.sleep(tick_wall_seconds)


# ---------------------------------------------------------------------------
# Post-run analysis activities
# ---------------------------------------------------------------------------

@activity.defn
def get_judge_types_for_run_activity(run_id: str) -> list[str]:
    """Resolve run_id -> scenario_id -> list of judge analysis_types."""
    from app.judges.runner import get_judge_analysis_types_for_run
    return get_judge_analysis_types_for_run(run_id)


@activity.defn
def generate_summary_activity(run_id: str) -> None:
    """Generate the narrative summary for a completed run. Idempotent."""
    from app.services.analysis_service import generate_summary
    generate_summary(run_id)


@activity.defn
def run_single_judge_activity(run_id: str, analysis_type: str) -> None:
    """Run a single judge for a completed run. Idempotent."""
    from app.judges.runner import run_single_judge
    run_single_judge(run_id, analysis_type)


@activity.defn
def run_final_score_activity(run_id: str) -> None:
    """Run the final score meta-judge after all other judges complete. Idempotent."""
    from app.judges.runner import run_final_score_judge
    run_final_score_judge(run_id)


# ---------------------------------------------------------------------------
# Analysis workflow: fans out summary + judges as parallel activities
# ---------------------------------------------------------------------------

@workflow.defn
class AnalysisWorkflow:
    """Durable post-run analysis: summary generation + all applicable judges.

    Each judge runs as an independent activity with its own retry policy,
    so one judge failing doesn't block others and transient LLM API errors
    are retried automatically.

    The final score meta-judge runs AFTER all other judges complete,
    since it needs their results as input.
    """

    @workflow.run
    async def run(self, run_id: str) -> None:
        # 1. Resolve which judges apply to this run's scenario
        judge_types: list[str] = await workflow.execute_activity(
            get_judge_types_for_run_activity,
            run_id,
            start_to_close_timeout=timedelta(seconds=30),
        )

        # 2. Retry policy for LLM-backed activities (summary + judges)
        llm_retry = RetryPolicy(
            maximum_attempts=3,
            initial_interval=timedelta(seconds=5),
            maximum_interval=timedelta(seconds=60),
            backoff_coefficient=2.0,
        )

        # 3. Fan out: summary + all judges run in parallel
        tasks = [
            workflow.execute_activity(
                generate_summary_activity,
                run_id,
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=llm_retry,
            ),
        ]
        for analysis_type in judge_types:
            tasks.append(
                workflow.execute_activity(
                    run_single_judge_activity,
                    args=[run_id, analysis_type],
                    start_to_close_timeout=timedelta(minutes=5),
                    retry_policy=llm_retry,
                )
            )

        # 4. Wait for all judges + summary to complete
        await asyncio.gather(*tasks)

        # 5. Run final score meta-judge AFTER all others (needs their results)
        await workflow.execute_activity(
            run_final_score_activity,
            run_id,
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=llm_retry,
        )


# ---------------------------------------------------------------------------
# Workflow launchers
# ---------------------------------------------------------------------------

async def ensure_analysis_workflow(run_id: str) -> None:
    settings = get_settings()
    client = await connect_temporal_client()
    try:
        await client.start_workflow(
            AnalysisWorkflow.run,
            run_id,
            id=analysis_workflow_id(run_id),
            task_queue=settings.temporal_task_queue,
        )
        logger.info("analysis workflow started", extra={"run_id": run_id})
    except WorkflowAlreadyStartedError:
        logger.info("analysis workflow already running", extra={"run_id": run_id})


def ensure_analysis_workflow_sync(run_id: str) -> None:
    asyncio.run(ensure_analysis_workflow(run_id))


async def ensure_run_workflow(run_id: str) -> None:
    settings = get_settings()
    client = await connect_temporal_client()
    try:
        await client.start_workflow(
            RunWorkflow.run,
            args=[run_id, 600],  # 10 min timeout per actor turn
            id=run_workflow_id(run_id),
            task_queue=settings.temporal_task_queue,
        )
        logger.info("temporal workflow started", extra={"run_id": run_id})
    except WorkflowAlreadyStartedError:
        logger.info("temporal workflow already running", extra={"run_id": run_id})
        return


def ensure_run_workflow_sync(run_id: str) -> None:
    asyncio.run(ensure_run_workflow(run_id))


async def run_temporal_worker() -> None:
    settings = get_settings()
    client = await connect_temporal_client()
    activity_executor = ThreadPoolExecutor(max_workers=32)
    worker = Worker(
        client,
        task_queue=settings.temporal_task_queue,
        workflows=[RunWorkflow, AnalysisWorkflow],
        activities=[
            get_run_runtime_state_activity,
            process_run_tick_activity,
            run_actor_turn_activity,
            get_judge_types_for_run_activity,
            generate_summary_activity,
            run_single_judge_activity,
            run_final_score_activity,
        ],
        activity_executor=activity_executor,
        workflow_runner=UnsandboxedWorkflowRunner(),
    )
    await worker.run()


def run_temporal_worker_sync() -> None:
    asyncio.run(run_temporal_worker())
