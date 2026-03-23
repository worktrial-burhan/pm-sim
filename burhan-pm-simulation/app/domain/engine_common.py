from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.domain.events import DomainEvent
from app.domain.reducers import apply_event
from app.domain.visibility import actor_can_view, normalize_visibility
from app.services import event_store, state_store
from app.services.db import ActorRecord, EventRecord, SimulationRunRecord, WorldObjectRecord


class CommandRejected(Exception):
    pass


@dataclass
class AppliedCommandResult:
    events: list[EventRecord] = field(default_factory=list)
    deliveries_created: int = 0


def append_and_apply(
    session: Session,
    *,
    run_id: str,
    sim_time: datetime,
    events: list[DomainEvent],
) -> list[EventRecord]:
    accepted_events = event_store.append_events(session, run_id=run_id, sim_time=sim_time, events=events)
    for event_record in accepted_events:
        apply_event(session, run_id=run_id, event_record=event_record)
    return accepted_events


def require_permission(actor: ActorRecord, permission_key: str) -> None:
    if not actor.permissions_json.get(permission_key, False):
        raise CommandRejected(f"actor lacks permission: {permission_key}")


def validate_actor_created_visibility(
    session: Session,
    *,
    run: SimulationRunRecord,
    actor: ActorRecord,
    raw_visibility: dict[str, Any] | None,
) -> dict[str, Any]:
    visibility = normalize_visibility(raw_visibility)
    scope = str(visibility.get("scope") or "company").strip().lower()

    if scope == "admin":
        raise CommandRejected("actor-created documents cannot use admin visibility")

    if scope == "private":
        visibility = {"scope": "private", "owner_actor_id": actor.id}
    elif scope == "actors":
        actor_ids = sorted({str(item) for item in visibility.get("actor_ids", []) if str(item).strip()})
        if actor.id not in actor_ids:
            actor_ids.append(actor.id)
        if not actor_ids:
            raise CommandRejected("actors visibility must include at least one actor")
        for visible_actor_id in actor_ids:
            if state_store.get_actor(session, run.id, visible_actor_id) is None:
                raise CommandRejected(f"visibility actor does not exist: {visible_actor_id}")
        visibility = {"scope": "actors", "actor_ids": actor_ids}
    elif scope == "team":
        team = str(visibility.get("team") or actor.team or "").strip()
        if not team:
            raise CommandRejected("team visibility requires a team")
        visibility = {"scope": "team", "team": team}
    elif scope == "role":
        role = str(visibility.get("role") or "").strip()
        if not role:
            raise CommandRejected("role visibility requires a role")
        if role == "admin":
            raise CommandRejected("actor-created documents cannot use admin visibility")
        visibility = {"scope": "role", "role": role}
    elif scope != "company":
        raise CommandRejected(f"unsupported document visibility scope: {scope}")

    if not actor_can_view(
        actor_id=actor.id,
        actor_role=actor.role,
        actor_team=actor.team,
        visibility=visibility,
    ):
        raise CommandRejected("document visibility must remain visible to the author")

    return visibility


def require_object_kind(
    session: Session, run_id: str, object_id: str | None, expected_kind: str
) -> WorldObjectRecord:
    if not object_id:
        raise CommandRejected("missing object id")
    obj = state_store.get_world_object(session, run_id, object_id)
    if obj is None:
        raise CommandRejected(f"object does not exist: {object_id}")
    if obj.kind != expected_kind:
        raise CommandRejected(f"object {object_id} is not a {expected_kind}")
    return obj


def get_thread_and_participants(
    session: Session, run_id: str, thread_id: str | None, *, expected_kind: str
) -> tuple[WorldObjectRecord, list[str]]:
    thread = require_object_kind(session, run_id, thread_id, expected_kind)
    participants = list((thread.state_json or {}).get("participant_actor_ids", []))
    if not participants:
        raise CommandRejected("thread has no participants")
    return thread, participants


def conditions_match(session: Session, run_id: str, conditions: list[dict[str, Any]]) -> bool:
    for condition in conditions:
        obj = state_store.get_world_object(session, run_id, condition["object_id"])
        if obj is None:
            return False
        value = (obj.state_json or {}).get(condition["state_key"])
        if "equals" in condition and value != condition["equals"]:
            return False
        if "not_equals" in condition and value == condition["not_equals"]:
            return False
        if "in" in condition and value not in condition["in"]:
            return False
        if "not_in" in condition and value in condition["not_in"]:
            return False
    return True
