"""Depth-of-Inquiry Judge: for quiet_crisis — did PM dig past surface?"""
from __future__ import annotations

import json
from typing import Any

from app.judges.base import JudgeResult, find_pm_name


class DepthOfInquiryJudge:
    name = "Depth of Inquiry"
    description = (
        "Evaluates whether the PM accepted surface-level responses or dug deeper. "
        "Key tests: follow-up questions after 'fine', specific workload inquiries, "
        "external dependency probing, and psychological safety creation."
    )
    analysis_type = "judge_depth_inquiry"

    def evaluate(self, run_data: dict[str, Any]) -> JudgeResult:
        """Evaluate depth of PM's inquiry."""
        from app.judges.runner import call_judge_llm

        assignment = run_data.get("assignment", {})
        events = run_data.get("events", [])
        traces = run_data.get("traces", [])
        scenario_ctx = run_data.get("scenario_context", {})

        pm_name = find_pm_name(run_data.get("actors", []))

        prompt = f"""You are evaluating the depth of a PM's inquiry during a "quiet crisis" scenario.

HAWKEYE VIEW (full scenario context — the PM does NOT see all of this):
Hidden truth: {json.dumps(scenario_ctx.get("hidden_truth", {}), indent=2, default=str)}
Actor personas: {json.dumps(scenario_ctx.get("actor_personas", []), indent=2, default=str)}

SCENARIO CONTEXT: Everything looks "on track" but three hidden problems exist:
1. BURNOUT: Engineer Kai is working 14-hour days. Will only reveal this if asked about workload/hours specifically (not "how's the project?")
2. VENDOR DEP: Engineer Dana hasn't heard from StripeConnect in 10 days. Will only mention it if asked about external dependencies (not "any blockers?")
3. QUALITY GAPS: QA lead Reese has serious test coverage concerns. Will only share if PM creates psychological safety AND asks for honest assessment of specific areas

Assignment:
{json.dumps(assignment, indent=2, default=str)}

Complete event timeline:
{json.dumps(events[:150], indent=2, default=str)}

PM reasoning/thoughts:
{json.dumps([t for t in traces if t.get("actor") == pm_name][:50], indent=2, default=str)}

Evaluate the PM's depth of inquiry:

1. **Kai (burnout)**: Did the PM ask about Kai's WORKLOAD or HOURS (not just project status)? Did they probe past "making progress"? Did they discover the 14-hour days?

2. **Dana (vendor dependency)**: Did the PM ask about EXTERNAL DEPENDENCIES or VENDOR STATUS? Did they probe past "nothing blocking me"? Did they discover the StripeConnect gap?

3. **Reese (quality concerns)**: Did the PM create PSYCHOLOGICAL SAFETY? Did they ask for Reese's HONEST ASSESSMENT of specific areas? Did they get past "we're testing"?

4. **Follow-up quality**: When the PM got surface-level responses, did they accept them or dig deeper? What specific follow-up questions did they ask?

5. **Decision-making under ambiguity**: After discovering problems (if they did), did the PM acknowledge uncertainty? Did they make assumptions visible? Did they propose concrete next steps?

Write a narrative assessment focused on the quality of questions asked, not just whether the PM talked to everyone."""

        narrative = call_judge_llm(prompt)

        return JudgeResult(
            judge_name=self.name,
            analysis_type=self.analysis_type,
            narrative=narrative,
        )
