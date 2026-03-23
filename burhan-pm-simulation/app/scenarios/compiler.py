from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.domain.events import DomainEvent
from app.domain.models import ActorStatus, OrchestrationStatus, RunStatus, TriggerStatus, new_id
from app.scenarios.loader import load_scenario
from app.services import event_store, state_store
from app.services.config import get_settings
from app.services.db import (
    ActorRecord,
    ActorStateRecord,
    SimulationRunRecord,
    TriggerRecord,
    WorldObjectRecord,
)


def compile_scenario(
    session: Session,
    *,
    scenario_id: str,
    controller_overrides: dict[str, str] | None = None,
    model: str | None = None,
) -> SimulationRunRecord:
    settings = get_settings()
    bundle = load_scenario(scenario_id)
    metadata = bundle["metadata"]
    actors = bundle["actors"]
    world = bundle["world"]
    triggers = bundle["triggers"]

    run_id = new_id("run")
    start_sim_time = datetime.fromisoformat(metadata.start_sim_time)

    run = SimulationRunRecord(
        id=run_id,
        scenario_id=metadata.id,
        status=metadata.default_run_status or RunStatus.PAUSED.value,
        orchestration_status=OrchestrationStatus.UNATTACHED.value,
        current_sim_time=start_sim_time,
        tick_wall_seconds=settings.tick_wall_seconds,
        tick_sim_seconds=settings.tick_sim_seconds,
        max_actor_invocations_per_tick=settings.max_actor_invocations_per_tick,
        config_json={
            "time_scale_multiplier": 10,
            "model": model or settings.claude_model,
            "start_sim_time": metadata.start_sim_time,
            "deadline_days": metadata.deadline_days,
            "assignment": (
                {
                    **metadata.mission.model_dump(),
                    "state": "in_progress",
                    "finished_by_actor_id": None,
                    "finished_at": None,
                    "finish_summary": None,
                    "remaining_risks": [],
                    "confidence": None,
                    "end_reason": None,
                }
                if metadata.mission is not None
                else {}
            )
        },
    )
    state_store.create_run(session, run)

    event_store.append_event(
        session,
        run_id=run_id,
        sim_time=start_sim_time,
        event=DomainEvent(
            event_type="RunCreated",
            data={"scenario_id": metadata.id, "name": metadata.name},
            visibility={"scope": "admin"},
        ),
    )

    for actor_seed in actors.actors:
        controller_type = (controller_overrides or {}).get(actor_seed.id, actor_seed.controller_type)
        actor = ActorRecord(
            id=actor_seed.id,
            run_id=run_id,
            name=actor_seed.name,
            role=actor_seed.role,
            team=actor_seed.team,
            controller_type=controller_type,
            timezone=actor_seed.timezone,
            working_hours_json=actor_seed.working_hours,
            permissions_json=actor_seed.permissions,
            profile_json={
                **actor_seed.profile,
                "character_prompt": actor_seed.character_prompt,
            },
        )
        state_store.create_actor(session, actor)
        state_store.create_actor_state(
            session,
            ActorStateRecord(
                actor_id=actor_seed.id,
                run_id=run_id,
                status=ActorStatus.ACTIVE.value,
                goals_json=actor_seed.goals,
                beliefs_json=actor_seed.beliefs,
                relationships_json=actor_seed.relationships,
                commitments_json=actor_seed.commitments,
                workload_json=actor_seed.workload,
                focus_state_json={},
            ),
        )
        event_store.append_event(
            session,
            run_id=run_id,
            sim_time=start_sim_time,
            event=DomainEvent(
                event_type="ActorCreated",
                actor_id=actor_seed.id,
                data={"name": actor_seed.name, "role": actor_seed.role},
                visibility={"scope": "admin"},
            ),
        )
        for routine_index, routine in enumerate(actor_seed.profile.get("routines", [])):
            interval_minutes = int(routine.get("interval_minutes") or 0)
            initial_offset_minutes = int(
                routine.get("initial_offset_minutes", interval_minutes or 0)
            )
            if initial_offset_minutes <= 0:
                continue
            state_store.create_trigger(
                session,
                TriggerRecord(
                    id=new_id("trg"),
                    run_id=run_id,
                    trigger_type="actor_routine_wake",
                    due_sim_time=start_sim_time + timedelta(minutes=initial_offset_minutes),
                    actor_id=actor_seed.id,
                    status=TriggerStatus.PENDING.value,
                    priority=int(routine.get("priority") or 4),
                    data_json={
                        "reason": routine.get("reason", "periodic routine"),
                        "source": "routine",
                        "routine_index": routine_index,
                        "recurring_interval_minutes": interval_minutes,
                    },
                ),
            )

    for object_seed in world.objects:
        state_store.create_world_object(
            session,
            WorldObjectRecord(
                id=object_seed.id,
                run_id=run_id,
                kind=object_seed.kind,
                title=object_seed.title,
                owner_actor_id=object_seed.owner_actor_id,
                parent_object_id=object_seed.parent_object_id,
                visibility_json=object_seed.visibility,
                state_json=object_seed.state,
            ),
        )
        event_store.append_event(
            session,
            run_id=run_id,
            sim_time=start_sim_time,
            event=DomainEvent(
                event_type="WorldObjectCreated",
                object_id=object_seed.id,
                data={"kind": object_seed.kind, "title": object_seed.title},
                visibility={"scope": "admin"},
            ),
        )

    for trigger_seed in triggers.triggers:
        due_time = start_sim_time + timedelta(minutes=trigger_seed.due_offset_minutes)
        state_store.create_trigger(
            session,
            TriggerRecord(
                id=trigger_seed.id,
                run_id=run_id,
                trigger_type=trigger_seed.trigger_type,
                due_sim_time=due_time,
                actor_id=trigger_seed.actor_id,
                object_id=trigger_seed.object_id,
                status=TriggerStatus.PENDING.value,
                priority=trigger_seed.priority,
                data_json=trigger_seed.data,
            ),
        )

    return run
