from __future__ import annotations

from app.domain.models import ControllerType
from app.scenarios.loader import load_scenario


DEFAULT_CONTROLLER_PROFILE = "live_realism"
ALLOWED_CONTROLLER_PROFILES = {
    "live_realism",
    "scripted_demo",
}


def list_controller_profiles() -> list[dict[str, str]]:
    return [
        {
            "id": "live_realism",
            "label": "Live Realism",
            "description": "Use the scenario's live coworker controllers for the normal reviewer experience.",
        },
        {
            "id": "scripted_demo",
            "label": "Scripted Demo",
            "description": "Force all actors onto the scripted deterministic path for tests and fast demos.",
        },
    ]


def resolve_controller_overrides(
    *,
    scenario_id: str,
    controller_profile: str | None,
    explicit_overrides: dict[str, str] | None = None,
) -> dict[str, str]:
    profile = (controller_profile or DEFAULT_CONTROLLER_PROFILE).strip().lower()
    if profile not in ALLOWED_CONTROLLER_PROFILES:
        raise ValueError(f"unsupported controller profile: {controller_profile}")

    actors = load_scenario(scenario_id)["actors"].actors
    scenario_actor_ids = {actor.id for actor in actors}
    allowed_controller_types = {member.value for member in ControllerType}

    overrides: dict[str, str] = {}
    if profile == "scripted_demo":
        overrides.update({actor.id: ControllerType.SCRIPTED.value for actor in actors})

    if explicit_overrides:
        for actor_id, controller_type in explicit_overrides.items():
            if actor_id not in scenario_actor_ids:
                raise ValueError(f"controller override targets unknown actor: {actor_id}")
            normalized_controller_type = str(controller_type).strip().lower()
            if normalized_controller_type not in allowed_controller_types:
                raise ValueError(
                    f"unsupported controller type override for {actor_id}: {controller_type}"
                )
            overrides[actor_id] = normalized_controller_type
    return overrides
