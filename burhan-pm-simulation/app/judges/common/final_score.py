"""Final Score Judge: meta-judge that runs after all other judges and produces an overall score."""
from __future__ import annotations

import json
import re
from typing import Any

from app.judges.base import JudgeResult, find_pm_name


class FinalScoreJudge:
    name = "Final Score"
    description = (
        "Meta-judge that runs after all other judges complete. Reads their results, "
        "applies its own intuition, and produces an overall 1-10 score with narrative justification."
    )
    analysis_type = "judge_final_score"

    def evaluate(self, run_data: dict[str, Any]) -> JudgeResult:
        """Synthesize all judge results into a final score."""
        from app.judges.runner import call_judge_llm

        scenario_ctx = run_data.get("scenario_context", {})
        assignment = run_data.get("assignment", {})
        events = run_data.get("events", [])
        traces = run_data.get("traces", [])

        pm_name = find_pm_name(run_data.get("actors", []))

        # Load all existing judge results for this run
        judge_results = run_data.get("judge_results", {})

        prompt = f"""You are a senior evaluator producing the FINAL SCORE for a PM simulation run.

You have access to all individual judge assessments AND the raw run data. Your job is to synthesize everything into a single, honest evaluation with a score from 1-10.

IMPORTANT: Do NOT mechanically average the judges. Use your own judgment. A PM who aced the trajectory but completely missed the core scenario challenge should score low. A PM who struggled with efficiency but showed genuine insight into hidden problems might score high. Trust your gut — you are the final word.

SCENARIO CONTEXT:
Scenario: {scenario_ctx.get("scenario_name", "Unknown")}
Description: {scenario_ctx.get("scenario_description", "N/A")}
Hidden truth: {json.dumps(scenario_ctx.get("hidden_truth", {}), indent=2, default=str)}
Mission: {json.dumps(scenario_ctx.get("mission", {}), indent=2, default=str)}

Assignment:
{json.dumps(assignment, indent=2, default=str)}

INDIVIDUAL JUDGE RESULTS:
{json.dumps(judge_results, indent=2, default=str)}

KEY RUN STATISTICS:
- Total events: {len(events)}
- PM name: {pm_name}
- PM actions: {len([e for e in events if e.get("actor") == pm_name])}
- Other actor actions: {len([e for e in events if e.get("actor") != pm_name and e.get("actor")])}
- Run duration: {run_data.get("start_sim_time")} to {run_data.get("end_sim_time")}

Now produce your final assessment:

1. First, reflect on what this scenario was REALLY testing. What was the core challenge? What would a great PM have done vs a mediocre one?

2. Then assess: How did this PM actually perform against that core challenge? Not against a checklist — against the spirit of what good PM work looks like in this situation.

3. Consider each judge's assessment, but weigh them by relevance. The scenario-specific judge matters most. Thinking quality matters. Trajectory matters. Fact-check is sanity-checking, not the main event.

4. Assign a score from 1-10:
   - 1-2: Failed fundamentally. Worse than having no PM.
   - 3-4: Below expectations. Missed the core challenge.
   - 5-6: Adequate. Did the obvious things but nothing more.
   - 7-8: Good. Showed genuine PM skill and insight.
   - 9-10: Exceptional. Would hire this PM. Demonstrated real craft.

5. End with a single paragraph: if you were coaching this PM, what's the ONE thing they should work on?

CRITICAL: Your score must appear on its own line in this exact format:
FINAL_SCORE: N

Write your full narrative assessment first, then the score line, then the coaching paragraph."""

        narrative = call_judge_llm(prompt)

        # Extract the score from the narrative
        score = _extract_score(narrative)

        return JudgeResult(
            judge_name=self.name,
            analysis_type=self.analysis_type,
            narrative=narrative,
            evidence=[{"final_score": score}] if score is not None else [],
        )


def _extract_score(text: str) -> int | None:
    """Extract the FINAL_SCORE: N from the judge output."""
    match = re.search(r"FINAL_SCORE:\s*(\d+)", text)
    if match:
        score = int(match.group(1))
        return max(1, min(10, score))
    return None
