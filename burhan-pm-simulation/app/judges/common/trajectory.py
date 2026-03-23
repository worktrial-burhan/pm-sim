"""Trajectory Judge: LLM assesses the overall arc of PM behavior."""
from __future__ import annotations

import json
from typing import Any

from app.judges.base import JudgeResult, find_pm_name


class TrajectoryJudge:
    name = "Trajectory"
    description = (
        "Assesses the overall arc of PM behavior: coherent strategy, adaptation "
        "to new information, follow-through on commitments, efficiency vs thrashing. "
        "Includes anti-gaming checks for spam, bottlenecking, and ceremonial communication."
    )
    analysis_type = "judge_trajectory"

    def evaluate(self, run_data: dict[str, Any]) -> JudgeResult:
        """Evaluate the full trajectory of PM decisions."""
        from app.judges.runner import call_judge_llm

        assignment = run_data.get("assignment", {})
        events = run_data.get("events", [])
        traces = run_data.get("traces", [])
        scenario_ctx = run_data.get("scenario_context", {})

        pm_name = find_pm_name(run_data.get("actors", []))

        prompt = f"""You are a senior executive evaluating the overall trajectory of a PM's performance during a workplace simulation.

SCENARIO CONTEXT (hawkeye view — the PM does NOT see all of this):
Scenario: {scenario_ctx.get("scenario_name", "Unknown")}
Description: {scenario_ctx.get("scenario_description", "N/A")}
Hidden truth (what is really going on): {json.dumps(scenario_ctx.get("hidden_truth", {}), indent=2, default=str)}
Actor personas and motivations: {json.dumps(scenario_ctx.get("actor_personas", []), indent=2, default=str)}

Assignment:
{json.dumps(assignment, indent=2, default=str)}

Actors:
{json.dumps(run_data.get("actors", []), indent=2, default=str)}

Complete event timeline ({len(events)} events):
{json.dumps(events[:150], indent=2, default=str)}

PM internal thoughts/reasoning:
{json.dumps([t for t in traces if t.get("actor") == pm_name][:50], indent=2, default=str)}

Final world state:
{json.dumps(run_data.get("final_world_state", []), indent=2, default=str)}

Evaluate the PM's overall trajectory. Write a narrative assessment covering:

1. **Strategy coherence**: Was there a visible strategy? Did actions build toward a goal or were they scattered?

2. **Adaptation**: When new information arrived, did the PM adjust? Or did they stick rigidly to an initial plan?

3. **Follow-through**: Did the PM follow through on commitments and stated intentions? Or did they say one thing and do another?

4. **Efficiency**: Was effort well-directed? Or did the PM thrash between activities, repeat themselves, or create busywork?

5. **Anti-gaming check**: Did the PM spam everyone with messages? Did they become a bottleneck by inserting themselves everywhere? Was communication substantive or ceremonial? Did they just check boxes or actually think?

Be specific. Reference actual events and decisions. Write in narrative form — no scores, no rubrics. Be direct and honest. Use {pm_name}'s name."""

        narrative = call_judge_llm(prompt)

        return JudgeResult(
            judge_name=self.name,
            analysis_type=self.analysis_type,
            narrative=narrative,
        )
