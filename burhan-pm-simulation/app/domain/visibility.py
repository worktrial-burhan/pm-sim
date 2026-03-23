from __future__ import annotations

from typing import Any


def normalize_visibility(visibility: dict[str, Any] | None) -> dict[str, Any]:
    if not visibility:
        return {"scope": "company"}
    return visibility


def actor_can_view(
    *,
    actor_id: str,
    actor_role: str | None,
    actor_team: str | None,
    visibility: dict[str, Any] | None,
) -> bool:
    scope = normalize_visibility(visibility)
    scope_name = scope.get("scope", "company")

    if scope_name == "company":
        return True
    if scope_name == "private":
        return actor_id == scope.get("owner_actor_id")
    if scope_name == "actors":
        return actor_id in set(scope.get("actor_ids", []))
    if scope_name == "team":
        return actor_team is not None and actor_team == scope.get("team")
    if scope_name == "role":
        return actor_role is not None and actor_role == scope.get("role")
    if scope_name == "admin":
        return actor_role == "admin"

    return False
