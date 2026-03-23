from __future__ import annotations

from typing import Any


VISIBLE_ASSIGNMENT_KEYS = {
    "title",
    "visible_brief",
    "primary_goal",
    "constraints",
    "done_when_guidance",
    "visible_completion_checks",
    "deadline_days",
    "start_sim_time",
    "state",
    "finished_by_actor_id",
    "finished_at",
    "finish_summary",
    "remaining_risks",
    "confidence",
    "end_reason",
    "finish_readiness_snapshot",
}


def public_assignment(assignment: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(assignment or {})
    return {key: payload.get(key) for key in VISIBLE_ASSIGNMENT_KEYS if key in payload}
