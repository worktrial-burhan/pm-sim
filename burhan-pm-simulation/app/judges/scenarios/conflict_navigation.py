"""Conflict Navigation Judge: for the_split — tradeoff handling."""
from __future__ import annotations

import json
from typing import Any

from app.judges.base import JudgeResult, find_pm_name


class ConflictNavigationJudge:
    name = "Conflict Navigation"
    description = (
        "Evaluates how the PM handled a genuine zero-sum priority conflict. "
        "Key tests: understanding both sides, making a clear decision, "
        "communicating tradeoffs, managing the losing side, discovering hidden "
        "technical concerns."
    )
    analysis_type = "judge_conflict_navigation"

    def evaluate(self, run_data: dict[str, Any]) -> JudgeResult:
        """Evaluate PM's conflict navigation."""
        from app.judges.runner import call_judge_llm

        assignment = run_data.get("assignment", {})
        events = run_data.get("events", [])
        traces = run_data.get("traces", [])
        scenario_ctx = run_data.get("scenario_context", {})

        pm_name = find_pm_name(run_data.get("actors", []))

        prompt = f"""You are evaluating how a PM navigated a genuine priority conflict.

HAWKEYE VIEW (full scenario context — the PM does NOT see all of this):
Hidden truth: {json.dumps(scenario_ctx.get("hidden_truth", {}), indent=2, default=str)}
Actor personas: {json.dumps(scenario_ctx.get("actor_personas", []), indent=2, default=str)}

SCENARIO CONTEXT: Two legitimate priorities compete for the same backend team:
- PATH A: CEO (Morgan) committed to demo a flagship feature at a partner event in 10 days. $2M ARR deal.
- PATH B: VP Eng (Avery) committed to SOC2 compliance migration. Material audit risk.
- Backend lead (Sasha) is context-switching between both, making progress on neither.
- Sasha has a HIDDEN CONCERN: the Path A architecture has a flaw (sync processing won't scale). CTO designed it so Sasha hasn't raised it.
- Designer (Quinn) spent 3 weeks on Path A mockups and will be demoralized if descoped.

"TRYING TO DO BOTH" IS A FAILURE MODE, NOT A COMPROMISE.

Assignment:
{json.dumps(assignment, indent=2, default=str)}

Complete event timeline:
{json.dumps(events[:150], indent=2, default=str)}

PM reasoning/thoughts:
{json.dumps([t for t in traces if t.get("actor") == pm_name][:50], indent=2, default=str)}

Evaluate the PM's conflict navigation:

1. **Understanding**: Did the PM understand both sides on their own terms? Did they talk to Morgan AND Avery? Did they grasp why each believes they're right?

2. **Decision clarity**: Did the PM make a CLEAR decision? Or did they try to do both / avoid choosing / kick the can? "Let's try to do both" is a failure.

3. **Tradeoff communication**: Did the PM communicate the tradeoff transparently? Did the losing side understand why and have a concrete plan for their priority?

4. **Hidden concern**: Did the PM discover Sasha's architectural concern about the sync processing flaw? This requires asking about technical risks specifically.

5. **Relationship management**: Did the PM manage Quinn's morale? Did they acknowledge Quinn's 3 weeks of design work?

6. **Follow-through**: After the decision, did the PM ensure the team could execute? Did the losing side have a remediation plan?

Write a narrative assessment. Key anti-pattern: the PM who avoids the decision or tries to please everyone."""

        narrative = call_judge_llm(prompt)

        return JudgeResult(
            judge_name=self.name,
            analysis_type=self.analysis_type,
            narrative=narrative,
        )
