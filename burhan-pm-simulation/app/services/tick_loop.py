from __future__ import annotations

import logging
import time
from concurrent.futures import Future, ThreadPoolExecutor

from app.services import simulation_service, state_store
from app.services.config import get_settings
from app.services.db import init_db, session_scope

logger = logging.getLogger(__name__)


class TickLoop:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._executor = ThreadPoolExecutor(max_workers=20)
        self._running_futures: dict[str, Future] = {}  # turn_id -> future
        self._next_tick_due_by_run: dict[str, float] = {}

    def run_once(self, *, force: bool = True) -> None:
        """Process one round of ticks.

        force=True  — run all ticks immediately, actor turns run synchronously
                       (used in tests).
        force=False — respect wall-clock intervals, actor turns submitted to
                       the thread pool and run concurrently.
        """
        self._reap_completed_futures()

        if force:
            for run_id in simulation_service.list_running_run_ids():
                turn_ids = simulation_service.process_run_tick(run_id)
                for turn_id in turn_ids:
                    simulation_service.run_actor_turn(turn_id)
            return

        now = time.monotonic()
        running_run_ids = simulation_service.list_running_run_ids()
        running_run_id_set = set(running_run_ids)
        for stale_run_id in list(self._next_tick_due_by_run):
            if stale_run_id not in running_run_id_set:
                self._next_tick_due_by_run.pop(stale_run_id, None)

        for run_id in running_run_ids:
            runtime_state = simulation_service.get_run_runtime_state(run_id)
            if runtime_state is None:
                continue
            effective_wall_seconds = max(float(runtime_state["tick_wall_seconds"]), 0.01)
            next_due = self._next_tick_due_by_run.get(run_id, now)
            catchup_ticks = 0
            while now + 1e-9 >= next_due and catchup_ticks < 200:
                turn_ids = simulation_service.process_run_tick(run_id)
                cap = int(runtime_state["max_actor_invocations_per_tick"])
                for turn_id in turn_ids[:cap]:
                    self._submit_turn(turn_id)
                catchup_ticks += 1
                next_due += effective_wall_seconds
                runtime_state = simulation_service.get_run_runtime_state(run_id)
                if runtime_state is None or runtime_state["status"] != "running":
                    break
                effective_wall_seconds = max(float(runtime_state["tick_wall_seconds"]), 0.01)
            self._next_tick_due_by_run[run_id] = next_due

            # Skip idle time if no actors are active
            self._maybe_skip_idle_time(run_id)

    def serve_forever(self) -> None:
        init_db()
        while True:
            self._reap_completed_futures()
            self.run_once(force=False)
            time.sleep(self.settings.worker_poll_seconds)

    def _submit_turn(self, turn_id: str) -> None:
        if turn_id in self._running_futures:
            return
        fut = self._executor.submit(simulation_service.run_actor_turn, turn_id)
        self._running_futures[turn_id] = fut

    def _reap_completed_futures(self) -> None:
        done = [tid for tid, f in self._running_futures.items() if f.done()]
        for tid in done:
            fut = self._running_futures.pop(tid)
            exc = fut.exception()
            if exc:
                logger.error(
                    "actor turn failed: turn_id=%s error=%s",
                    tid, exc,
                    exc_info=exc,
                )

    def _maybe_skip_idle_time(self, run_id: str) -> None:
        """If no actors are active and no futures running, skip to next trigger."""
        if any(not f.done() for f in self._running_futures.values()):
            return
        with session_scope() as session:
            run = state_store.get_run(session, run_id)
            if not run:
                return
            # Don't skip if there are actors still mid-turn
            open_actors = state_store.actor_ids_with_open_turns(session, run_id)
            if open_actors:
                return
            next_trigger_time = state_store.earliest_pending_trigger_time(session, run_id)
            if next_trigger_time and next_trigger_time > run.current_sim_time:
                state_store.update_run_time(session, run_id, next_trigger_time)
