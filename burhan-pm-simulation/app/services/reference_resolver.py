from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Iterable

from sqlalchemy.orm import Session

from app.services import state_store

logger = logging.getLogger(__name__)


class ReferenceResolutionError(ValueError):
    pass


@dataclass(frozen=True)
class ActorIdentity:
    id: str
    role: str | None
    team: str | None


def resolve_actor_id(session: Session, *, run_id: str, raw_reference: str) -> str:
    normalized = _normalize_reference(raw_reference)
    actors = state_store.list_actors(session, run_id)

    direct = [actor.id for actor in actors if actor.id == raw_reference]
    if direct:
        return direct[0]

    matches = [
        actor.id
        for actor in actors
        if normalized in {_normalize_reference(actor.name), _normalize_reference(actor.id)}
    ]
    if len(set(matches)) == 1:
        return matches[0]

    candidates = {actor.id: actor.name for actor in actors}
    return _resolve_with_fallback(
        matches=matches,
        candidates=candidates,
        kind="coworker",
        raw_reference=raw_reference,
    )


def resolve_visible_object_id(
    session: Session,
    *,
    run_id: str,
    actor: ActorIdentity,
    raw_reference: str,
    kind: str | None = None,
    surface: str | None = None,
) -> str:
    normalized = _normalize_reference(raw_reference)
    visible_objects = state_store.list_visible_world_objects(
        session,
        run_id=run_id,
        actor_id=actor.id,
        actor_role=actor.role,
        actor_team=actor.team,
        kind=kind,
    )

    direct = [obj.id for obj in visible_objects if obj.id == raw_reference]
    if direct:
        return direct[0]

    matches: list[str] = []
    candidates: dict[str, str] = {}
    for obj in visible_objects:
        if surface is not None and str((obj.state_json or {}).get("surface") or "") != surface:
            continue
        labels = {obj.id, obj.title}
        if obj.kind == "thread":
            labels.add(str((obj.state_json or {}).get("subject") or ""))
        if obj.kind == "obligation":
            labels.add(str((obj.state_json or {}).get("summary") or ""))
        normalized_labels = {_normalize_reference(label) for label in labels if str(label).strip()}
        if normalized in normalized_labels:
            matches.append(obj.id)
        label_display = obj.title or next(iter(sorted(labels - {obj.id})), obj.id)
        candidates[obj.id] = label_display

    if len(set(matches)) == 1:
        return matches[0]

    return _resolve_with_fallback(
        matches=matches,
        candidates=candidates,
        kind=kind or "object",
        raw_reference=raw_reference,
    )


def resolve_owned_obligation_id(
    session: Session,
    *,
    run_id: str,
    actor_id: str,
    raw_reference: str,
) -> str:
    normalized = _normalize_reference(raw_reference)
    obligations = state_store.list_actor_world_objects(
        session,
        run_id=run_id,
        owner_actor_id=actor_id,
        kind="obligation",
    )

    direct = [obj.id for obj in obligations if obj.id == raw_reference]
    if direct:
        return direct[0]

    matches: list[str] = []
    candidates: dict[str, str] = {}
    for obj in obligations:
        state = obj.state_json or {}
        status = str(state.get("status") or "").strip().lower()
        if status in {"done", "cancelled"}:
            continue
        labels = {obj.id, obj.title, str(state.get("summary") or "")}
        normalized_labels = {_normalize_reference(label) for label in labels if str(label).strip()}
        if normalized in normalized_labels:
            matches.append(obj.id)
        candidates[obj.id] = obj.title or str(state.get("summary") or obj.id)

    if len(set(matches)) == 1:
        return matches[0]

    return _resolve_with_fallback(
        matches=matches,
        candidates=candidates,
        kind="obligation",
        raw_reference=raw_reference,
    )


def resolve_delivery_references(
    session: Session,
    *,
    run_id: str,
    actor_id: str,
    raw_references: list[str],
    current_sim_time: Any,
) -> list[str]:
    from app.services import delivery_service

    inbox = delivery_service.list_inbox(
        session,
        run_id=run_id,
        actor_id=actor_id,
        current_sim_time=current_sim_time,
        limit=30,
    )

    candidates: dict[str, str] = {}
    for item in inbox:
        did = item.get("delivery_id", "")
        summary = item.get("summary") or ""
        surface = item.get("surface") or ""
        sender = (item.get("event") or {}).get("sender_name") or ""
        candidates[did] = f"[{surface}] {sender}: {summary}"

    resolved: list[str] = []
    for ref in raw_references:
        ref_stripped = ref.strip()
        if not ref_stripped:
            continue
        if ref_stripped in candidates:
            resolved.append(ref_stripped)
            continue
        normalized = _normalize_reference(ref_stripped)
        exact = [
            did for did, label in candidates.items()
            if normalized == _normalize_reference(label)
            or normalized == _normalize_reference(did)
        ]
        if len(set(exact)) == 1:
            resolved.append(exact[0])
            continue
        partial = [
            did for did, label in candidates.items()
            if normalized in _normalize_reference(label)
        ]
        if len(set(partial)) == 1:
            resolved.append(partial[0])
            continue
        llm_result = _llm_resolve(ref_stripped, candidates, kind="inbox item")
        if llm_result is not None:
            resolved.append(llm_result)

    return resolved


def _resolve_with_fallback(
    *,
    matches: list[str],
    candidates: dict[str, str],
    kind: str,
    raw_reference: str,
) -> str:
    unique_matches = sorted(set(matches))
    if len(unique_matches) == 1:
        return unique_matches[0]

    if not candidates:
        raise ReferenceResolutionError(
            f"{kind} reference not found: {raw_reference}"
        )

    llm_result = _llm_resolve(raw_reference, candidates, kind=kind)
    if llm_result is not None:
        return llm_result

    if len(unique_matches) > 1:
        raise ReferenceResolutionError(
            f"ambiguous {kind} reference: {raw_reference}"
        )
    raise ReferenceResolutionError(
        f"{kind} reference not found: {raw_reference}"
    )


def _llm_resolve(
    raw_reference: str,
    candidates: dict[str, str],
    *,
    kind: str,
) -> str | None:
    from app.services.config import get_settings

    settings = get_settings()
    api_key = settings.anthropic_api_key
    if not api_key:
        return None

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)
    except Exception:
        return None

    candidate_lines = "\n".join(
        f"  {cid}: {label}" for cid, label in candidates.items()
    )
    prompt = (
        f"A user referred to a {kind} as: \"{raw_reference}\"\n\n"
        f"The available {kind}s are:\n{candidate_lines}\n\n"
        f"Which candidate ID is the user most likely referring to? "
        f"Reply with ONLY the ID string, nothing else. "
        f"If none match, reply with exactly: NONE"
    )

    try:
        response = client.messages.create(
            model=settings.claude_model,
            max_tokens=80,
            messages=[{"role": "user", "content": prompt}],
        )
        answer = response.content[0].text.strip()
        if answer == "NONE" or answer not in candidates:
            return None
        logger.info("LLM resolved %s reference '%s' -> %s", kind, raw_reference, answer)
        return answer
    except Exception:
        logger.warning("LLM reference resolution failed for '%s'", raw_reference, exc_info=True)
        return None


def _normalize_reference(value: str | None) -> str:
    return " ".join(str(value or "").strip().casefold().split())
