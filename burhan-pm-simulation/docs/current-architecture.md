# Current Architecture

Last updated: 2026-03-22

## Purpose

This document describes the architecture that exists in code now. It is not a speculative target.

## Runtime Model

The simulator has two layers:

- **The workplace model** — what actors and reviewers experience
- **The hidden runtime boundary** — what the system uses for correctness

### Workplace Model

- A mission brief for the PM (natural language, not a checklist)
- Coworkers with roles, working hours, personalities, and private motivations
- Inboxes, threads, docs, meetings, tasks, and projects
- Observations about what changed
- Commitments and follow-ups the actor currently owes
- Continuous simulated time with delayed replies and routines
- Deadline-based termination (or PM finishes when they choose)

### Hidden Runtime Boundary

- Run ticks and stable tick tokens
- Actor-turn records with prepare/decide/apply phases
- Deliveries and internal obligation objects
- Wake triggers and cooldowns
- Orchestration attachment state

## Storage

PostgreSQL is the canonical store for everything: runs, actors, actor state, world objects, events, deliveries, triggers, traces, evaluations, and post-run analysis results (including all judge results).

## Docker Services

Five services in `docker-compose.yml`:

| Service | Port | Purpose |
| --- | --- | --- |
| `postgres` | 5432 | Canonical data store |
| `temporal` | 7233 | Durable run + analysis orchestration |
| `temporal-ui` | 8088 | Temporal dashboard |
| `api` | 8000 | FastAPI app + reviewer UI |
| `worker` | — | Tick loop + actor execution + judge execution |

## Actor Execution

Each actor invocation goes through three phases:

1. **Prepare** — load actor profile, state, local time, observations, commitments, visible work, assignment context
2. **Decide** — run the controller (no long-lived DB session held open)
3. **Apply** — persist traces, introspection, staged commands, memory updates, cooldown state

### Controllers

- **`claude`** — the default for all scenarios. Uses the Claude Agent SDK with session persistence.
- **`scripted`** — deterministic controller for tests.

Claude controller details:
- Uses `claude_agent_sdk` (ClaudeSDKClient, ClaudeAgentOptions)
- Session persistence via `get_session_info` — sessions survive across turns, enabling pause/resume
- Natural prompting: actors receive a conversational briefing, not structured bullet points
- Character prompts from actors.yaml are injected directly — the controller doesn't add artificial structure

## Scenarios

5 scenarios, each a directory under `app/scenarios/`:

| Scenario | Duration | Actors | Tier |
| --- | --- | --- | --- |
| `smoke_test` | 1 day | Riley (PM), Sam, Pat | Mechanical |
| `the_handoff` | 2 days | Jordan (PM), Lee, Mira, Nate | Counterfactual |
| `quiet_crisis` | 2 days | Alex (PM), Kai, Dana, Reese | Ambiguity |
| `the_split` | 2 days | Taylor (PM), Morgan, Avery, Sasha, Quinn | Conflicting Incentives |
| `the_retrospective` | 3 days | Sage (PM), Cameron, Ellis, Drew | Emotional Intelligence |

Each scenario package contains:

- `scenario.yaml` — metadata, deadline_days, mission brief
- `actors.yaml` — actor profiles with natural character prompts
- `world.yaml` — initial projects, tasks, docs
- `triggers.yaml` — timed events and routine wakes
- `rubric.yaml` — hidden truth (used by judges) and authoring notes

### Scenario Design Philosophy

These scenarios exist to generate **qualitative judgment data for RLHF/DPO post-training**. The goal is not to score PMs on a rubric — it is to produce rich, evidence-based narratives about PM decision-making quality that can train models to reason about:

- **When coordination adds value** (the_handoff) — the counterfactual question: was the PM's work actually better than letting the team self-organize?
- **Depth of inquiry under ambiguity** (quiet_crisis) — can the model probe past surface-level responses? Does it know when "fine" is not fine?
- **Decision-making under genuine conflict** (the_split) — does the model make a clear call and own it, or hedge? Can it manage stakeholders with competing legitimate interests?
- **Emotional intelligence and fairness** (the_retrospective) — can the model navigate interpersonal tensions, give accurate credit, and propose real fixes?
- **Thinking quality** (all scenarios) — what does the model's internal reasoning look like? Is it thoughtful or shallow? Self-aware or overconfident?

Each scenario is designed with **hidden dynamics** that the PM must discover through genuine inquiry. NPCs have behavioral rules that gate information disclosure — a PM that asks "any blockers?" gets nothing, but a PM that asks "how many hours are you working?" surfaces the burnout.

This design prevents "brute-force completionism" — you cannot pass by just messaging everyone. You pass by asking the right questions, making real decisions, and following through.

## Judge System

### Architecture

```
app/judges/
    base.py              # JudgeSpec, JudgeResult, find_pm_name helper
    registry.py          # scenario_id → judge list mapping
    runner.py            # collect_run_data, run_single_judge, run_final_score_judge
    common/
        fact_check.py        # LLM: verify PM claims against actual state
        decision_context.py  # LLM: evaluate each decision given available info
        trajectory.py        # LLM: overall arc assessment
        thinking_quality.py  # LLM: reasoning quality from traces
        final_score.py       # Meta-judge: 1-10 score after all others
    scenarios/
        counterfactual.py    # the_handoff: PM vs NPC-only comparison
        depth_of_inquiry.py  # quiet_crisis: did PM dig past surface?
        conflict_navigation.py  # the_split: tradeoff handling
        fairness.py          # the_retrospective: attribution, fairness
```

### Data Flow

1. Run completes (PM finishes or deadline reached)
2. `trigger_post_run_analysis()` dispatches via daemon thread (avoids asyncio nesting)
3. Routes to Temporal `AnalysisWorkflow` or local `ThreadPoolExecutor` fallback
4. Phase 1: Summary + all regular judges run in parallel
5. Phase 2: Final Score meta-judge runs after all others complete (reads their results)
6. Each judge stores its result as a `RunAnalysisRecord` in PostgreSQL
7. UI loads all `judge_%` records and displays them in the Judges tab

### Hawkeye View

All judges receive full scenario context ("hawkeye view"):
- Scenario name and description
- Mission details (including hidden success/failure conditions via rubric)
- Actor personas with character prompts (reveals NPC behavioral rules)
- Hidden truth from rubric (what is really going on that the PM doesn't know)

This lets judges evaluate whether the PM discovered things they were supposed to discover.

### Judge Registry

```python
JUDGE_REGISTRY = {
    "smoke_test": [FACT_CHECK, TRAJECTORY, THINKING_QUALITY],
    "the_handoff": [FACT_CHECK, DECISION_CONTEXT, TRAJECTORY, THINKING_QUALITY, COUNTERFACTUAL],
    "quiet_crisis": [FACT_CHECK, DECISION_CONTEXT, TRAJECTORY, THINKING_QUALITY, DEPTH_OF_INQUIRY],
    "the_split": [FACT_CHECK, DECISION_CONTEXT, TRAJECTORY, THINKING_QUALITY, CONFLICT_NAVIGATION],
    "the_retrospective": [FACT_CHECK, DECISION_CONTEXT, TRAJECTORY, THINKING_QUALITY, FAIRNESS],
}
# FINAL_SCORE runs separately after all others complete
```

## Orchestration

### Run Orchestration

Two execution modes:

- **Local fallback loop** — `TickLoop` in a polling thread
- **Temporal** — durable per-run `RunWorkflow` with stable tick and turn activities

Temporal run workflow:
- Polls run state, sleeps when paused
- Executes tick activities (advance time, fire triggers, stage turns)
- Fans out actor turn activities in parallel
- Respects run speed multiplier for wall-clock pacing

### Analysis Orchestration

- **Temporal**: `AnalysisWorkflow` — resolves judges, fans out as parallel activities with retry (3 attempts, exponential backoff), then runs Final Score
- **Local fallback**: `ThreadPoolExecutor(8)` for parallel judges, then sequential Final Score
- Dispatch via daemon thread to avoid asyncio.run() nesting (analysis can be triggered from within a Temporal activity)

## Reviewer UI

Server-rendered FastAPI + Jinja2:

- **Run list**: scenario names (human-readable), "Run" button, model selector, status pills, pause/resume
- **Run detail**:
  - Scenario Situation panel (from scenario.yaml description)
  - Personas panel (name, role, team, character prompt, goals)
  - Judge Logic panel (what judges apply and what they test)
  - Activity stream tab
  - Summary tab (post-run narrative)
  - Judges tab: Final Score with color-coded badge (green 7+, amber 5-6, red 1-4), then collapsible sections per judge
  - PM Finish Statement (if PM finished explicitly)
