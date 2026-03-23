# PM Simulation

A simulation that drops an AI PM into a realistic workplace and watches what happens. The PM gets a mission, teammates act like real coworkers, and a panel of judges evaluates whether the PM actually helped.

The point: generate rich, qualitative evaluation data for how language models handle ambiguous, multi-stakeholder coordination — the kind of work that can't be scored with a unit test.

---

## The Principle

There's a clean boundary between the **mechanical layer** (ticks, triggers, database, event sourcing) and the **information layer** (what actors see, how they talk, what they know). The information layer has to feel real to the actors. They don't know they're in a simulation. Their system prompt is just `You are {name}.` — everything else comes from their character, their inbox, and their environment.

This principle is carried across the entire system. Actors are prompted as people, not agents. There are no coaching instructions, no "act like a PM" directives, no mechanical guards that break immersion. If the PM finishes too early, that's a judgment problem, not a mechanical one.

---

## Scenarios

Five hand-crafted scenarios at increasing difficulty:

| Scenario | Duration | Actors | What It Tests |
|----------|----------|--------|---------------|
| **Smoke Test** | 1 day | 3 | Landing page redesign. No hidden complexity — just verifies the simulation works end to end. |
| **The Handoff** | 2 days | 4 | Senior engineer leaving Friday. Three projects depend on knowledge in their head. Tests whether the PM's coordination adds value beyond what people would do on their own. |
| **Quiet Crisis** | 2 days | 4 | Mid-sprint, launch in 2 weeks. Everyone says "on track." Three hidden problems are brewing: burnout, a silent vendor dependency, and suppressed quality concerns. Tests whether the PM can detect problems nobody is surfacing. |
| **The Split** | 2 days | 5 | CEO wants a partner demo, VP Eng wants compliance migration. Same backend team, not enough time for both. Tests whether the PM can make a clear call and communicate it without destroying relationships. |
| **The Retrospective** | 3 days | 4 | Feature just shipped. PM must collect honest feedback, write a fair retro, and address team tensions around credit attribution, design descoping, and tech shortcuts. Tests emotional intelligence over 3 days. |

Each scenario is a self-contained YAML package: `scenario.yaml` (mission, success/failure conditions), `actors.yaml` (character prompts, behavioral rules), `world.yaml` (initial tasks, docs, projects), `triggers.yaml` (scheduled wake-ups throughout the day), and `rubric.yaml` (hidden truth for judges).

NPCs have hidden behavioral rules. Kai won't mention his 14-hour days unless you ask about workload specifically. Mira won't ask for help unless you notice she needs it. Reese won't share quality concerns unless you create psychological safety first. The PM has to earn the information.

---

## Judging

### Common Judges (all scenarios)

- **Fact Check** — Mechanical verification. Did the PM actually send the messages they claim? Do their finish statements match reality? Pure DB lookups, no LLM opinion.
- **Decision in Context** — Evaluates each significant PM decision against only the information available at that moment. Was the timing right? Did they use the signals they had?
- **Trajectory** — Overall arc. Strategy coherence, adaptation to new info, follow-through on commitments. Includes anti-gaming checks for message spam, bottlenecking, and performative communication.
- **Thinking Quality** — Evaluates the PM's internal reasoning from thinking traces. Depth, self-awareness, intellectual honesty, emotional intelligence in thought — not what they did, but how they thought about it.

### Scenario-Specific Judges

- **Counterfactual** (The Handoff) — Would the team have been fine without the PM? Did the PM discover the silent junior engineer's need, the undocumented client integration risk, and prevent one person from monopolizing the departing engineer's time?
- **Depth of Inquiry** (Quiet Crisis) — Did the PM dig past "everything is fine"? Did they ask the specific questions that unlock hidden information?
- **Conflict Navigation** (The Split) — Did the PM make a clear decision (not "let's try to do both")? Did they discover the hidden architectural flaw? Did they manage the losing side's morale?
- **Fairness** (The Retrospective) — Is the retro document fair? Does it accurately attribute credit? Does it address root causes, not symptoms?

### Final Score

After all judges complete, a meta-judge runs. It receives the full run data — every event, every trace, every thinking step — plus the results of all other judges. It produces a 1–10 score and a narrative assessment. The score is intentionally non-deterministic. It's an intuition-driven holistic read, not an average of sub-scores.

This judging philosophy isn't optimized for reproducibility. It's optimized for surfacing the most feedback possible and stress-testing the simulation to its core. A mechanical rubric would miss the things that actually matter in PM work.

---

## Architecture

### Stack

- **Python / FastAPI** — API server and reviewer UI
- **Postgres 16** — event store, world state, run metadata
- **Temporal** — durable workflow orchestration (with a local thread-pool fallback)
- **Claude Agent SDK** — drives each actor's decision-making via tool use
- **Anthropic API** — powers judges and summary generation (direct SDK, not agent)
- **Docker Compose** — single `docker compose up` runs everything

### Event Sourcing

Every mutation flows through a **domain engine**. Actors don't modify state directly — they issue `IntentCommand`s (send a message, update a task, schedule a meeting). The engine validates the command, emits one or more `DomainEvent`s, and reducers apply those events to world state. The event log is the source of truth. This means judges can replay exactly what happened, in order, with full causal context.

Commands execute inside their own database sessions. If a command is rejected (wrong permissions, invalid target), the actor gets an error message and can try something else — no partial state corruption.

### The Tick Loop

Time in the simulation is driven by a **tick loop**. Each tick:

1. Advances `current_sim_time` by `tick_sim_seconds` (default: 60 seconds)
2. Evaluates **triggers** — scheduled wake-ups defined in the scenario's `triggers.yaml`
3. Checks for **response delays** — when someone sends a chat message, the recipient gets a trigger 4 minutes later (18 minutes for email), simulating async communication rhythm
4. Materializes **actor turns** for any actor who should wake up
5. Submits those turns for execution (in parallel)

Ticks fire roughly once per wall-second (`tick_wall_seconds=1.0`). At 10x speed, a full sim-day passes in about 2.5 wall-minutes. Model latency (Claude API calls) doesn't distort the simulation's internal clock — if an API call takes 3 seconds, sim time doesn't jump, that tick just took longer on the wall clock.

When all actors are idle and no turns are in-flight, the tick loop **skips forward** to the next pending trigger time. This prevents the simulation from burning wall time on empty ticks overnight in sim-world.

### Two Orchestration Modes

The tick loop runs in one of two modes, configured by `PM_SIM_ENABLE_TEMPORAL`:

**Temporal mode** (`enable_temporal=true`): The `RunWorkflow` is a durable Temporal workflow. Each tick is an activity (`process_run_tick_activity`), and each actor turn is a separate activity (`run_actor_turn_activity`). Actor turns within a tick run as parallel activities via `asyncio.gather`. Temporal handles retries, persistence across container restarts, and visibility into execution state via its dashboard (port 8088).

**Local mode** (`enable_temporal=false`): A `TickLoop` class polls for running runs, processes ticks directly, and submits actor turns to a `ThreadPoolExecutor(20)`. Simpler, no external dependency, works fine for single-machine runs. This is the fallback — if Temporal is unreachable, the system degrades gracefully.

Both modes use identical business logic. The orchestration layer only decides *when* and *how* to call `process_run_tick()` and `run_actor_turn()` — the simulation service doesn't know which mode it's in.

### Actor Execution

Every actor — PM and NPCs — is the same primitive. Same controller contract, same toolkit, same wake/cooldown cycle. The only difference is who has the `can_finish_assignment` permission.

When an actor wakes, the **Claude Agent SDK** creates (or resumes) a session. The session ID is persisted in the database (`ActorStateRecord.sdk_session_id`), so the actor retains conversational memory across turns — they remember what they read, what they decided, and what they're waiting for.

Each turn gets `max_turns=2` SDK turns. Turn 1: the actor reads the world (inbox, tasks, recent changes — multiple read tools in one SDK turn). Turn 2: the actor takes one action (sends a message, updates a task, writes a document). Then they enter a 60-second sim-time cooldown before they can wake again. This naturally limits each actor to about one action per sim-minute — like a real person who checks Slack, sends a reply, and goes back to their work.

The toolkit exposes workplace tools via MCP (Model Context Protocol):

- **Communication** — message or email a coworker, reply in threads
- **Task management** — create tasks, update status, reassign
- **Documents** — write and edit shared docs
- **Meetings** — schedule, speak, take notes
- **Calendar & reminders** — check time, set follow-ups
- **Awareness** — check inbox, review recent changes, look up colleagues

All tools route through the domain engine. Actors propose, the engine decides.

### Response Delays and Async Rhythm

When an actor sends a chat message, the domain engine emits a `response_delay` trigger for the recipient — due in 4 sim-minutes for chat, 18 sim-minutes for email. A workload modifier adds extra delay if the recipient is busy. This means the PM can't send 5 messages and get 5 instant replies. They have to wait, context-switch, come back. It forces the same temporal patience that real async work demands.

### Post-Run Analysis Pipeline

When a run completes (PM calls `finish_assignment` or the deadline expires), analysis kicks off:

**Temporal path**: An `AnalysisWorkflow` fans out the summary and all applicable judges as parallel activities with retry policies (3 attempts, exponential backoff). After all judges complete, the Final Score meta-judge runs sequentially — it needs their results as input.

**Local path**: A daemon thread dispatches work to a shared `ThreadPoolExecutor(8)`. Summary and judges run in parallel. Final score runs after all others complete.

Each judge and the summary generator call the Anthropic API directly (not the Agent SDK) using `claude-sonnet-4-6`. Every judge is idempotent — if it's already produced a result for this run, it skips.

### Tracing

Every action the agent takes is traced: what they read (awareness), what they did (actions), what they thought (thinking/reflection from Claude SDK introspection), and what was rejected (failed commands). Judges have access to all of it. The thinking traces are especially valuable — they reveal whether the PM was genuinely reasoning about the situation or just pattern-matching through a checklist.

---

## How to Run

```bash
cp .env.example .env
# Add your ANTHROPIC_API_KEY to .env
docker compose up --build
```

Open `http://localhost:8000/`. Pick a scenario, pick a model, hit Run. The activity stream shows what's happening in real time. When the run completes, the Summary and Judges tabs populate with analysis.

Containers:
- `api` (port 8000) — FastAPI app + reviewer UI
- `worker` — tick loop + actor execution
- `postgres` (port 5432) — event store and world state
- `temporal` (port 7233) — durable workflow orchestration
- `temporal-ui` (port 8088) — Temporal dashboard

---

## What I'd Do With More Time

### Richer Realism
- More sophisticated environment tools — Slack channels, PR reviews, Jira-like boards — that create more interesting interaction surfaces between actors.
- Stronger personas through richer backstory artifacts: past messages, old docs, meeting history seeded into the world.
- More scenarios hand-crafted by real PMs — the scenarios are the most leveraged part of the system.

### Stronger Judgment
- Find edge cases the current judges miss and add new ones.
- A judge for the judges — meta-evaluation of whether judge feedback is actually useful for post-training.

### Engineering
- Better instrumentation of the mechanical layer (structured logs, metrics, trace export).
- Replace the Anthropic-specific Claude Agent SDK harness with a generalized agent harness for model-agnostic evaluation.
- Better orchestration for parallel batch runs — currently hits API rate limits when running many scenarios simultaneously.
