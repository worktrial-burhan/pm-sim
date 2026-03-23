from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import psycopg
from dotenv import load_dotenv
from temporalio.api.enums.v1 import WorkflowExecutionStatus
from temporalio.client import Client


ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

API_BASE_URL = "http://localhost:8000"
POSTGRES_DSN = "postgresql://postgres:postgres@localhost:5432/pm_sim"
TEMPORAL_ADDRESS = "localhost:7233"

SCRIPTED_REQUIRED_EVENT_TYPES = {
    "ChatMessageSent",
    "EmailSent",
    "TaskStatusUpdated",
    "DocumentUpdated",
    "MeetingScheduled",
}
DEFAULT_LIVE_CONTROLLER_OVERRIDES = {
    "actor_pm": "claude",
    "actor_sam": "claude",
    "actor_pat": "scripted",
}
ALL_CLAUDE_CONTROLLER_OVERRIDES = {
    "actor_pm": "claude",
    "actor_sam": "claude",
    "actor_pat": "claude",
}


@dataclass
class SmokeSummary:
    scripted_run_id: str
    live_run_id: str
    scripted_event_count: int
    live_event_count: int
    db_counts: dict[str, dict[str, int]]
    workflows: dict[str, str]
    evaluations: dict[str, float]


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a live end-to-end smoke test.")
    parser.add_argument(
        "--boot-stack",
        action="store_true",
        help="Run `docker compose up -d --build` before the smoke test.",
    )
    parser.add_argument(
        "--reset-stack",
        action="store_true",
        help="Run `docker compose down -v --remove-orphans` before booting the stack.",
    )
    parser.add_argument(
        "--all-claude",
        action="store_true",
        help="Use Claude-backed controllers for all scenario actors instead of a mixed live run.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=240.0,
        help="Overall timeout budget for each wait stage.",
    )
    args = parser.parse_args()

    summary = run_live_smoke(
        boot_stack=args.boot_stack,
        reset_stack=args.reset_stack,
        all_claude=args.all_claude,
        timeout_seconds=args.timeout_seconds,
    )
    print(json.dumps(summary.__dict__, indent=2, sort_keys=True))


def run_live_smoke(
    *,
    boot_stack: bool = False,
    reset_stack: bool = False,
    all_claude: bool = False,
    timeout_seconds: float = 240.0,
) -> SmokeSummary:
    if reset_stack:
        _run(["docker", "compose", "down", "-v", "--remove-orphans"])
    if boot_stack:
        _run(["docker", "compose", "up", "-d", "--build"])

    wait_for_http_health(timeout_seconds)
    wait_for_postgres(timeout_seconds)
    asyncio.run(wait_for_temporal(timeout_seconds))

    with httpx.Client(base_url=API_BASE_URL, timeout=10.0, follow_redirects=True) as client:
        scripted_run_id = _exercise_scripted_run(client, timeout_seconds)
        live_run_id = _exercise_live_run(client, timeout_seconds, all_claude=all_claude)

    db_counts = _fetch_db_counts(scripted_run_id, live_run_id)
    workflow_states = asyncio.run(_fetch_workflow_states(scripted_run_id, live_run_id))

    scripted_evaluation = _fetch_evaluation_score(scripted_run_id)
    live_evaluation = _fetch_evaluation_score(live_run_id)

    return SmokeSummary(
        scripted_run_id=scripted_run_id,
        live_run_id=live_run_id,
        scripted_event_count=db_counts[scripted_run_id]["events"],
        live_event_count=db_counts[live_run_id]["events"],
        db_counts=db_counts,
        workflows=workflow_states,
        evaluations={
            scripted_run_id: scripted_evaluation,
            live_run_id: live_evaluation,
        },
    )


def wait_for_http_health(timeout_seconds: float) -> None:
    _wait_until(
        timeout_seconds,
        lambda: _http_ready(),
        "API health endpoint did not become ready",
    )


def wait_for_postgres(timeout_seconds: float) -> None:
    def _ready() -> bool:
        try:
            with psycopg.connect(POSTGRES_DSN) as conn:
                with conn.cursor() as cur:
                    cur.execute("select 1")
                    cur.fetchone()
            return True
        except Exception:
            return False

    _wait_until(timeout_seconds, _ready, "Postgres did not become ready")


async def wait_for_temporal(timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            await Client.connect(TEMPORAL_ADDRESS)
            return
        except Exception as exc:
            last_error = exc
            await asyncio.sleep(1)
    raise RuntimeError(f"Temporal did not become ready: {last_error}")


def _exercise_scripted_run(client: httpx.Client, timeout_seconds: float) -> str:
    run_id = _create_run(client, controller_profile="scripted_demo", controller_overrides={})
    _start_run(client, run_id)

    def _ready() -> bool:
        response = client.get(f"/api/runs/{run_id}/events")
        response.raise_for_status()
        events = response.json()
        event_types = {event["event_type"] for event in events}
        return SCRIPTED_REQUIRED_EVENT_TYPES.issubset(event_types)

    _wait_until(
        timeout_seconds,
        _ready,
        "scripted run did not reach the expected multi-surface event set",
    )

    run_page = client.get(f"/ui/runs/{run_id}")
    run_page.raise_for_status()
    actor_page = client.get(f"/ui/runs/{run_id}/actors/actor_pm")
    actor_page.raise_for_status()
    if "Riley" not in actor_page.text:
        raise RuntimeError("actor detail UI did not render the expected PM card")

    return run_id


def _exercise_live_run(client: httpx.Client, timeout_seconds: float, *, all_claude: bool) -> str:
    controller_overrides = (
        ALL_CLAUDE_CONTROLLER_OVERRIDES if all_claude else DEFAULT_LIVE_CONTROLLER_OVERRIDES
    )
    live_actor_ids = tuple(
        actor_id for actor_id, controller_type in controller_overrides.items() if controller_type == "claude"
    )
    run_id = _create_run(client, controller_overrides=controller_overrides)
    _start_run(client, run_id)

    def _ready() -> bool:
        if not _live_actor_turns_happened(client, run_id, live_actor_ids):
            return False
        if _live_controller_failed(run_id):
            raise RuntimeError("live Claude controller failed; inspect actor traces in the UI")
        return _live_actor_generated_event(run_id, live_actor_ids)

    _wait_until(
        timeout_seconds,
        _ready,
        "live Claude run did not produce actor turns and events in time",
    )

    return run_id


def _live_actor_turns_happened(
    client: httpx.Client,
    run_id: str,
    live_actor_ids: tuple[str, ...],
) -> bool:
    actors_with_turns: set[str] = set()
    for actor_id in live_actor_ids:
        response = client.get(f"/api/runs/{run_id}/actors/{actor_id}")
        response.raise_for_status()
        payload = response.json()
        trace_types = {trace["trace_type"] for trace in payload["traces"]}
        if "controller_request" in trace_types and "controller_response" in trace_types:
            actors_with_turns.add(actor_id)
    return "actor_pm" in actors_with_turns and len(actors_with_turns) >= min(2, len(live_actor_ids))


def _live_controller_failed(run_id: str) -> bool:
    with psycopg.connect(POSTGRES_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select count(*)
                from traces
                where run_id = %s
                  and trace_type = 'system_debug'
                  and data_json->>'message' = 'controller invocation failed'
                """,
                (run_id,),
            )
            return int(cur.fetchone()[0]) > 0


def _live_actor_generated_event(run_id: str, live_actor_ids: tuple[str, ...]) -> bool:
    with psycopg.connect(POSTGRES_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select count(*)
                from events
                where run_id = %s
                  and actor_id = any(%s)
                  and event_type not in (
                    'RunCreated',
                    'ActorCreated',
                    'WorldObjectCreated',
                    'RunStarted',
                    'ClockAdvanced'
                  )
                """,
                (run_id, list(live_actor_ids)),
            )
            return int(cur.fetchone()[0]) > 0


def _fetch_db_counts(*run_ids: str) -> dict[str, dict[str, int]]:
    results: dict[str, dict[str, int]] = {}
    with psycopg.connect(POSTGRES_DSN) as conn:
        with conn.cursor() as cur:
            for run_id in run_ids:
                counts: dict[str, int] = {}
                for table_name in ("events", "traces", "deliveries", "world_objects", "actors"):
                    cur.execute(
                        f"select count(*) from {table_name} where run_id = %s",
                        (run_id,),
                    )
                    counts[table_name] = int(cur.fetchone()[0])
                results[run_id] = counts
    return results


async def _fetch_workflow_states(*run_ids: str) -> dict[str, str]:
    client = await Client.connect(TEMPORAL_ADDRESS)
    states: dict[str, str] = {}
    for run_id in run_ids:
        handle = client.get_workflow_handle(f"pm-sim-run-{run_id}")
        description = await handle.describe()
        states[run_id] = WorkflowExecutionStatus.Name(int(description.status))
    return states


def _fetch_evaluation_score(run_id: str) -> float:
    response = httpx.get(f"{API_BASE_URL}/api/runs/{run_id}/evaluation", timeout=10.0)
    response.raise_for_status()
    return float(response.json()["score"])


def _create_run(
    client: httpx.Client,
    *,
    controller_profile: str = "live_realism",
    controller_overrides: dict[str, str],
) -> str:
    response = client.post(
        "/api/runs",
        json={
            "scenario_id": "smoke_test",
            "controller_profile": controller_profile,
            "controller_overrides": controller_overrides,
        },
    )
    response.raise_for_status()
    return str(response.json()["id"])


def _start_run(client: httpx.Client, run_id: str) -> None:
    response = client.post(f"/api/runs/{run_id}/start")
    response.raise_for_status()


def _http_ready() -> bool:
    try:
        response = httpx.get(f"{API_BASE_URL}/health", timeout=5.0)
        return response.status_code == 200
    except Exception:
        return False


def _wait_until(timeout_seconds: float, predicate, error_message: str) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(1)
    raise RuntimeError(error_message)


def _run(command: list[str]) -> None:
    subprocess.run(command, cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
