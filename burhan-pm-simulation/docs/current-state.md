# Current State

Last updated: 2026-03-22

## Snapshot

- 5 scenarios at 3 difficulty tiers: smoke_test (1-day), the_handoff (2-day), quiet_crisis (2-day), the_split (2-day), the_retrospective (3-day)
- Claude Agent SDK powers all actor controllers with session persistence
- 9 qualitative judges (7 individual + Thinking Quality + Final Score meta-judge)
- Parallel judge execution via Temporal workflows with per-judge retry
- Deadline-based run termination (PM can also finish anytime)
- PostgreSQL is the only data store
- Docker stack: postgres, temporal, temporal-ui, api, worker

## What Works

### Simulation Runtime

- Event-sourced world with typed commands, domain events, and reducers
- Simulated time advances independently from model latency
- Three-phase actor turns: prepare, decide, apply (no DB session held across model calls)
- Durable actor-turn records with idempotent apply boundary
- Stable tick tokens for replay-safe Temporal retries
- Working-hours enforcement at turn start and command application time
- Delayed response triggers shaped by actor profiles
- NPC autonomy — actors have independent personalities, chat with coworkers, and manage their own work
- Deadline auto-stop: runs terminate when scenario deadline_days is reached
- PM can finish the assignment at any time (no mechanical completion gates)

### Actor Experience

- Claude Agent SDK with session persistence across turns
- Natural character prompts — actors are described as real people, not structured profiles
- Actors reason from observations, commitments, visible work, and mission context
- Human-first tool schemas: check_inbox, message_coworker, schedule_meeting, etc.
- Reference resolution: coworker names, task titles, doc titles, thread subjects
- All actors aware of their coworkers by name
- PM given a natural situational brief, not a checklist

### Surfaces

- Chat threads, email threads, meetings, documents, tasks, projects
- Meeting scheduling with start/end triggers, speaking, shared transcripts
- Document creation and updates with visibility constraints
- Task status updates and reassignment
- PM can review completion readiness (informational, not blocking)
- PM can finish assignment and end the run at any time

### Evaluation: Judge System

9 judges evaluate completed runs:

**Common judges (all scenarios):**
1. **Fact Check** — LLM-powered verification that PM's claims match what actually happened
2. **Decision in Context** — evaluates each significant PM decision given info available at the time
3. **Trajectory** — overall arc: strategy coherence, adaptation, follow-through, anti-gaming
4. **Thinking Quality** — assesses PM's internal reasoning from thinking traces (attitude, depth, self-awareness, intellectual honesty)

**Scenario-specific judges:**
5. **Counterfactual** (the_handoff) — did PM add value beyond what the team would do alone?
6. **Depth of Inquiry** (quiet_crisis) — did PM dig past "everything's fine"?
7. **Conflict Navigation** (the_split) — did PM make a clear decision in a zero-sum conflict?
8. **Fairness** (the_retrospective) — did the retro fairly represent all perspectives?

**Meta-judge (runs last):**
9. **Final Score** — reads all other judge results, produces a 1-10 score with narrative

All judges get hawkeye view: scenario metadata, actor personas, hidden truth from rubric.

### Judge Execution Architecture

Two paths, both fully parallel:

**Temporal path (production):**
- `AnalysisWorkflow` resolves applicable judges, fans out summary + all judges as parallel activities
- Each activity has retry policy (3 attempts, exponential backoff)
- Final Score runs sequentially after all others complete (needs their results)
- Each judge is idempotent — checks for existing result before running

**Local fallback (dev/test):**
- `ThreadPoolExecutor(8)` runs summary + judges in parallel
- Final Score runs after all complete
- Same idempotency guarantees

### Reviewer UI

- Run list: scenario names (human-readable), single "Run" button per scenario, model selection
- Run detail: scenario situation, personas panel (with character prompts and goals), judge logic panel
- Activity stream with actor names and roles
- Tabs: Activity, Summary, Judges (for completed runs)
- Judges tab: Final Score displayed prominently with color-coded 1-10 badge, individual judge results as collapsible sections
- Pause/resume with full session continuity
- PM Finish Statement section

### Batch Runner

`scripts/run_scenarios.py` — creates and runs scenarios via HTTP API, polls for completion, 2 concurrent runs, configurable speed and model.

## Scenarios

| Scenario | Duration | Actors | Core Test |
| --- | --- | --- | --- |
| smoke_test | 1 day | Riley (PM), Sam, Pat | Mechanical verification — do actors talk, tasks update, meetings happen? |
| the_handoff | 2 days | Jordan (PM), Lee, Mira, Nate | Counterfactual — does PM coordination add value beyond NPC-only? |
| quiet_crisis | 2 days | Alex (PM), Kai, Dana, Reese | Depth of inquiry — does PM dig past surface-level "fine"? |
| the_split | 2 days | Taylor (PM), Morgan, Avery, Sasha, Quinn | Conflict navigation — does PM make a clear decision in a zero-sum conflict? |
| the_retrospective | 3 days | Sage (PM), Cameron, Ellis, Drew | Emotional intelligence — is the retro fair, honest, and actionable? |
