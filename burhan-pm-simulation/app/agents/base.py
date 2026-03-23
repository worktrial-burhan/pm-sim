from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from app.agents.session_state import ActorTurnContext


@dataclass
class ControllerDecision:
    introspection_entries: list[dict[str, Any]] = field(default_factory=list)
    decision_signal: str | None = None
    final_reasoning: str | None = None
    session_id: str | None = None
    cost_usd: float | None = None


class BaseController(ABC):
    @abstractmethod
    async def decide(self, context: ActorTurnContext, toolkit: Any) -> ControllerDecision:
        raise NotImplementedError
