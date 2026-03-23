from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class IntentCommand(BaseModel):
    command_type: str
    actor_id: str
    issued_at_sim: datetime
    target_ref: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)
