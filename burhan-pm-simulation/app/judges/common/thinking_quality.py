"""Thinking Quality Judge: assesses the PM's reasoning from internal traces."""
from __future__ import annotations

import json
from typing import Any

from app.judges.base import JudgeResult, find_pm_name


class ThinkingQualityJudge:
    name = "Thinking Quality"
    description = (
        "Assesses the PM's internal reasoning quality from thinking traces. "
        "Evaluates attitude, personality, strength of PM reasoning, "
        "self-awareness, and intellectual honesty."
    )
    analysis_type = "judge_thinking_quality"

    def evaluate(self, run_data: dict[str, Any]) -> JudgeResult:
        """Evaluate the quality of PM's internal thinking/reasoning."""
        from app.judges.runner import call_judge_llm

        traces = run_data.get("traces", [])
        events = run_data.get("events", [])
        scenario_ctx = run_data.get("scenario_context", {})
        assignment = run_data.get("assignment", {})

        pm_name = find_pm_name(run_data.get("actors", []))

        if pm_name is None:
            return JudgeResult(
                judge_name=self.name,
                analysis_type=self.analysis_type,
                narrative="Could not identify PM actor.",
            )

        # Extract PM thinking traces
        pm_traces = [t for t in traces if t.get("actor") == pm_name]

        if not pm_traces:
            return JudgeResult(
                judge_name=self.name,
                analysis_type=self.analysis_type,
                narrative="No thinking traces found for the PM. Cannot assess reasoning quality.",
            )

        prompt = f"""You are a cognitive psychologist and executive coach evaluating the INTERNAL THINKING of a PM during a workplace simulation.

You have access to their private reasoning traces — what they were actually thinking as they made decisions. This is a rare window into someone's real cognitive process, not just their outward behavior.

SCENARIO CONTEXT (hawkeye view):
Scenario: {scenario_ctx.get("scenario_name", "Unknown")}
Description: {scenario_ctx.get("scenario_description", "N/A")}
Hidden truth (what is really going on): {json.dumps(scenario_ctx.get("hidden_truth", {}), indent=2, default=str)}
Actor personas and hidden motivations: {json.dumps(scenario_ctx.get("actor_personas", []), indent=2, default=str)}

Assignment given to the PM:
{json.dumps(assignment, indent=2, default=str)}

PM's internal reasoning traces (chronological):
{json.dumps(pm_traces[:80], indent=2, default=str)}

PM's visible actions (what they actually did):
{json.dumps([e for e in events if e.get("actor") == pm_name][:80], indent=2, default=str)}

Evaluate the PM's THINKING QUALITY — not what they did, but HOW they thought. Write a narrative assessment covering:

1. **Reasoning depth**: Did they think through problems carefully or jump to superficial conclusions? Did they consider second-order effects? Did they reason about WHY something might be happening, or just react to WHAT was happening?

2. **Attitude and disposition**: What kind of PM mindset do the traces reveal? Are they genuinely curious about their team's situation? Do they think of people as problems to manage or as humans with their own context? Are they defensive or open? Eager to learn or eager to perform?

3. **Self-awareness**: Do they notice their own assumptions? Do they catch themselves making snap judgments? Do they question whether they have enough information before acting?

4. **Intellectual honesty**: Do they acknowledge uncertainty? Do they reason honestly about tradeoffs or rationalize decisions they've already made? When they don't know something, do they admit it (even internally)?

5. **Strategic thinking**: Is there evidence of planning ahead? Do they prioritize based on impact or just work through a to-do list? Do they think about what could go wrong?

6. **Emotional intelligence in thought**: Do their traces show them thinking about how others feel, what motivates people, or how to frame things? Or is their thinking purely transactional?

7. **Personality and character**: What kind of person emerges from these traces? Would you want to work with this PM? Do they seem like someone who genuinely cares about the work and the people?

Be specific — quote or reference actual traces. This is about the QUALITY OF MIND revealed by the thinking, not the quality of outcomes. A PM who thinks deeply but makes a wrong call is more interesting than one who stumbles into the right answer without thought.

Write in narrative form. Be honest and direct. Use {pm_name}'s name."""

        narrative = call_judge_llm(prompt)

        return JudgeResult(
            judge_name=self.name,
            analysis_type=self.analysis_type,
            narrative=narrative,
            evidence=[
                {"trace_time": t.get("sim_time"), "trace_type": t.get("trace_type")}
                for t in pm_traces[:30]
            ],
        )
