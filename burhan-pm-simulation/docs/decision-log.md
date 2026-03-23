# Decision Log

This is an append-only record of architectural decisions and later deviations.

## 2026-03-20

### Decision: Keep the strong simulation semantics, simplify the runtime

Context:

- The original architecture preserved the right semantics but carried too much implementation weight for v1.

Decision:

- Keep event-sourced truth, actor symmetry, partial observability, traces, and continuous simulated time.
- Simplify the runtime to a Python tick loop backed by PostgreSQL current-state tables.

Why:

- This preserves the core ideas without burying them under unnecessary infrastructure.

### Decision: Use PostgreSQL as the primary store

Context:

- The initial plan split storage aggressively across PostgreSQL and object storage.

Decision:

- Store events, state, triggers, deliveries, traces, and evaluations primarily in PostgreSQL.
- Use MinIO only for large blobs.

Why:

- This keeps v1 simpler while preserving room for growth.

### Decision: Keep Temporal, but bound its role tightly

Context:

- You explicitly wanted Temporal in the stack, but it should not become the semantic center of the simulation.

Decision:

- Use Temporal only for durable run lifecycle control and selected activities such as controller invocation and evaluation checkpointing.

Why:

- This satisfies the stack choice without splitting simulation semantics across two orchestration systems.

### Decision: Keep `Delivery` explicit

Context:

- Some abstractions were collapsed for v1, but inboxes and unread state still require a concrete mechanic.

Decision:

- Keep deliveries as explicit rows rather than treating all actor-visible change as an implicit query.

Why:

- We need delivered-at time, unread state, and actor wake semantics.

### Decision: Add lightweight continuity docs

Context:

- Future agents will lose context as implementation progresses.

Decision:

- Maintain three lightweight docs:
  - `current-state.md`
  - `build-journal.md`
  - `decision-log.md`

Why:

- This preserves continuity without adding heavy process or machine-managed task state.

### Decision: Do not over-specify the execution plan before the first slice works

Context:

- The initial parallel plan decomposed the build too aggressively before any runtime code existed.

Decision:

- Replace the detailed task graph with a lightweight execution strategy.
- Treat contracts as provisional until the first real end-to-end slice works.
- Parallelize only after the first slice has proven the actual code boundaries.

Why:

- The codebase has not yet earned rigid decomposition.
- The first implemented flow will reveal the real boundaries better than speculative planning.

## 2026-03-21

### Decision: Make the actor-visible tool surface read like workplace actions, not API calls

Context:

- The simulator already used human-first references like coworker names and task titles.
- But the exposed tool names still looked like backend methods such as `get_my_inbox` and `send_chat`.

Decision:

- Keep internal toolkit method names and domain command names stable.
- Rename the actor-visible tool registry to workplace-action names such as:
  - `check_inbox`
  - `read_conversation`
  - `message_coworker`
  - `review_commitments`
  - `wrap_up_assignment`
- Use the registry as the single source of truth for both prompt exposure and toolkit trace naming.

Why:

- This preserves clean internal boundaries while making the actor-facing surface feel more like real work and less like an API catalog.
- It avoids compatibility sludge by making the actor-visible layer intentional rather than maintaining parallel old/new names.

### Decision: Only call a Claude episode `budget_exhausted` when it truly failed to land another action

Context:

- The controller previously labeled some episodes as `budget_exhausted` even when the final allowed step had already staged real work, including assignment wrap-up.

Decision:

- If the last allowed Claude step stages one or more commands, record the episode as `commands_staged`.
- Reserve `budget_exhausted` for episodes that actually hit the step cap without landing another action.
- Increase the default Claude step budget to `50` to make live runs more liberal before they hit that fallback.

Why:

- The old label was mechanically misleading in logs and reviews.
- It made successful closing actions look like indecisive browsing.
- A larger default step budget reduces false “paused” endings in realistic live runs.

### Decision: Prefer explicit registries and shared read models over branch ladders and duplicated route assembly

Context:

- As the simulation grew, reducers, presenters, and routes started accumulating long event-type condition chains and repeated API/UI payload assembly.

Decision:

- Move reducer dispatch to explicit registries.
- Move event/observation rendering to explicit registries.
- Centralize shared API/UI read-model assembly in one module.
- Centralize the Temporal attach handshake in one orchestration service.

Why:

- This keeps the code closer to the domain model.
- It reduces “random if statement” growth.
- It makes future extension add new handlers or helpers instead of editing several unrelated ladders.

### Decision: Decompose the domain engine by responsibility, not by arbitrary helper extraction

Context:

- The simulation semantics had grown, and `app/domain/engine.py` had become the main place where command validation, trigger orchestration, async-work mechanics, and shared invariants were all accumulating.

Decision:

- Keep `app/domain/engine.py` as a stable facade.
- Move shared invariants into `engine_common.py`.
- Move delivery/obligation mechanics into `engine_attention.py`.
- Move command handlers into `engine_commands.py`.
- Move trigger handlers into `engine_triggers.py`.

Why:

- This keeps the public API stable while preventing one file from becoming the default dumping ground for every new simulation mechanic.
- The split follows actual domain responsibilities rather than generic “utils” extraction.

### Decision: Make concrete reference flows part of the implementation plan

Context:

- The prior plan described system parts, but not the most important thing: how the pieces actually work together in code.

Decision:

- Add concrete flows for:
  - actor wake on tick
  - `get_my_inbox`
  - `send_chat`
  - run creation

Why:

- These flows are the real implementation guide.
- They are more useful than a large speculative task breakdown at this stage.

### Decision: Keep actor memory in the simulator, not in the model session

Context:

- The first live-agent slice will use Claude-backed controllers.
- Long-lived model sessions are convenient, but they are not reliable enough to be the canonical source of actor memory or history.

Decision:

- Store durable actor memory, notes, deliveries, commitments, and relationship state in the simulator.
- Treat any Claude session as an optional convenience cache, not as canonical state.
- Reconstruct each actor turn from stored actor state, visible world context, recent deliveries, and recent traces.

Why:

- This preserves replayability and recoverability.
- It keeps actor history stable across context loss and session resets.
- It prevents the model runtime from becoming a hidden second database.

### Decision: Keep scenario IDs human-readable and scope them by run in storage

Context:

- Scenario files use stable IDs like `actor_pm` and `obj_project_launch`.
- Rewriting every seed ID at compile time would make scenarios harder to author and inspect.

Decision:

- Allow seed IDs to repeat across runs by scoping actors, actor state, world objects, and triggers by `(run_id, id)` in storage.

Why:

- This keeps scenario authoring simple.
- It preserves readable IDs in traces and APIs.
- It allows multiple runs of the same scenario to coexist safely in one database.

### Decision: Inbox reads must be explicit acknowledgments, not implicit side effects

Context:

- Automatically marking inbox items read during `get_my_inbox` made unread state untrustworthy and would have broken live agents.

Decision:

- `get_my_inbox` is a pure read.
- Acknowledgment requires an explicit `mark_inbox_items_read` call.

Why:

- Unread state should represent whether an actor has actually acknowledged an item.
- Wake behavior and inbox semantics stay defensible when controllers become more complex.

### Decision: Temporal owns the background loop when enabled

Context:

- You explicitly wanted Temporal to be real, not decorative.
- The local tick loop is still useful as a fallback path for lightweight development.

Decision:

- Keep the simulation domain logic in shared Python services.
- When Temporal is enabled, a supervisor workflow owns wall-clock progression and dispatches activities to:
  - list running runs
  - advance simulated time
  - execute actor turns

Why:

- Temporal becomes the real background orchestrator.
- The domain engine remains shared and testable.
- The local fallback loop still exists for fast iteration without Temporal.

### Decision: Temporal orchestration is per-run, not one global processing workflow

Context:

- A single global workflow serialized all runs behind the slowest actor turn.
- We want Temporal to add real orchestration value without introducing nondeterministic world mutation inside a run.

Decision:

- Use one Temporal workflow per run.
- Each run workflow honors that run's own `tick_wall_seconds`.
- Different runs can progress independently.
- Actor turns within a run stay ordered for now so world mutation remains deterministic.

Why:

- This removes whole-system head-of-line blocking across runs.
- It keeps the unit of orchestration aligned with the unit of simulation state.
- It preserves a clean path to future parallel decision generation without parallel write races.

### Decision: Actor-facing acknowledgment tools must be actor-scoped in storage

Context:

- The live Claude tool loop can only be safe if storage-layer acknowledgments cannot cross actor boundaries.

Decision:

- Delivery acknowledgment updates are scoped by `run_id`, `actor_id`, and delivered time.

### Decision: Actor-facing tools should resolve human references, not force opaque ids

Context:

- The world state is keyed by canonical ids, but real coworkers think in names, titles, and email subjects.
- Live PM behavior was failing in exactly that gap: correct social judgment, wrong opaque identifier.

Decision:

- Keep canonical ids inside the domain engine.
- Add a reference-resolution layer at the toolkit boundary so actor-facing tools can accept visible names, titles, and subjects when they resolve unambiguously.

Why:

- This keeps the world model rigorous without forcing unrealistic agent behavior.
- It moves reference translation into a single intentional layer instead of relying on prompt discipline.

### Decision: Scenario-emitted signal events may be reducer no-ops

Context:

- State-patch triggers can legitimately emit scenario-level signal events such as `LaunchRiskEscalated`.
- Those events matter for logs and evaluation, but they do not necessarily correspond to a new reducer mutation.

Decision:

- Allow events to carry an explicit reducer hint of `noop`.
- Reducers still fail loudly on unknown events by default, but intentional signal-only events no longer crash the runtime.

Why:

- This preserves strict reducer behavior while allowing scenario-authored signals to exist as first-class events.

### Decision: Runtime turn records stay internal; actor behavior is framed as attention windows

Context:

- The simulator still needs atomic persistence and retry boundaries.
- The actors should not experience those boundaries as “turns.”

Decision:

- Keep durable actor-turn records internally for idempotency and orchestration.
- Treat them semantically as hidden attention windows, not as actor-facing ontology.
- Remove turn/controller/simulation framing from live actor prompts.

Why:

- This preserves runtime correctness without leaking board-game semantics into the simulated workplace.

### Decision: Use one tool registry for implementation, prompt exposure, and Anthropic tool definitions

Context:

- The previous live-controller path defined tools three times:
  - toolkit method
  - controller dispatch
  - prompt schema

Decision:

- Introduce a single registry of tool name, method binding, description, and input schema.
- Use that registry for:
  - dispatch
  - CLI fallback prompt manifests
  - Anthropic native tool definitions

Why:

- This removes a real maintenance hazard and keeps the work-tool surface intentional.

### Decision: The primary live actor path should use Anthropic native tool use

Context:

- Raw JSON action envelopes force the model into an out-of-world control posture.
- The Anthropic API already supports native tool use.

Decision:

- Use native Anthropic tool use when the API backend is available.
- Keep the JSON loop only as a fallback path for CLI/non-native environments.

Why:

- Native tool use lets the actor stay in natural voice while still interacting through typed workplace affordances.

### Decision: Message deliveries create delayed attention, not instant bot wakeups

Context:

- Instantly waking actors on every unread message feels bot-like.
- Real coworkers batch, defer, and respond inside working hours.

Decision:

- Deliveries now create `response_delay` triggers.
- Wake timing is shaped by local working hours and per-actor response behavior.
- Calendar meeting starts can still wake immediately when the meeting begins.

Why:

- This makes asynchronous behavior feel more human without losing deterministic scheduling.

### Decision: Actions consume simulated minutes

Context:

- A coworker replying in chat, updating a task, and editing a document should not make all three events appear at the same timestamp.

Decision:

- Action tools stage commands with increasing `issued_at_sim`.
- Apply-phase advances the run clock as those actions are committed.

Why:

- This makes intra-episode behavior legible in time and creates more realistic event timelines.

Why:

- This preserves actor isolation even if a controller guesses or hallucinates another delivery ID.
- Storage invariants do not depend on controller correctness.

### Decision: Claude controllers use the local CLI as a decision runtime, not as a memory store

Context:

- The local environment has a usable `claude` CLI.
- We still want simulator-owned memory and typed tool mediation.

Decision:

### Decision: Default reviewer runs should be live-realism-first

Context:

- The scenario had real live actor behavior in code, but the normal create-run path still produced scripted runs.

Decision:

- Make the scenario defaults `claude`.
- Keep deterministic scripted behavior behind an explicit `scripted_demo` controller profile.

Why:

- The default product experience should match the product thesis.
- Deterministic coverage still exists, but it stops shaping the reviewer surface by accident.

### Decision: Obligations live on the same world-object backbone as other work

Context:

- Async work needed to become durable and causal without introducing a second parallel state model.

Decision:

- Model obligations as `WorldObject(kind="obligation")`.
- Drive them through normal events, reducers, visibility, and triggers.

Why:

- This keeps the simulation ontology coherent.
- Messages, meetings, reminders, and later work surfaces can all converge on one async work primitive.

### Decision: Reasoning should be reviewer-visible but still stored as traces

Context:

- Agent reasoning was already being stored, but only as raw trace payloads.

Decision:

- Keep cognition in traces.
- Add a combined reviewer activity log that merges events with reasoning summaries such as `Burhan PM thinking: ...`.

Why:

- Evaluation and review need access to actor reasoning.
- The system does not need a second bespoke reasoning store.

- Use the local `claude` CLI for controller decision steps.
- Keep a bounded tool loop in Python.
- Offload large prompt/response artifacts to MinIO when enabled.

Why:

- This gives us a real Claude backend without turning the CLI session into the simulator's memory.
- It preserves the command-staging pattern and simulator-owned state.
- It makes MinIO serve a real purpose instead of sitting unused in the stack.

### Decision: Keep local tests isolated from the live `.env`

Context:

- The repo now includes a live `.env` that enables MinIO, Temporal, and Anthropic-backed actors.
- That configuration is correct for Docker smoke runs, but it should not leak into deterministic offline tests.

Decision:

- Force safe offline defaults in the autouse test fixture:
  - disable Temporal
  - disable MinIO
  - clear `ANTHROPIC_API_KEY`
  - default the Claude backend away from the live network path unless a test explicitly overrides it

Why:

- Unit and integration tests should stay fast, deterministic, and offline.
- The live stack path should be an explicit opt-in test surface, not an accidental side effect of local test execution.

### Decision: Use an unsandboxed Temporal workflow runner for this v1

Context:

- The workflow module imports simulator configuration code that touches filesystem path resolution at import time.
- Temporal's default workflow sandbox rejected that import path during worker startup.

Decision:

- Run the `RunWorkflow` with `UnsandboxedWorkflowRunner()` in v1.

Why:

- The workflow logic here is thin orchestration around activities, not business logic heavy with application code.
- This removes a real operational blocker without hiding the fact that the workflow code is currently coupled to app modules that are not sandbox-clean.
- It keeps Temporal real in the runtime while avoiding a premature module-layer refactor just to satisfy sandbox purity.

### Decision: Validate the live path with one deterministic run plus one Claude-backed run

Context:

- A single all-Claude smoke run would prove the live agent path, but it would make surface-coverage assertions too brittle.
- A single scripted run would prove surfaces, but not the live Anthropic/Temporal/MinIO path.

Decision:

- The live smoke harness runs:
  - one scripted coverage run
  - one Claude-backed run with live PM/NPC actors

Why:

- The scripted run proves the multi-surface mechanics deterministically.
- The Claude-backed run proves the real agent/runtime path.
- Together they test what matters without turning the smoke harness into a flaky model-behavior oracle.

### Decision: Temporal retries stop at a durable actor-turn boundary, not at raw `(run_id, actor_id)` wakeups

Context:

- Temporal actor-turn activities can retry after timeout or worker failure.
- If the activity target is just `(run_id, actor_id)`, a retry can duplicate traces, deliveries, and world mutations.

Decision:

- Materialize `ActorTurnRecord` rows during ticking.
- Execute Temporal actor turns by `turn_id`.
- Persist controller decisions before apply, then make the apply phase idempotent.

Why:

- This makes Temporal add real runtime value instead of just wrapping a polling loop.
- It keeps side effects replay-safe without turning the controller itself into the source of truth.
- It also gives us a clean future boundary for parallel decision generation.

### Decision: Temporal ticks use stable workflow-owned tick tokens

Context:

- Even with durable actor turns, a retried tick activity can still advance simulated time twice if the activity succeeds but its result is lost before the workflow sees it.

Decision:

- The workflow now issues stable per-run tick tokens.
- `process_run_tick()` persists the result for a given `(run_id, tick_token)` and reuses it on retry.

Why:

- This makes the tick activity replay-safe instead of merely fast.
- It pushes Temporal use one layer deeper: the workflow now owns durable units for both ticking and turns.

### Decision: `finish_assignment` is a terminal mutation for the current attention window

Context:

- The PM can stage multiple commands inside one attention window.
- Once `finish_assignment` completes the run, any later staged command in that same apply-phase becomes semantically invalid.

Decision:

- After each applied command, refresh run state.
- If the run is no longer running, stop applying further staged commands and record that later commands were skipped.

Why:

- Completion should be terminal in world semantics, not just eventually terminal after the rest of the batch lands.

### Decision: Response-delay triggers are scoped to the exact delivery that created them

### Decision: Closure readiness must be visible before finish, not only judged afterward

Context:

- The PM could reach the correct social outcome but still close the run prematurely because closure requirements were not legible in-world.
- The evaluator was catching the miss after the fact, but the PM-facing surface did not clearly expose what still needed to be buttoned up.

Decision:

- Let each mission declare visible completion checks.
- Give the PM a dedicated `review_completion_readiness` tool.
- Keep `finish_assignment` permissive, but capture a finish-readiness snapshot containing blockers and warnings at the moment of closure.

Why:

- Closure should be a real PM skill, not a hidden evaluator trap.
- The PM needs a visible readiness surface before choosing to stop.
- Reviewers need to see what was still open when the PM ended the assignment.

### Decision: Alignment checks should follow visible mission semantics, not hidden medium-specific gotchas

Context:

- The prior `launch_crunch` closure path effectively wanted a stakeholder update on one exact communication surface even when a semantically equivalent grounded update had already happened elsewhere.

Decision:

- Align visible mission checks and rubric checks around the real coordination outcome.
- In `launch_crunch`, a grounded written PM update to revenue can now be satisfied by either chat or email.

Why:

- The simulator should reward sound coordination semantics first.
- Surface-specific constraints should only matter when the world actually makes them matter.

### Decision: Separate actor ontology from runtime mechanism

Context:

- The simulator needed hidden bookkeeping for deliveries, triggers, and due work.
- Letting actors reason directly over those mechanism objects made the world feel software-y instead of human.

Decision:

- Keep deliveries, hidden obligations, and wake triggers as internal runtime mechanism.
- Derive a separate actor-facing layer built around:
  - observations
  - commitments
  - memory
  - visible work
  - mission context
- Actor prompts, tools, and UI should use that actor-facing layer instead of exposing scheduler ontology.

Why:

- Real coworkers think in what changed, what they owe, what they remember, and what matters now.
- The simulator still needs hidden machinery for stability, but that machinery should not become the world model.

Context:

- A stale response-delay trigger waking an actor because some other unrelated unread message exists breaks the meaning of delayed attention.

Decision:

- Each `response_delay` trigger now wakes only if its own `delivery_id` is still unread and already delivered.

Why:

- This preserves the causal link between a message and the delayed attention it should create.

### Decision: Reviewer speed control changes wall pacing, not simulation granularity

Context:

- You wanted `1x`, `10x`, and `100x` controls in the UI.
- Coarsening the simulation tick itself would make meetings, deadlines, and delayed replies feel fake.

Decision:

- Keep the simulation tick at one minute.
- Implement speed as a run-level wall-clock multiplier that shortens effective `tick_wall_seconds`.
- Temporal and the local fallback loop both consume that effective wall pacing.

Why:

- The world still advances through fine-grained simulated minutes.
- Reviewers can speed runs up without collapsing causal timing.

### Decision: Live tool exposure is filtered by actor permission

Context:

- Showing every tool to every actor wastes tokens and weakens the in-world illusion.

Decision:

- Add permission-aware tool filtering at the registry layer.
- Use the filtered set for Anthropic tool definitions, CLI prompt manifests, and runtime dispatch checks.

Why:

- Actors only see affordances they can actually use.
- The live path stays leaner and less confusing.

### Decision: Actor-created document visibility must be constrained to actor-valid scopes

Context:

- Raw visibility payloads allowed actors to create admin-only or self-hidden documents.

Decision:

- Validate actor-created document visibility on write.
- Reject admin visibility.
- Normalize private visibility to the author.
- Ensure the author can still see the result.

Why:

- The reviewer/admin boundary should not be forgeable from actor tools.
- Actor-created artifacts should stay legible to their creator.

### Decision: Live smoke should assert participation, not one specific NPC identity

Context:

- The PM's improved live behavior naturally engaged the backend lead before the engineering manager.
- The smoke harness had hard-coded `actor_eng_manager`, so it failed despite a healthy PM + live coworker loop.

Decision:

- Make the live smoke require:
  - PM participation
  - at least one live Claude coworker participation
  - actor-generated live events from the configured live actor set
- Update the default mixed live smoke override set to PM + backend lead for the current scenario path.

Why:

- The harness should prove the live path, not a single authored interaction path.

### Decision: planning is now split into one master simulation roadmap plus one dedicated reviewer UI roadmap

Context:

- The repo had several narrower plans, but the current work now spans runtime semantics, prompting, objectives, obligations, meetings, evaluation, and reviewer-surface design.
- UI/logging/admin-surface concerns were important enough to deserve their own intentional plan instead of remaining implied side notes.

Decision:

- Use [Master Simulation Plan](/Users/fleet/burhan-pm-simulation/docs/master-simulation-plan.md) as the top-level design and implementation roadmap.
- Use [Reviewer UI And Observability Plan](/Users/fleet/burhan-pm-simulation/docs/reviewer-ui-and-observability-plan.md) as the top-level UI/logging/admin roadmap.

Why:

- This gives future implementation work one clear semantic source of truth and one clear reviewer-surface source of truth.
- It avoids scattering major design changes across handoff notes and one-off feedback responses.

### Decision: Run state must stay truthful about orchestration attachment

Context:

- A run marked `running` with no attached Temporal workflow is a broken lie.
- Start and resume can fail because Temporal is unavailable even when the DB write succeeds.

Decision:

- Add explicit run orchestration state: `unattached`, `attaching`, `attached`, `error`.
- Temporal-enabled start/resume now use an attach handshake:
  - mark attaching
  - start or reuse the workflow
  - only then promote the run into `running`

Why:

- This keeps operator-visible state aligned with the actual background runtime.
- It prevents paused or newly created runs from looking active when there is no orchestrator attached.

### Decision: explicit controller overrides must be validated at run creation time

Context:

- Invalid override values were previously accepted into the seeded run and only failed later when actor execution could not resolve a controller.

Decision:

- Validate explicit controller overrides during run creation.
- Reject:
  - unknown actor ids
  - unsupported controller type values

Why:

- Bad API input should fail at the boundary where it enters the system.
- Runs should not be creatable in a state that is already known to be non-executable.

### Decision: deferring an obligation must cancel the earlier wake path

Context:

- A deferred reply obligation that still leaves its original `response_delay` trigger pending is not actually deferred.

Decision:

- When a reply obligation is explicitly deferred, cancel all pending triggers tied to that obligation before scheduling the new due trigger.

Why:

- Deferral must change runtime behavior, not just update display state.
- Social timing mechanics only matter if the scheduler obeys them exactly.

### Decision: reducers must fail loudly on unknown event types

Context:

- Silently ignoring unknown event types makes state corruption hard to detect and turns typos into invisible no-ops.

Decision:

- Keep an explicit set of reducer no-op event types.
- Raise an error for any event type that is neither handled nor explicitly allowed as a no-op.

Why:

- Event/state mismatches should fail early.
- This keeps the event-sourced core intentional instead of permissive.

### Decision: actor-facing tools must speak workplace language, not storage language

Context:

- The runtime still uses canonical ids internally for determinism and state integrity.
- But real coworkers do not think or speak in `thread_id`, `recipient_actor_id`, or `document_id`.
- The actor-facing surface had already gained human-reference resolution, but the visible tool schemas still leaked storage-oriented parameter names.

Decision:

- Make the actor-visible tool surface human-first.
- Use workplace-language parameter names such as:
  - `coworker`
  - `conversation`
  - `task`
  - `document`
  - `meeting`
  - `obligation`
  - `items`
  - `message`
- Keep canonical ids strictly beneath the toolkit/domain boundary.

Why:

- The actor-facing affordance should feel like using work tools, not driving an internal API.
- Reference resolution remains a real layer, but the visible interface no longer asks the actor to think in storage keys.

## 2026-03-22

### Decision: Use Claude Agent SDK instead of raw Anthropic API

Context:

- The raw Anthropic API tool-use path required manual tool dispatch, response parsing, and session management.
- The Claude Agent SDK provides a higher-level interface with built-in session persistence.

Decision:

- Use `claude_agent_sdk` (`ClaudeSDKClient`, `ClaudeAgentOptions`) for all live actor controllers.
- Use `get_session_info(session_id, directory=cwd)` to verify session existence.
- Sessions are stored by the SDK at `~/.claude/projects/<sanitized-cwd>/<session_id>.jsonl`.

Why:

- Simpler controller code with native session persistence.
- SDK handles the tool-use protocol internally.
- Session files persist actor conversational memory across turns.

### Decision: Remove MinIO from the stack

Context:

- MinIO was used only for large Claude prompt/response blob offload.
- All other data already lives in PostgreSQL.
- The blob storage added operational complexity without proportional value.

Decision:

- Remove MinIO from docker-compose.yml and the runtime.
- Store all data in PostgreSQL.

Why:

- Simpler stack with fewer moving parts.
- PostgreSQL handles the current data volumes without issue.

### Decision: Use Claude Sonnet 4.6 with streaming for post-run analysis

Context:

- Post-run analysis (summary + judgment) processes the full event log, which can be large.
- Long Anthropic API calls without streaming hit timeout limits.

Decision:

- Use `claude-sonnet-4-6` for analysis (1M token context window).
- Use streaming via `client.messages.stream()` with `text_stream`.
- Generate analysis in a background `threading.Thread(daemon=True)`.

Why:

- Streaming avoids the 10-minute timeout on long requests.
- Background thread prevents analysis from blocking the run completion path.
- Sonnet 4.6 balances cost and quality for analysis tasks.

### Decision: Use Text columns instead of String(255) for variable-length fields

Context:

- PostgreSQL enforces `varchar(255)` strictly. SQLite does not.
- World object titles from scenarios can exceed 255 characters.

Decision:

- Use `Text` instead of `String(255)` for fields like `WorldObjectRecord.title` that may contain variable-length content.

Why:

- Prevents silent truncation or hard errors on PostgreSQL.
- No meaningful performance difference for these fields.
