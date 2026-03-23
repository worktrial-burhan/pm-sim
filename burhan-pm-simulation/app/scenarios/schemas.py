from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ScenarioMission(BaseModel):
    title: str
    visible_brief: str
    primary_goal: str | None = None
    constraints: list[str] = Field(default_factory=list)
    done_when_guidance: list[str] = Field(default_factory=list)
    visible_completion_checks: list["RubricCheck"] = Field(default_factory=list)
    hidden_success_conditions: list[str] = Field(default_factory=list)
    failure_conditions: list[str] = Field(default_factory=list)
    hard_stop_conditions: list[str] = Field(default_factory=list)


class ScenarioMetadata(BaseModel):
    id: str
    name: str
    description: str
    start_sim_time: str
    deadline_days: int = 1
    default_run_status: str = "paused"
    mission: ScenarioMission | None = None


class ActorSeed(BaseModel):
    id: str
    name: str
    role: str
    team: str | None = None
    controller_type: str = "scripted"
    timezone: str = "America/Los_Angeles"
    working_hours: dict[str, Any] = Field(default_factory=dict)
    permissions: dict[str, Any] = Field(default_factory=dict)
    character_prompt: str = ""
    profile: dict[str, Any] = Field(default_factory=dict)
    goals: dict[str, Any] = Field(default_factory=dict)
    beliefs: dict[str, Any] = Field(default_factory=dict)
    relationships: dict[str, Any] = Field(default_factory=dict)
    commitments: dict[str, Any] = Field(default_factory=dict)
    workload: dict[str, Any] = Field(default_factory=dict)


class ActorsFile(BaseModel):
    actors: list[ActorSeed]


class WorldObjectSeed(BaseModel):
    id: str
    kind: str
    title: str
    owner_actor_id: str | None = None
    parent_object_id: str | None = None
    visibility: dict[str, Any] = Field(default_factory=dict)
    state: dict[str, Any] = Field(default_factory=dict)


class WorldFile(BaseModel):
    objects: list[WorldObjectSeed]


class TriggerSeed(BaseModel):
    id: str
    trigger_type: str
    due_offset_minutes: int
    actor_id: str | None = None
    object_id: str | None = None
    priority: int = 0
    data: dict[str, Any] = Field(default_factory=dict)


class TriggersFile(BaseModel):
    triggers: list[TriggerSeed]


class RubricCheck(BaseModel):
    id: str
    label: str
    type: str
    weight: float = 1.0
    object_id: str | None = None
    state_key: str | None = None
    equals: Any | None = None
    not_equals: Any | None = None
    event_type: str | None = None
    event_types: list[str] = Field(default_factory=list)
    actor_id: str | None = None
    recipient_actor_id: str | None = None
    before_sim_time: str | None = None
    data_contains: dict[str, Any] = Field(default_factory=dict)


class RubricFile(BaseModel):
    hidden_truth: dict[str, Any] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
    checks: list[RubricCheck] = Field(default_factory=list)  # Optional for new scenarios using judge system


ScenarioMission.model_rebuild()
