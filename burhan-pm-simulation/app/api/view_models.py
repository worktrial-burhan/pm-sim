from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.api.presenters import (
    activity_sort_key,
    format_relative_time,
    format_time_ago,
    is_visible_activity,
    serialize_actor,
    serialize_object,
    summarize_activity,
    summarize_event,
    summarize_trace,
)
from app.services import (
    attention_service,
    delivery_service,
    event_store,
    perception_service,
    state_store,
    trace_store,
)
from app.services.assignment_service import public_assignment


def build_actor_lookup(session: Session, run_id: str) -> dict[str, str]:
    return {actor.id: actor.name for actor in state_store.list_actors(session, run_id)}


def build_actor_cards(session: Session, run_id: str) -> list[dict[str, Any]]:
    return [
        serialize_actor(actor, state_store.get_actor_state(session, run_id, actor.id))
        for actor in state_store.list_actors(session, run_id)
    ]


def build_run_events(session: Session, run_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
    actor_lookup = build_actor_lookup(session, run_id)
    return [
        {
            "id": event.id,
            "seq": event.seq,
            "sim_time": event.sim_time.isoformat(),
            "event_type": event.event_type,
            "actor_name": actor_lookup.get(event.actor_id, event.actor_id or "System"),
            "summary": summarize_event(event, actor_lookup),
            "data": event.data_json,
        }
        for event in event_store.list_events(session, run_id=run_id, limit=limit)
    ]


def build_run_activity(
    session: Session,
    run_id: str,
    *,
    event_limit: int = 200,
    trace_limit: int = 200,
) -> list[dict[str, Any]]:
    actor_lookup = build_actor_lookup(session, run_id)
    events = [
        {
            "id": event.id,
            "entry_type": "event",
            "seq": event.seq,
            "sim_time": event.sim_time.isoformat(),
            "kind": event.event_type,
            "actor_id": event.actor_id,
            "actor_name": actor_lookup.get(event.actor_id, event.actor_id or "System"),
            "summary": summarize_event(event, actor_lookup),
            "data": event.data_json,
        }
        for event in event_store.list_events(session, run_id=run_id, limit=event_limit)
    ]
    traces = [
        {
            "id": trace.id,
            "entry_type": "trace",
            "sim_time": trace.sim_time.isoformat(),
            "kind": trace.trace_type,
            "actor_id": trace.actor_id,
            "actor_name": actor_lookup.get(trace.actor_id, trace.actor_id or "System"),
            "summary": summarize_trace(trace, actor_lookup),
            "data": trace.data_json,
        }
        for trace in trace_store.list_run_traces(
            session,
            run_id=run_id,
            limit=trace_limit,
            trace_types=["introspection_write", "system_debug", "command_rejected"],
        )
    ]
    payload = events + traces
    payload.sort(key=activity_sort_key, reverse=True)
    return payload


def build_clean_activity(
    session: Session,
    run_id: str,
    current_sim_time: str,
) -> list[dict[str, Any]]:
    """Build the human-readable activity stream for the clean UI."""
    actors = state_store.list_actors(session, run_id)
    actor_lookup = {a.id: a.name for a in actors}
    actor_role_lookup = {a.id: a.role for a in actors}

    events = [
        {
            "id": event.id,
            "entry_type": "event",
            "seq": event.seq,
            "sim_time": event.sim_time.isoformat(),
            "kind": event.event_type,
            "actor_id": event.actor_id,
            "actor_name": actor_lookup.get(event.actor_id, event.actor_id or "System"),
            "data": event.data_json,
        }
        for event in event_store.list_events(session, run_id=run_id, limit=None)
    ]

    traces = [
        {
            "id": trace.id,
            "entry_type": "trace",
            "sim_time": trace.sim_time.isoformat(),
            "kind": trace.trace_type,
            "actor_id": trace.actor_id,
            "actor_name": actor_lookup.get(trace.actor_id, trace.actor_id or "System"),
            "data": trace.data_json,
        }
        for trace in trace_store.list_run_traces(
            session,
            run_id=run_id,
            limit=None,
            trace_types=["introspection_write", "command_rejected"],
        )
    ]

    all_entries = events + traces
    # Filter to only visible entries
    visible = [e for e in all_entries if is_visible_activity(e)]
    # Sort reverse chronological (latest first)
    visible.sort(key=activity_sort_key, reverse=True)

    # Add human-readable fields
    for entry in visible:
        entry["relative_time"] = format_time_ago(entry["sim_time"], current_sim_time)
        entry["actor_role"] = actor_role_lookup.get(entry.get("actor_id") or "", "")
        entry["summary"] = summarize_activity(entry, actor_lookup)
        entry["is_thought"] = entry["entry_type"] == "trace" and entry["kind"] == "introspection_write"

    return visible


def build_actor_detail_payload(session: Session, *, run_id: str, actor_id: str) -> dict[str, Any]:
    run = state_store.get_run(session, run_id)
    actor = state_store.get_actor(session, run_id, actor_id)
    if run is None or actor is None:
        raise LookupError("actor or run not found")

    actor_state = state_store.get_actor_state(session, run_id, actor_id)
    actor_lookup = build_actor_lookup(session, run_id)
    observations = (
        perception_service.list_actor_observations(
            session,
            run=run,
            actor=actor,
            actor_state=actor_state,
            actor_directory=actor_lookup,
        )
        if actor_state is not None
        else []
    )
    return {
        "actor": serialize_actor(actor, actor_state),
        "assignment": (
            public_assignment((run.config_json or {}).get("assignment") or {})
            if actor.permissions_json.get("can_finish_assignment", False)
            else {}
        ),
        "work_availability": attention_service.describe_work_window(actor, run.current_sim_time),
        "observations": observations,
        "commitments": perception_service.list_actor_commitments(
            session,
            run_id=run_id,
            actor_id=actor_id,
            actor_directory=actor_lookup,
        ),
        "inbox": delivery_service.list_inbox(
            session,
            run_id=run_id,
            actor_id=actor_id,
            current_sim_time=run.current_sim_time,
            limit=20,
        ),
        "visible_objects": [
            serialize_object(obj)
            for obj in perception_service.visible_work_objects(
                session,
                run_id=run_id,
                actor_id=actor.id,
                actor_role=actor.role,
                actor_team=actor.team,
            )
        ],
        "traces": [
            {
                "id": trace.id,
                "trace_type": trace.trace_type,
                "sim_time": trace.sim_time.isoformat(),
                "summary": summarize_trace(trace, actor_lookup),
                "data": trace.data_json,
                "data_json": trace.data_json,
            }
            for trace in trace_store.list_actor_traces(session, run_id=run_id, actor_id=actor_id, limit=50)
        ],
    }
