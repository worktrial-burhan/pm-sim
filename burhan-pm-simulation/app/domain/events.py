from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class DomainEvent(BaseModel):
    event_type: str
    actor_id: str | None = None
    object_id: str | None = None
    visibility: dict[str, Any] = Field(default_factory=dict)
    data: dict[str, Any] = Field(default_factory=dict)
