from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class ActorTurnContext:
    run_id: str
    current_sim_time: datetime
    local_current_time: str
    actor_id: str
    actor_name: str
    actor_role: str
    actor_team: str | None
    profile: dict[str, Any]
    permissions: dict[str, Any]
    work_availability: dict[str, Any] = field(default_factory=dict)
    goals: dict[str, Any] = field(default_factory=dict)
    beliefs: dict[str, Any] = field(default_factory=dict)
    relationships: dict[str, Any] = field(default_factory=dict)
    declared_commitments: dict[str, Any] = field(default_factory=dict)
    workload: dict[str, Any] = field(default_factory=dict)
    actor_directory: dict[str, str] = field(default_factory=dict)
    observations: list[dict] = field(default_factory=list)
    current_commitments: list[dict] = field(default_factory=list)
    visible_objects: list[dict] = field(default_factory=list)
    assignment: dict[str, Any] = field(default_factory=dict)
    turn_id: str | None = None
    model: str | None = None
