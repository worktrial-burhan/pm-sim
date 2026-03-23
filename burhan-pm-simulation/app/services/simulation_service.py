from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from app.agents.base import BaseController, ControllerDecision
from app.agents.claude_controller import ClaudeController
from app.agents.scripted_controller import ScriptedController
from app.agents.session_state import ActorTurnContext
from app.agents.toolkit import ControllerToolkit
from app.domain.engine import apply_trigger
from app.domain.events import DomainEvent
from app.domain.models import (
    ActorStatus,
    ActorTurnStatus,
    ControllerType,
    OrchestrationStatus,
    RunStatus,
    TraceType,
    new_id,
    utcnow,
)
from app.domain.scheduler import get_due_triggers
from app.services import (
    attention_service,
    delivery_service,
    event_store,
    perception_service,
    run_service,
    state_store,
    trace_store,
)
from app.services.assignment_service import public_assignment
from app.services.config import get_settings
from app.services.db import ActorTurnRecord, RunTickRecord, session_scope

logger = logging.getLogger(__name__)


@dataclass
class PreparedActorTurn:
    turn_id: str
    run_id: str
    actor_id: str
    current_sim_time: Any
    actor: Any
    context: ActorTurnContext
    request_context: dict[str, Any]


def get_controller_registry() -> dict[str, BaseController]:
    return {
        ControllerType.CLAUDE.value: ClaudeController(),
        ControllerType.SCRIPTED.value: ScriptedController(),
    }


def list_running_run_ids() -> list[str]:
    with session_scope() as session:
        return state_store.list_tickable_run_ids(session)


def get_run_runtime_state(run_id: str) -> dict | None:
    with session_scope() as session:
        run = state_store.get_run(session, run_id)
        if run is None:
            return None
        return {
            "run_id": run.id,
            "status": run.status,
            "orchestration_status": run.orchestration_status,
            "tick_wall_seconds": run_service.effective_tick_wall_seconds(run),
            "max_actor_invocations_per_tick": run.max_actor_invocations_per_tick,
            "time_scale_multiplier": run_service.get_time_scale_multiplier(run),
        }


def process_run_tick(run_id: str, tick_token: str | None = None) -> list[str]:
    with session_scope() as session:
        if tick_token:
            existing_tick = state_store.get_run_tick(session, run_id, tick_token)
            if existing_tick is not None:
                return list(existing_tick.turn_ids_json or [])

        run = state_store.get_run_for_update(session, run_id)
        if run is None:
            return []
        if run.status != RunStatus.RUNNING.value:
            return []
        if run.orchestration_status != OrchestrationStatus.ATTACHED.value:
            return []

        next_sim_time = run.current_sim_time + timedelta(seconds=run.tick_sim_seconds)

        # ── Deadline auto-stop ──────────────────────────────────────────
        config = run.config_json or {}
        deadline_days = config.get("deadline_days")
        start_sim_str = config.get("start_sim_time")
        if deadline_days and start_sim_str:
            from datetime import datetime as _dt, timezone as _tz
            start_sim = _dt.fromisoformat(start_sim_str)
            if start_sim.tzinfo is None:
                start_sim = start_sim.replace(tzinfo=_tz.utc)
            deadline_sim = start_sim + timedelta(days=int(deadline_days))
            check_time = next_sim_time
            if check_time.tzinfo is None:
                check_time = check_time.replace(tzinfo=_tz.utc)
            if check_time >= deadline_sim:
                event_store.append_event(
                    session,
                    run_id=run_id,
                    sim_time=run.current_sim_time,
                    event=DomainEvent(
                        event_type="DeadlineReached",
                        visibility={"scope": "admin"},
                        data={"deadline_days": deadline_days},
                    ),
                )
                state_store.update_run_status(
                    session, run_id, RunStatus.STOPPED.value, now=run.current_sim_time,
                )
                from app.services import analysis_service
                analysis_service.trigger_post_run_analysis(run_id)
                logger.info("run stopped: deadline reached", extra={"run_id": run_id})
                return []

        state_store.update_run_time(session, run_id, next_sim_time)
        run = state_store.get_run(session, run_id)
        assert run is not None
        event_store.append_event(
            session,
            run_id=run_id,
            sim_time=run.current_sim_time,
            event=DomainEvent(
                event_type="ClockAdvanced",
                visibility={"scope": "admin"},
                data={"current_sim_time": run.current_sim_time.isoformat()},
            ),
        )

        due_triggers = get_due_triggers(session, run_id=run.id, current_sim_time=run.current_sim_time)
        triggered_actor_ids: list[str] = []
        for trigger in due_triggers:
            if trigger.actor_id and trigger.trigger_type in {
                "actor_routine_wake",
                "response_delay",
                "obligation_due",
            }:
                actor = state_store.get_actor(session, run_id, trigger.actor_id)
                actor_state = state_store.get_actor_state(session, run_id, trigger.actor_id)
                if actor is not None and actor_state is not None:
                    deferred_due_time: datetime | None = None
                    if not attention_service.is_within_working_hours(actor, run.current_sim_time):
                        deferred_due_time = attention_service.next_working_time(actor, run.current_sim_time)
                    if (
                        actor_state.next_eligible_wake_time
                        and _as_aware_utc(actor_state.next_eligible_wake_time)
                        > _as_aware_utc(run.current_sim_time)
                    ):
                        deferred_due_time = max(
                            deferred_due_time or actor_state.next_eligible_wake_time,
                            actor_state.next_eligible_wake_time,
                            key=_as_aware_utc,
                        )
                    if deferred_due_time and _as_aware_utc(deferred_due_time) > _as_aware_utc(
                        run.current_sim_time
                    ):
                        state_store.update_trigger_due_time(
                            session,
                            run_id=run_id,
                            trigger_id=trigger.id,
                            due_sim_time=_align_to_reference(deferred_due_time, run.current_sim_time),
                        )
                        continue
            triggered_actor_ids.extend(
                apply_trigger(
                    session,
                    run=run,
                    trigger=trigger,
                    current_sim_time=run.current_sim_time,
                )
            )

        wake_candidates = list(dict.fromkeys(triggered_actor_ids))
        active_turn_actor_ids = state_store.actor_ids_with_open_turns(session, run_id)
        triggered_actor_id_set = set(triggered_actor_ids)

        turn_ids: list[str] = []
        for actor_id in wake_candidates:
            if len(turn_ids) >= run.max_actor_invocations_per_tick:
                break
            if actor_id in active_turn_actor_ids:
                continue

            actor_state = state_store.get_actor_state(session, run_id, actor_id)
            if actor_state is None or actor_state.status != ActorStatus.ACTIVE.value:
                continue
            if actor_state.next_eligible_wake_time and _as_aware_utc(
                actor_state.next_eligible_wake_time
            ) > _as_aware_utc(run.current_sim_time):
                continue

            turn_ids.append(
                _materialize_turn(
                    session,
                    run=run,
                    actor_id=actor_id,
                    cause_ref={
                        "has_due_trigger": actor_id in triggered_actor_id_set,
                        "has_unread_delivery": delivery_service.actor_has_unread_deliveries(
                            session,
                            run_id=run_id,
                            actor_id=actor_id,
                            current_sim_time=run.current_sim_time,
                        ),
                    },
                )
            )
            active_turn_actor_ids.add(actor_id)

        if tick_token:
            state_store.create_run_tick(
                session,
                RunTickRecord(
                    id=new_id("tick"),
                    run_id=run_id,
                    tick_token=tick_token,
                    sim_time=run.current_sim_time,
                    turn_ids_json=turn_ids,
                ),
            )

        logger.info("processed tick", extra={"run_id": run_id, "turn_count": len(turn_ids)})
        return turn_ids


def run_actor_turn(turn_id: str, controllers: dict[str, BaseController] | None = None) -> None:
    """Run a single actor turn. Bridges sync callers to async controller.decide()."""
    controllers = controllers or get_controller_registry()

    turn_status = _get_turn_status(turn_id)
    if turn_status in {
        ActorTurnStatus.APPLIED.value,
        ActorTurnStatus.CANCELLED.value,
        ActorTurnStatus.FAILED.value,
    }:
        return

    prepared = _prepare_actor_turn(turn_id)
    if prepared is None:
        return

    controller = controllers.get(prepared.actor.controller_type)
    if controller is None:
        _fail_turn(
            turn_id,
            stage="prepare",
            message=f"no controller registered for {prepared.actor.controller_type}",
            sim_time=prepared.current_sim_time,
            run_id=prepared.run_id,
            actor_id=prepared.actor_id,
        )
        return

    toolkit = ControllerToolkit(
        run_id=prepared.run_id,
        actor=prepared.actor,
        current_sim_time=prepared.current_sim_time,
    )

    try:
        # Bridge sync → async: run the async decide() in an event loop
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            # Already in an async context (e.g. Temporal activity) — create a task
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                decision = pool.submit(asyncio.run, controller.decide(prepared.context, toolkit)).result()
        else:
            decision = asyncio.run(controller.decide(prepared.context, toolkit))
    except Exception as exc:
        _fail_turn(
            turn_id,
            stage="decide",
            message="controller invocation failed",
            error=str(exc),
            sim_time=prepared.current_sim_time,
            run_id=prepared.run_id,
            actor_id=prepared.actor_id,
        )
        with session_scope() as sess:
            run = state_store.get_run(sess, prepared.run_id)
            cooldown_time = run.current_sim_time if run else prepared.current_sim_time
        _set_actor_cooldown(
            run_id=prepared.run_id,
            actor_id=prepared.actor_id,
            current_sim_time=cooldown_time,
        )
        return

    _finalize_turn(turn_id, prepared, decision, toolkit)


def _materialize_turn(session, *, run, actor_id: str, cause_ref: dict[str, Any]) -> str:
    turn_id = new_id("turn")
    turn_seq = run.next_turn_seq
    run.next_turn_seq += 1
    session.flush()
    state_store.create_actor_turn(
        session,
        ActorTurnRecord(
            id=turn_id,
            run_id=run.id,
            actor_id=actor_id,
            turn_seq=turn_seq,
            sim_time=run.current_sim_time,
            cause_type="tick_wake",
            cause_ref_json=cause_ref,
            request_context_json={},
            status=ActorTurnStatus.PREPARED.value,
            prepared_at=utcnow(),
        ),
    )
    return turn_id


def _get_turn_status(turn_id: str) -> str | None:
    with session_scope() as session:
        turn = state_store.get_actor_turn(session, turn_id)
        return None if turn is None else turn.status


def _prepare_actor_turn(turn_id: str) -> PreparedActorTurn | None:
    with session_scope() as session:
        turn = state_store.get_actor_turn_for_update(session, turn_id)
        if turn is None:
            return None
        if turn.status in {
            ActorTurnStatus.APPLIED.value,
            ActorTurnStatus.DECIDED.value,
            ActorTurnStatus.CANCELLED.value,
            ActorTurnStatus.FAILED.value,
        }:
            return None

        run = state_store.get_run(session, turn.run_id)
        actor = state_store.get_actor(session, turn.run_id, turn.actor_id)
        actor_state = state_store.get_actor_state(session, turn.run_id, turn.actor_id)
        if run is None or actor is None or actor_state is None:
            state_store.update_actor_turn(
                session, turn.id,
                status=ActorTurnStatus.FAILED.value,
                error_json={"stage": "prepare", "message": "turn references missing state"},
            )
            return None
        # Allow turns that were materialized while the run was still active.
        # A sibling turn in the same tick may have completed the run — but turns
        # created before that completion should still execute so every actor
        # who was woken gets a chance to act.
        run_is_tickable = (
            run.status in {RunStatus.RUNNING.value, RunStatus.COMPLETED.value}
            and run.orchestration_status == OrchestrationStatus.ATTACHED.value
        )
        if not run_is_tickable:
            state_store.update_actor_turn(
                session, turn.id,
                status=ActorTurnStatus.CANCELLED.value,
                error_json={"stage": "prepare", "message": "run is not tickable"},
            )
            return None
        if actor_state.next_eligible_wake_time and _as_aware_utc(
            actor_state.next_eligible_wake_time
        ) > _as_aware_utc(turn.sim_time):
            state_store.update_actor_turn(
                session, turn.id,
                status=ActorTurnStatus.CANCELLED.value,
                error_json={"stage": "prepare", "message": "actor is still on cooldown"},
            )
            return None

        effective_sim_time = _max_sim_time(turn.sim_time, run.current_sim_time)
        if not attention_service.is_within_working_hours(actor, effective_sim_time):
            state_store.update_actor_turn(
                session, turn.id,
                status=ActorTurnStatus.CANCELLED.value,
                error_json={"stage": "prepare", "message": "actor is outside working hours"},
            )
            return None

        actor_directory = {
            c.id: c.name for c in state_store.list_actors(session, turn.run_id)
        }
        visible_objects = perception_service.visible_work_objects(
            session, run_id=turn.run_id, actor_id=actor.id,
            actor_role=actor.role, actor_team=actor.team,
        )
        observations = perception_service.list_actor_observations(
            session, run=run, actor=actor, actor_state=actor_state,
            actor_directory=actor_directory,
        )
        commitments = perception_service.list_actor_commitments(
            session, run_id=turn.run_id, actor_id=actor.id,
            actor_directory=actor_directory,
        )

        request_context = {
            "observation_count": len(observations),
            "commitment_count": len(commitments),
            "visible_object_count": len(visible_objects),
            "controller_type": actor.controller_type,
            "cause_type": turn.cause_type,
            "cause_ref": turn.cause_ref_json,
        }
        state_store.set_actor_turn_window(
            session, run_id=turn.run_id, actor_id=actor.id,
            started_at=effective_sim_time, completed_at=None, next_eligible_wake_time=None,
        )
        state_store.update_actor_turn(
            session, turn.id,
            status=ActorTurnStatus.DECIDING.value,
            request_context_json=request_context,
            prepared_at=turn.prepared_at or utcnow(),
            error_json=None,
        )

        context = ActorTurnContext(
            turn_id=turn.id,
            run_id=turn.run_id,
            current_sim_time=effective_sim_time,
            local_current_time=attention_service.local_time_for_actor(actor, effective_sim_time).isoformat(),
            actor_id=actor.id,
            actor_name=actor.name,
            actor_role=actor.role,
            actor_team=actor.team,
            profile=actor.profile_json,
            permissions=actor.permissions_json,
            work_availability=attention_service.describe_work_window(actor, effective_sim_time),
            goals=actor_state.goals_json,
            beliefs=actor_state.beliefs_json,
            relationships=actor_state.relationships_json,
            declared_commitments=actor_state.commitments_json,
            workload=actor_state.workload_json,
            actor_directory=actor_directory,
            observations=observations,
            current_commitments=commitments,
            visible_objects=[
                {
                    "id": obj.id, "kind": obj.kind, "title": obj.title,
                    "state": obj.state_json,
                }
                for obj in visible_objects
            ],
            assignment=(
                public_assignment((run.config_json or {}).get("assignment") or {})
                if actor.permissions_json.get("can_finish_assignment", False)
                else {}
            ),
            model=(run.config_json or {}).get("model"),
        )

        return PreparedActorTurn(
            turn_id=turn.id,
            run_id=turn.run_id,
            actor_id=actor.id,
            current_sim_time=effective_sim_time,
            actor=actor,
            context=context,
            request_context=request_context,
        )


def _finalize_turn(
    turn_id: str,
    prepared: PreparedActorTurn,
    decision: ControllerDecision,
    toolkit: ControllerToolkit,
) -> None:
    """Record traces, set cooldown, mark turn applied. Commands already executed inline."""
    with session_scope() as session:
        turn = state_store.get_actor_turn_for_update(session, turn_id)
        if turn is None or turn.status == ActorTurnStatus.APPLIED.value:
            return

        run = state_store.get_run(session, turn.run_id)
        if run is None:
            return

        # Request trace
        trace_store.append_trace(
            session, run_id=turn.run_id, sim_time=turn.sim_time,
            actor_id=turn.actor_id, trace_type=TraceType.CONTROLLER_REQUEST,
            data=prepared.request_context,
        )

        # Toolkit trace entries
        for entry in toolkit.consume_trace_entries():
            trace_store.append_trace(
                session, run_id=turn.run_id,
                sim_time=_trace_entry_sim_time(entry.get("sim_time"), default=turn.sim_time),
                actor_id=turn.actor_id,
                trace_type=entry.get("trace_type", TraceType.SYSTEM_DEBUG.value),
                data=entry.get("data", {}),
            )

        # Response trace
        trace_store.append_trace(
            session, run_id=turn.run_id, sim_time=turn.sim_time,
            actor_id=turn.actor_id, trace_type=TraceType.CONTROLLER_RESPONSE,
            data={
                "executed_command_count": len(toolkit.executed_commands),
                "decision_signal": decision.decision_signal,
                "final_reasoning": decision.final_reasoning,
                "cost_usd": decision.cost_usd,
                "session_id": decision.session_id,
            },
        )

        # Introspection entries
        for entry in decision.introspection_entries:
            trace_store.append_trace(
                session, run_id=turn.run_id,
                sim_time=_trace_entry_sim_time(entry.get("sim_time"), default=run.current_sim_time),
                actor_id=turn.actor_id,
                trace_type=TraceType.INTROSPECTION_WRITE,
                data=entry,
            )

        # Persist decision for audit
        decision_payload = {
            "introspection_entries": list(decision.introspection_entries),
            "decision_signal": decision.decision_signal,
            "final_reasoning": decision.final_reasoning,
            "executed_commands": toolkit.executed_commands,
            "session_id": decision.session_id,
            "cost_usd": decision.cost_usd,
        }

        # Set cooldown from the current sim time at the moment the turn finishes.
        _set_actor_cooldown(
            run_id=turn.run_id,
            actor_id=turn.actor_id,
            current_sim_time=run.current_sim_time,
            session=session,
        )

        # Mark turn applied (commands already executed inline)
        state_store.update_actor_turn(
            session, turn_id,
            status=ActorTurnStatus.APPLIED.value,
            decision_json=decision_payload,
            decided_at=utcnow(),
            applied_at=utcnow(),
            error_json=None,
        )

        # Check if run completed during this turn (e.g. PM called finish_assignment).
        # Commands execute in their own sessions before _finalize_turn, so run.status
        # already reflects the post-command state.  We check for completed/stopped and
        # rely on idempotency in the analysis pipeline to avoid duplicate work.
        run_after = state_store.get_run(session, turn.run_id)
        run_just_completed = (
            run_after is not None
            and run_after.status in {RunStatus.COMPLETED.value, RunStatus.STOPPED.value}
        )

    # Session is committed — traces are in the DB. Safe to trigger analysis.
    if run_just_completed:
        from app.services import analysis_service
        analysis_service.trigger_post_run_analysis(prepared.run_id)


def _fail_turn(
    turn_id: str, *, stage: str, message: str,
    sim_time, run_id: str, actor_id: str, error: str | None = None,
) -> None:
    with session_scope() as session:
        turn = state_store.get_actor_turn_for_update(session, turn_id)
        if turn is None or turn.status == ActorTurnStatus.APPLIED.value:
            return
        trace_store.append_trace(
            session, run_id=run_id, sim_time=sim_time, actor_id=actor_id,
            trace_type=TraceType.SYSTEM_DEBUG,
            data={"message": message, "stage": stage, "error": error},
        )
        logger.warning(
            "actor turn failed: turn_id=%s run_id=%s actor_id=%s stage=%s message=%s error=%s",
            turn_id, run_id, actor_id, stage, message, error,
        )
        state_store.update_actor_turn(
            session, turn_id,
            status=ActorTurnStatus.FAILED.value,
            error_json={"stage": stage, "message": message, "error": error},
        )


def _set_actor_cooldown(*, run_id: str, actor_id: str, current_sim_time, session=None) -> None:
    next_eligible = current_sim_time + timedelta(seconds=get_settings().actor_cooldown_seconds)

    if session is not None:
        state_store.set_actor_turn_window(
            session, run_id=run_id, actor_id=actor_id,
            started_at=current_sim_time, completed_at=current_sim_time,
            next_eligible_wake_time=next_eligible,
        )
        return

    with session_scope() as owned_session:
        state_store.set_actor_turn_window(
            owned_session, run_id=run_id, actor_id=actor_id,
            started_at=current_sim_time, completed_at=current_sim_time,
            next_eligible_wake_time=next_eligible,
        )


def _trace_entry_sim_time(value: str | None, *, default: datetime) -> datetime:
    if not value:
        return default
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return default


def _as_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _align_to_reference(value: datetime, reference: datetime) -> datetime:
    normalized = _as_aware_utc(value)
    if reference.tzinfo is None:
        return normalized.replace(tzinfo=None)
    return normalized.astimezone(reference.tzinfo)


def _max_sim_time(left: datetime, right: datetime) -> datetime:
    result = max(left, right, key=_as_aware_utc)
    return _align_to_reference(result, right)
