#!/usr/bin/env python3
"""Batch runner: queue and run multiple PM simulation scenarios via the HTTP API.

Usage:
    python scripts/run_scenarios.py --model claude-sonnet-4-6 --speed 10
    python scripts/run_scenarios.py --scenarios smoke_test the_handoff --speed 100
    python scripts/run_scenarios.py --scenarios smoke_test --speed 100 --base-url http://localhost:8000
"""
from __future__ import annotations

import argparse
import sys
import time
from typing import Any

import httpx

ALL_SCENARIOS = [
    "smoke_test",
    "the_handoff",
    "quiet_crisis",
    "the_split",
    "the_retrospective",
]

DEFAULT_BASE_URL = "http://localhost:8000"
DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_SPEED = 10
DEFAULT_CONCURRENCY = 2
POLL_INTERVAL_SECONDS = 5


def create_run(client: httpx.Client, scenario_id: str, model: str) -> dict[str, Any]:
    """Create a new run via the API."""
    resp = client.post(
        "/api/runs",
        json={"scenario_id": scenario_id, "model": model},
    )
    resp.raise_for_status()
    return resp.json()


def start_run(client: httpx.Client, run_id: str) -> dict[str, Any]:
    """Start a run."""
    resp = client.post(f"/api/runs/{run_id}/start")
    resp.raise_for_status()
    return resp.json()


def set_time_scale(client: httpx.Client, run_id: str, multiplier: int) -> dict[str, Any]:
    """Set the time scale multiplier."""
    resp = client.post(
        f"/api/runs/{run_id}/time-scale",
        json={"multiplier": multiplier},
    )
    resp.raise_for_status()
    return resp.json()


def get_run(client: httpx.Client, run_id: str) -> dict[str, Any]:
    """Get run status."""
    resp = client.get(f"/api/runs/{run_id}")
    resp.raise_for_status()
    return resp.json()


def is_terminal(status: str) -> bool:
    """Check if a run status is terminal."""
    return status in {"completed", "stopped", "failed"}


def run_batch(
    base_url: str,
    scenarios: list[str],
    model: str,
    speed: int,
    concurrency: int,
) -> None:
    """Run scenarios in batches with limited concurrency."""
    client = httpx.Client(base_url=base_url, timeout=30.0)

    # Create all runs
    queued: list[dict[str, Any]] = []
    for scenario_id in scenarios:
        print(f"[CREATE] {scenario_id}...", end=" ", flush=True)
        run = create_run(client, scenario_id, model)
        queued.append({"run_id": run["id"], "scenario_id": scenario_id})
        print(f"run_id={run['id']}")

    active: list[dict[str, Any]] = []
    completed: list[dict[str, Any]] = []
    pending = list(queued)

    def start_next() -> None:
        """Start the next pending run if capacity allows."""
        while len(active) < concurrency and pending:
            item = pending.pop(0)
            run_id = item["run_id"]
            scenario_id = item["scenario_id"]
            print(f"[START]  {scenario_id} (run_id={run_id})")
            start_run(client, run_id)
            set_time_scale(client, run_id, speed)
            active.append(item)

    # Start initial batch
    start_next()

    # Poll for completion
    while active:
        time.sleep(POLL_INTERVAL_SECONDS)

        still_active = []
        for item in active:
            run_id = item["run_id"]
            scenario_id = item["scenario_id"]
            run = get_run(client, run_id)
            status = run.get("status", "unknown")

            if is_terminal(status):
                print(f"[DONE]   {scenario_id} — status={status}")
                item["final_status"] = status
                completed.append(item)
            else:
                sim_time = run.get("current_sim_time", "?")
                print(f"[POLL]   {scenario_id} — status={status}, sim_time={sim_time}")
                still_active.append(item)

        active = still_active
        start_next()

    # Summary
    print("\n" + "=" * 60)
    print("BATCH COMPLETE")
    print("=" * 60)
    for item in completed:
        print(f"  {item['scenario_id']:25s}  {item.get('final_status', '?'):12s}  {item['run_id']}")

    print(f"\nAll runs visible in UI at {base_url}/")
    client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Batch runner for PM simulation scenarios")
    parser.add_argument(
        "--scenarios",
        nargs="+",
        default=ALL_SCENARIOS,
        choices=ALL_SCENARIOS,
        help="Scenarios to run (default: all)",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Model to use (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--speed",
        type=int,
        default=DEFAULT_SPEED,
        help=f"Time scale multiplier (default: {DEFAULT_SPEED})",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=DEFAULT_CONCURRENCY,
        help=f"Max concurrent runs (default: {DEFAULT_CONCURRENCY})",
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"API base URL (default: {DEFAULT_BASE_URL})",
    )

    args = parser.parse_args()

    print(f"Batch runner: {len(args.scenarios)} scenarios, model={args.model}, speed={args.speed}x, concurrency={args.concurrency}")
    print(f"API: {args.base_url}")
    print()

    try:
        run_batch(
            base_url=args.base_url,
            scenarios=args.scenarios,
            model=args.model,
            speed=args.speed,
            concurrency=args.concurrency,
        )
    except httpx.ConnectError:
        print(f"\nERROR: Cannot connect to {args.base_url}. Is the server running?", file=sys.stderr)
        sys.exit(1)
    except httpx.HTTPStatusError as exc:
        print(f"\nERROR: HTTP {exc.response.status_code}: {exc.response.text}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
