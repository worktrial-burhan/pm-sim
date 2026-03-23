"""Base classes for the judge system."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class JudgeResult:
    """Output of a single judge evaluation."""
    judge_name: str
    analysis_type: str
    narrative: str
    evidence: list[dict[str, Any]] = field(default_factory=list)


class Judge(Protocol):
    """Protocol that all judges implement."""

    name: str
    description: str
    analysis_type: str

    def evaluate(self, run_data: dict[str, Any]) -> JudgeResult:
        """Run the judge against collected run data and return a qualitative result."""
        ...


@dataclass
class JudgeSpec:
    """Specification for a judge — used in the registry."""
    name: str
    description: str
    analysis_type: str
    judge_class: type


def find_pm_name(actors: list[dict[str, Any]]) -> str | None:
    """Find the PM actor name, handling both old ('pm') and new ('Product Manager') role formats."""
    for actor in actors:
        role = (actor.get("role") or "").lower().strip()
        if role in ("pm", "product manager"):
            return actor.get("name")
    return None
