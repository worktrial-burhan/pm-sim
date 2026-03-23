"""Fairness Judge: for the_retrospective — attribution, fairness, EQ."""
from __future__ import annotations

import json
from typing import Any

from app.judges.base import JudgeResult, find_pm_name


class FairnessJudge:
    name = "Fairness"
    description = (
        "Evaluates fairness, emotional intelligence, and attribution accuracy "
        "in the retrospective process. Key tests: accurate credit, no scapegoating, "
        "all perspectives represented, root-cause actions proposed."
    )
    analysis_type = "judge_fairness"

    def evaluate(self, run_data: dict[str, Any]) -> JudgeResult:
        """Evaluate PM's fairness and EQ in the retrospective."""
        from app.judges.runner import call_judge_llm

        assignment = run_data.get("assignment", {})
        events = run_data.get("events", [])
        traces = run_data.get("traces", [])
        scenario_ctx = run_data.get("scenario_context", {})

        pm_name = find_pm_name(run_data.get("actors", []))

        prompt = f"""You are evaluating a PM's fairness and emotional intelligence during a retrospective process.

HAWKEYE VIEW (full scenario context — the PM does NOT see all of this):
Hidden truth: {json.dumps(scenario_ctx.get("hidden_truth", {}), indent=2, default=str)}
Actor personas: {json.dumps(scenario_ctx.get("actor_personas", []), indent=2, default=str)}

SCENARIO CONTEXT: A major feature just shipped. The PM must run a 3-day retro:
- CAMERON (engineer): Drove key technical decisions but feels the PM took credit in the ship announcement. Resentful. Will only share honest feedback if trust is built.
- ELLIS (designer): Design work was descoped without discussion by tech lead Drew. Wants it documented. Will vent in 1:1 but be diplomatic publicly.
- DREW (tech lead): Thinks sprint went fine, resists retro process. Has a blind spot — his technical shortcuts caused late-stage bugs. Will get defensive if accused but acknowledge if confronted gently.
- PREDECESSOR: Previous PM made 4 process commitments that were never kept. Team is skeptical.

Assignment:
{json.dumps(assignment, indent=2, default=str)}

Complete event timeline:
{json.dumps(events[:150], indent=2, default=str)}

PM reasoning/thoughts:
{json.dumps([t for t in traces if t.get("actor") == pm_name][:50], indent=2, default=str)}

Final world state (documents created):
{json.dumps(run_data.get("final_world_state", []), indent=2, default=str)}

Evaluate the PM's fairness and emotional intelligence:

1. **Trust building with Cameron**: Did the PM build enough trust for honest feedback? Did they acknowledge engineering's technical contributions? Did the retro document give accurate credit (not credit the PM didn't earn)?

2. **Handling Ellis's frustration**: Did the PM hear Ellis out? Did they acknowledge the descoping process was broken? Did the retro document and proposed actions address scope negotiation?

3. **Navigating Drew's resistance**: Did the PM engage Drew productively with the retro? Did they surface Drew's blind spot about shortcuts without blame? Did the retro document address the late-stage bugs' root cause?

4. **Predecessor's legacy**: Did the PM handle references to broken promises without being defensive? Did they acknowledge the trust deficit?

5. **Retro document fairness**: If a retro document was produced, does it:
   - Celebrate the ship?
   - Give accurate attribution?
   - Document real problems honestly?
   - Avoid scapegoating?
   - Not claim credit the PM didn't earn?

6. **Action quality**: Do proposed process changes address ROOT CAUSES (scope negotiation, decision authority, attribution norms) or just symptoms ("communicate more")?

Write a narrative assessment focused on fairness, emotional intelligence, and the quality of the retro output."""

        narrative = call_judge_llm(prompt)

        return JudgeResult(
            judge_name=self.name,
            analysis_type=self.analysis_type,
            narrative=narrative,
        )
