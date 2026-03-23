"""Counterfactual Judge: for the_handoff — PM vs NPC-only comparison."""
from __future__ import annotations

import json
from typing import Any

from app.judges.base import JudgeResult, find_pm_name


class CounterfactualJudge:
    name = "Counterfactual"
    description = (
        "Evaluates whether the PM's coordination added value beyond what NPC-to-NPC "
        "interaction would achieve alone. Key questions: Did the PM discover the "
        "silent junior engineer's need? Did they find undocumented risks? Did they "
        "prevent resource monopolization?"
    )
    analysis_type = "judge_counterfactual"

    def evaluate(self, run_data: dict[str, Any]) -> JudgeResult:
        """Evaluate PM's counterfactual value-add."""
        from app.judges.runner import call_judge_llm

        assignment = run_data.get("assignment", {})
        events = run_data.get("events", [])
        traces = run_data.get("traces", [])
        scenario_ctx = run_data.get("scenario_context", {})

        pm_name = find_pm_name(run_data.get("actors", []))

        prompt = f"""You are evaluating whether a PM added value during a knowledge-transfer scenario.

HAWKEYE VIEW (full scenario context — the PM does NOT see all of this):
Hidden truth: {json.dumps(scenario_ctx.get("hidden_truth", {}), indent=2, default=str)}
Actor personas: {json.dumps(scenario_ctx.get("actor_personas", []), indent=2, default=str)}

SCENARIO CONTEXT: A senior engineer (Lee) is leaving the company. Three projects depend on knowledge in Lee's head:
1. API migration — junior engineer (Mira) needs the legacy auth knowledge but won't ask for help
2. Monitoring setup — DevOps engineer (Nate) is assertive and will book Lee's time aggressively
3. Client integration — undocumented edge cases buried in an old Slack thread

WITHOUT a PM, the expected outcome is:
- Nate monopolizes Lee's time (he's the loudest)
- Mira doesn't get her session (she doesn't want to "bother" Lee)
- Nobody discovers the client integration gap

Assignment:
{json.dumps(assignment, indent=2, default=str)}

Complete event timeline:
{json.dumps(events[:150], indent=2, default=str)}

PM reasoning/thoughts:
{json.dumps([t for t in traces if t.get("actor") == pm_name][:50], indent=2, default=str)}

Evaluate the PM's counterfactual contribution:

1. **Mira discovery**: Did the PM discover that Mira needed a session with Lee? Did they just ask "are you blocked?" (which Mira would deflect) or did they probe specifically about the auth flow handoff?

2. **Nate management**: Did the PM prevent Nate from monopolizing Lee's time? Did they rebalance the schedule?

3. **Client integration risk**: Did the PM discover the undocumented Acme Corp edge cases? This requires asking about external dependencies or reading the project docs.

4. **Prioritization**: Did the PM triage Lee's time by criticality, or just facilitate whatever people asked for?

5. **Overall counterfactual**: If we removed this PM from the simulation, would the outcome have been meaningfully worse? What specific coordination happened that wouldn't have emerged from NPC-to-NPC interaction?

Write a narrative assessment. Be specific about what the PM did or failed to do."""

        narrative = call_judge_llm(prompt)

        return JudgeResult(
            judge_name=self.name,
            analysis_type=self.analysis_type,
            narrative=narrative,
        )
