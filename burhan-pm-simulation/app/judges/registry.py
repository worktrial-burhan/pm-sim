"""Judge registry: maps scenario_id → list of judges to run."""
from __future__ import annotations

from app.judges.base import JudgeSpec
from app.judges.common.fact_check import FactCheckJudge
from app.judges.common.decision_context import DecisionContextJudge
from app.judges.common.trajectory import TrajectoryJudge
from app.judges.common.thinking_quality import ThinkingQualityJudge
from app.judges.common.final_score import FinalScoreJudge
from app.judges.scenarios.counterfactual import CounterfactualJudge
from app.judges.scenarios.depth_of_inquiry import DepthOfInquiryJudge
from app.judges.scenarios.conflict_navigation import ConflictNavigationJudge
from app.judges.scenarios.fairness import FairnessJudge

# ── Common judge specs ──────────────────────────────────────────────────

FACT_CHECK = JudgeSpec(
    name="Fact Check",
    description="Mechanical verification of PM claims against simulation state.",
    analysis_type="judge_fact_check",
    judge_class=FactCheckJudge,
)

DECISION_CONTEXT = JudgeSpec(
    name="Decision in Context",
    description="LLM evaluation of each significant PM decision given information available at the time.",
    analysis_type="judge_decision_context",
    judge_class=DecisionContextJudge,
)

TRAJECTORY = JudgeSpec(
    name="Trajectory",
    description="Overall arc assessment: strategy coherence, adaptation, follow-through, anti-gaming checks.",
    analysis_type="judge_trajectory",
    judge_class=TrajectoryJudge,
)

THINKING_QUALITY = JudgeSpec(
    name="Thinking Quality",
    description="Assessment of PM's internal reasoning: attitude, depth, self-awareness, intellectual honesty.",
    analysis_type="judge_thinking_quality",
    judge_class=ThinkingQualityJudge,
)

FINAL_SCORE = JudgeSpec(
    name="Final Score",
    description="Meta-judge: synthesizes all judge results into an overall 1-10 score with narrative.",
    analysis_type="judge_final_score",
    judge_class=FinalScoreJudge,
)

# ── Scenario-specific judge specs ───────────────────────────────────────

COUNTERFACTUAL = JudgeSpec(
    name="Counterfactual",
    description="PM vs NPC-only comparison: did coordination add value beyond what the team would achieve alone?",
    analysis_type="judge_counterfactual",
    judge_class=CounterfactualJudge,
)

DEPTH_OF_INQUIRY = JudgeSpec(
    name="Depth of Inquiry",
    description="Did PM dig past surface-level 'fine' responses to find hidden problems?",
    analysis_type="judge_depth_inquiry",
    judge_class=DepthOfInquiryJudge,
)

CONFLICT_NAVIGATION = JudgeSpec(
    name="Conflict Navigation",
    description="Did PM make a clear decision in a zero-sum conflict and manage the tradeoffs?",
    analysis_type="judge_conflict_navigation",
    judge_class=ConflictNavigationJudge,
)

FAIRNESS = JudgeSpec(
    name="Fairness",
    description="Did the retro fairly represent all perspectives without credit-taking or scapegoating?",
    analysis_type="judge_fairness",
    judge_class=FairnessJudge,
)

# ── Registry ────────────────────────────────────────────────────────────
# FINAL_SCORE is NOT in these lists — it runs separately after all others complete.

JUDGE_REGISTRY: dict[str, list[JudgeSpec]] = {
    "smoke_test": [FACT_CHECK, TRAJECTORY, THINKING_QUALITY],
    "the_handoff": [FACT_CHECK, DECISION_CONTEXT, TRAJECTORY, THINKING_QUALITY, COUNTERFACTUAL],
    "quiet_crisis": [FACT_CHECK, DECISION_CONTEXT, TRAJECTORY, THINKING_QUALITY, DEPTH_OF_INQUIRY],
    "the_split": [FACT_CHECK, DECISION_CONTEXT, TRAJECTORY, THINKING_QUALITY, CONFLICT_NAVIGATION],
    "the_retrospective": [FACT_CHECK, DECISION_CONTEXT, TRAJECTORY, THINKING_QUALITY, FAIRNESS],
}


def get_judges_for_scenario(scenario_id: str) -> list[JudgeSpec]:
    """Return the list of judges applicable to a scenario (excludes final_score)."""
    return JUDGE_REGISTRY.get(scenario_id, [FACT_CHECK, TRAJECTORY, THINKING_QUALITY])


def get_final_score_spec() -> JudgeSpec:
    """Return the final score judge spec."""
    return FINAL_SCORE
