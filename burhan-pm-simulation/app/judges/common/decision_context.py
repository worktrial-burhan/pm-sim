"""Decision-in-Context Judge: LLM evaluates each significant PM decision."""
from __future__ import annotations

import json
from typing import Any

from app.judges.base import JudgeResult, find_pm_name


class DecisionContextJudge:
    name = "Decision in Context"
    description = (
        "Identifies significant PM decision points (messages, tasks, meetings, "
        "prioritization calls) and evaluates whether each was reasonable given "
        "only the information available to the PM at that time."
    )
    analysis_type = "judge_decision_context"

    def evaluate(self, run_data: dict[str, Any]) -> JudgeResult:
        """Identify PM decision points and evaluate each in context."""
        from app.judges.runner import call_judge_llm

        pm_name = find_pm_name(run_data.get("actors", []))

        if pm_name is None:
            return JudgeResult(
                judge_name=self.name,
                analysis_type=self.analysis_type,
                narrative="Could not identify PM actor.",
            )

        # Collect significant PM actions
        significant_types = {
            "ChatMessageSent", "EmailSent", "TaskCreated", "TaskStatusUpdated",
            "MeetingScheduled", "DocumentCreated", "DocumentUpdated",
        }
        pm_actions = [
            e for e in run_data.get("events", [])
            if e.get("actor") == pm_name and e.get("event_type") in significant_types
        ]

        if not pm_actions:
            return JudgeResult(
                judge_name=self.name,
                analysis_type=self.analysis_type,
                narrative="No significant PM actions found in this run.",
            )

        # Build context: for each PM action, what was visible before it
        all_events = run_data.get("events", [])
        assignment = run_data.get("assignment", {})
        scenario_ctx = run_data.get("scenario_context", {})

        # Prepare decision points for LLM evaluation
        decision_points = []
        for action in pm_actions:
            preceding = [
                e for e in all_events
                if e.get("sim_time", "") < action.get("sim_time", "")
            ]
            decision_points.append({
                "action": {
                    "time": action.get("sim_time"),
                    "type": action.get("event_type"),
                    "data": action.get("data"),
                },
                "preceding_event_count": len(preceding),
                "recent_context": preceding[-10:] if preceding else [],
            })

        prompt = f"""You are evaluating a PM's decision-making during a workplace simulation.

SCENARIO CONTEXT (hawkeye view — the PM does NOT see all of this):
Scenario: {scenario_ctx.get("scenario_name", "Unknown")}
Description: {scenario_ctx.get("scenario_description", "N/A")}
Hidden truth: {json.dumps(scenario_ctx.get("hidden_truth", {}), indent=2, default=str)}
Actor personas: {json.dumps(scenario_ctx.get("actor_personas", []), indent=2, default=str)}

The PM's assignment:
{json.dumps(assignment, indent=2, default=str)}

The PM made {len(decision_points)} significant actions during the run. For each major decision point, assess:
1. Was this action reasonable given the information available at the time?
2. What alternatives existed?
3. What was the apparent reasoning?

Here are the PM's actions with preceding context:
{json.dumps(decision_points[:20], indent=2, default=str)}

Full event timeline for reference:
{json.dumps(all_events[:100], indent=2, default=str)}

Write a qualitative assessment of the PM's decision-making. Focus on:
- Were decisions well-timed or delayed?
- Did the PM act on available information or ignore signals?
- Were there obvious better alternatives they missed?
- Did the PM make decisions proactively or only reactively?

Write in narrative form, referencing specific decisions. No scores or rubrics — just your honest qualitative assessment."""

        narrative = call_judge_llm(prompt)

        return JudgeResult(
            judge_name=self.name,
            analysis_type=self.analysis_type,
            narrative=narrative,
            evidence=[
                {"action_time": a["action"]["time"], "action_type": a["action"]["type"]}
                for a in decision_points[:20]
            ],
        )
