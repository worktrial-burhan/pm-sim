# Build Journal

This is an append-only implementation and handoff log.

## 2026-03-20

### Initial planning baseline

- Created repo root: `burhan-pm-simulation/`
- Created initial directory scaffold for app, docs, docker, scripts, and tests
- Wrote lean v1 implementation blueprint
- Wrote initial execution plan
- Added lightweight continuity-doc rule so future implementation work updates repo context as code lands

### Planning simplification pass

- Removed the over-decomposed 53-task style execution plan
- Replaced it with a simpler execution strategy centered on the first vertical slice
- Updated the implementation plan to show concrete reference flows:
  - actor wake flow
  - `get_my_inbox`
  - `send_chat`
  - run creation
- Replaced milestone-heavy planning with four implementation slices

### Slice 1 implementation

- Bootstrapped the Python package, Dockerfile, and Docker Compose stack
- Added the core SQLAlchemy tables for runs, actors, actor state, world objects, events, deliveries, triggers, traces, and evaluations
- Added the event store, state store, delivery service, trace store, and run service
- Implemented the lean domain engine for `send_chat` and `reply_thread`
- Implemented reducers for thread creation and chat message state updates
- Implemented the worker tick loop with trigger firing, actor wake selection, controller invocation, and cooldown windows
- Added scripted PM and engineering-manager controllers
- Added the `launch_crunch` scenario seed files and compiler
- Added REST routes and thin UI pages for runs, events, actors, and admin inspection
- Added integration coverage for the scripted chat loop

### Slice 1 hardening

- Ran the integration test suite successfully
- Found and fixed a missing runtime dependency: `python-multipart`
- Added an API and UI smoke test using `TestClient`
- Replaced deprecated FastAPI startup hooks with a lifespan handler
- Verified that `docker compose config` passes for the current stack

### Robustness pass

- Fixed run-scoping bugs by allowing scenario seed IDs to repeat across runs without database collisions
- Fixed inbox semantics so reading inbox items does not implicitly mark them read
- Added explicit inbox acknowledgment support for controllers
- Fixed thread authorization so nonparticipants cannot inject messages into private threads
- Tightened run lifecycle transitions and returned 404/409 errors for ordinary client mistakes
- Replaced `max(seq) + 1` event sequencing with per-run sequence reservation and a uniqueness constraint
- Added regression coverage for duplicate runs, thread injection, inbox acknowledgment, and lifecycle/API errors

### Live-controller and orchestration groundwork

- Added a real Claude-backed controller path using the local `claude` CLI
- Kept actor memory system-owned and reconstructible from simulator state
- Added MinIO blob-store integration for large Claude prompt/response artifacts
- Extracted reusable simulation execution functions for run ticks and actor turns
- Added a real Temporal supervisor workflow and activities that can own the background loop when enabled

### Live-path hardening

- Scoped inbox acknowledgment to `(run_id, actor_id)` so one actor cannot mark another actor's deliveries read
- Made Claude prompt/response artifact storage best-effort so MinIO failures do not invalidate a valid turn
- Hardened Claude response parsing to recover JSON from wrapped responses
- Aligned Temporal actor-turn timeouts with the configured bounded Claude loop budget
- Replaced the global Temporal supervisor with per-run workflows that honor each run's own `tick_wall_seconds`
- Wired run start and resume routes to ensure the corresponding Temporal run workflow exists when Temporal is enabled
- Added regression coverage for cross-actor delivery acknowledgment, Claude blob-storage resilience, and Temporal timeout/run-workflow helpers

### Surface expansion and persona pass

- Expanded the domain engine and toolkit beyond chat into:
  - email
  - meetings
  - documents
  - task mutations
  - project priority updates
  - self-wake scheduling
- Expanded the `launch_crunch` scenario so PM and NPC behavior now includes:
  - proactive revenue pressure
  - blocker logging
  - risk-register updates
  - meeting scheduling
  - backend meeting-note capture
- Strengthened actor context so Claude-backed controllers see goals, beliefs, relationships, commitments, workload, and a persona brief
- Added an explicit no-action vs commands-staged decision signal to controller responses and traces
- Added a simple rubric-backed evaluation service and wired the evaluation API

### Live stack validation

- Added `.env` and `.env.example` for the live Docker/Anthropic path
- Updated Docker Compose so Temporal and MinIO are actually enabled in the live stack
- Added an opt-in live smoke test and the reusable `scripts/live_stack_smoke.py` harness
- Fixed real live-stack bugs uncovered during the first end-to-end run:
  - wrong Temporal auto-setup DB driver
  - invalid Temporal dynamic-config path
  - missing Temporal activity executor for synchronous activities
  - workflow sandbox import restriction
  - wrong `Client.start_workflow()` argument shape
  - brittle smoke assertion against the UI's scenario label
- Ran the live smoke harness successfully against:
  - FastAPI UI/API
  - PostgreSQL
  - Temporal
  - MinIO
  - live Claude-backed actors via Anthropic
- Verified successful smoke summary with:
  - one scripted coverage run
  - one live Claude-backed run
  - nonzero MinIO Claude artifacts
  - nonzero live event/traces counts
  - evaluation results for both runs

### Current handoff

- Local automated coverage is green: `12 passed, 1 skipped`
- The live Docker/Claude smoke harness has passed end to end
- The next work should shift away from wiring and toward deeper realism: information asymmetry, richer NPC initiative, and better evaluation semantics

## 2026-03-21

### Actor-facing tool-surface realism pass

- Renamed the Claude-visible tool surface from API-ish names to workplace-action names such as:
  - `check_inbox`
  - `read_conversation`
  - `message_coworker`
  - `review_commitments`
  - `wrap_up_assignment`
- Kept the internal toolkit methods and domain commands stable while changing only the actor-facing tool registry boundary
- Added `tool_name_for_method(...)` so toolkit traces and staged action metadata stay aligned with the same registry instead of drifting
- Reframed prompt language from "available work tools" toward "work surfaces and actions available to you right now"
- Re-ran the full local suite successfully after the rename: `41 passed, 1 skipped`

### Claude loop semantics pass

- Fixed the controller fallback so a final allowed Claude step that already staged real work is no longer mislabeled as `budget_exhausted`
- Applied the same fix to both:
  - native Anthropic tool-use path
  - JSON/CLI fallback path
- Added regression coverage for “last allowed step stages a command” in both controller paths
- Increased the default Claude step budget from `6` to `50` in config and env templates
- Re-ran the full local suite successfully after the change: `43 passed, 1 skipped`

### Abstraction cleanup pass

- Replaced long `if/elif` ladders in reducers with an explicit reducer registry
- Replaced long event-summary and observation-rendering ladders with explicit registries
- Extracted shared run-activity, run-events, and actor-detail assembly into `app/api/view_models.py`
- Extracted the three-phase Temporal attach flow into `app/services/orchestration_service.py`
- Removed duplicated tool-manifest functions and kept one canonical tool-definition surface
- Tightened state-store status checks to use domain enums instead of stray string literals
- Verified the cleanup with a full green suite: `41 passed, 1 skipped`

### Domain-engine decomposition

- Split the old `app/domain/engine.py` monolith into four concrete modules:
  - `engine_common.py`
  - `engine_attention.py`
  - `engine_commands.py`
  - `engine_triggers.py`
- Kept `app/domain/engine.py` as the stable public facade exporting only:
  - `CommandRejected`
  - `AppliedCommandResult`
  - `apply_command`
  - `apply_trigger`
- Moved command logic, trigger logic, and async-work mechanics behind clearer module boundaries without changing behavior
- Re-ran the full suite successfully after the split: `41 passed, 1 skipped`

### Realism and runtime hardening plan

- Added [Realism And Runtime Hardening Plan](/Users/fleet/burhan-pm-simulation/docs/realism-and-runtime-hardening-plan.md)
- Converted the latest external review feedback into a concrete remediation roadmap
- The new plan covers:
  - paused-run and Temporal idempotency fixes
  - prepare/decide/apply turn execution
  - in-world prompting and character prompts
  - tool registry refactor
  - working-hours and response-delay semantics
  - obligations as the async work model
  - causal cognitive state
  - ontology tightening
  - evaluation layering
  - performance and test hardening

### Phase 0 runtime hardening, first landing

- Added explicit run orchestration state so start and resume no longer pretend a run is active before Temporal attachment succeeds
- Split actor turns into durable records with `prepared -> deciding -> decided -> applied|failed|cancelled` lifecycle
- Switched Temporal actor-turn execution from `(run_id, actor_id)` to stable `turn_id`
- Refactored controller execution into prepare, decide, and apply phases so no DB session stays open across model calls
- Moved toolkit tracing to buffered in-memory entries that are committed at apply time
- Changed inbox acknowledgement to stage an action command instead of mutating storage inline during controller execution
- Added a real idempotent apply boundary for retried actor turns
- Tightened the tick path so non-running or unattached runs do not advance time or consume triggers
- Added self-wake guardrails:
  - cap outstanding self-wakes per actor
  - dedupe repeated reasons
- Added durable tick replay for Temporal by accepting stable tick tokens and reusing the stored tick result on retry
- Added regression coverage for:
  - paused runs not advancing
  - actor-turn replay idempotency
  - tick replay idempotency
  - truthful Temporal attach failure handling

### Prompt realism cleanup

- Removed explicit `simulation`, `controller`, and `NPC` framing from the Claude system prompt
- Kept the structured JSON tool loop, but reframed it as using work tools rather than acting as an operator
- Removed staged-command-count feedback from the actor-facing prompt payload
- Added a unit test that guards against those out-of-world prompt terms returning

### Master planning reset

- Added [Master Simulation Plan](/Users/fleet/burhan-pm-simulation/docs/master-simulation-plan.md) as the new top-level roadmap
- Added [Reviewer UI And Observability Plan](/Users/fleet/burhan-pm-simulation/docs/reviewer-ui-and-observability-plan.md) as the dedicated UI/logging/admin-surface plan
- Folded the latest design feedback into explicit plans for:
  - mission/objective semantics
  - PM-driven finish/stop semantics
  - obligation-driven attention instead of visible turns
  - action-time progression within one attention episode
  - native Anthropic tool use as the target live path
  - meeting-session modeling
  - cost strategy
  - realism measurement
  - graceful actor failure semantics
  - reviewer timeline and observability polish

### Current handoff

- Local automated coverage is green: `17 passed, 1 skipped`
- The repo is now partway through Phase 0, with the main remaining Temporal-runtime gaps being:
  - deeper live Temporal retry coverage
  - richer workflow-owned waits via obligations instead of only SQL wake selection
- The live Docker smoke was rerun successfully after this refactor and still passed against:
  - FastAPI/UI
  - PostgreSQL
  - Temporal
  - MinIO
  - live Claude-backed actors
- The active design target is now the pair of docs:
  - [Master Simulation Plan](/Users/fleet/burhan-pm-simulation/docs/master-simulation-plan.md)
  - [Reviewer UI And Observability Plan](/Users/fleet/burhan-pm-simulation/docs/reviewer-ui-and-observability-plan.md)

### Attention, timing, and in-world actor pass

- Added a first-class mission/assignment object to scenario metadata and seeded run config
- Added PM-facing assignment visibility and a PM-only `finish_assignment` action path
- Added `get_thread_messages`, `list_colleagues`, and `create_document` to the tool surface
- Unified tool definitions behind a single registry so tool implementation, prompt exposure, and native Anthropic tool definitions all come from one source
- Reworked the primary Claude path to use Anthropic native tool use instead of the old raw JSON-only control loop
- Kept the CLI JSON path as a bounded fallback for environments without native tool use
- Rewrote actor prompting around in-world identity:
  - `YOU ARE X`
  - local time
  - working-hours state
  - inbox snapshot
  - visible work snapshot
  - assignment pressure where relevant
- Added authored character prompts and response-behavior profiles to the `launch_crunch` actors
- Added working-hours normalization and local-time handling
- Shifted wake semantics from `unread delivery means instant wake` to delayed-response triggers plus working-hours gating
- Added action-time progression inside one actor attention window:
  - staged actions now receive increasing `issued_at_sim`
  - apply-phase advances run time as those actions land

### Reference resolution and custom signal-event hardening

- Added a real reference-resolution layer so actors can act using:
  - coworker names
  - task titles
  - document titles
  - thread subjects/titles
  - meeting titles
  instead of needing opaque internal ids everywhere
- Wired the toolkit to resolve human references into canonical ids before staging commands
- Updated actor/tool prompting to tell the model it can refer to visible work by name/title/subject when unambiguous
- Fixed state-patch signal events so scenario-defined event types like `LaunchRiskEscalated` can be emitted without crashing the reducer layer
- Added regression coverage for:
  - human reference resolution in task and email-thread actions
  - deadline-triggered `LaunchRiskEscalated` state patch flow
  - action-attempt traces can now carry their own effective time
- Moved `launch_crunch` start time to 9:00 AM Pacific so the realism layer and seeded triggers agree
- Verified the full local suite after this pass:
  - `21 passed, 1 skipped`
  - `python -m compileall` passes
- Added a small reviewer-surface polish pass so the new semantics are inspectable:
  - run timeline now shows actor plus event type
  - actor pages now show local time, working-hours state, and visible work context
- While probing the live Docker path after this rewrite, the mixed Claude smoke exposed a real behavior issue:
  - the PM was spending the whole attention window browsing instead of acting
- Tightened the in-world prompt and raised the default native-tool-use step budget from `4` to `6`
- Reverified the full local suite after that tuning:
  - `21 passed, 1 skipped`
  - `python -m compileall` passes
- A fresh clean live-smoke rerun should still be done from a clean stack after this last prompt-budget tweak

### Runtime correctness, reviewer-speed control, and live-path realism follow-through

- Fixed apply-phase terminality so `finish_assignment` now ends the run immediately and skips any later staged commands from that same attention window
- Fixed `response_delay` semantics so each wake trigger only wakes on the specific unread delivery that created it
- Validated actor-created document visibility:
  - admin scope is rejected
  - invalid actor/team/role visibility is rejected
  - the author must remain able to see what they created

### Closure and alignment sharpness pass

- Added mission-visible completion checks so the PM can see what “buttoned up” means from inside the world
- Added a PM-only `review_completion_readiness` tool that surfaces:
  - failed visible completion checks
  - unread inbox items
  - the PM's own open obligations
  - pending replies from others
  - active meetings
  - at-risk projects
  - high-priority work still in flight
- Tightened `finish_assignment` so it now records a finish-readiness snapshot into the assignment state and emitted event payload
- Normalized `remaining_risks` in assignment completion payloads so a string no longer becomes a list of characters
- Aligned the `launch_crunch` mission with explicit visible completion checks:
  - blocker or clarified path is durably recorded
  - revenue gets a grounded written PM update
  - the cross-functional decision path is visibly in motion
- Relaxed the stakeholder-update rubric mismatch so the scenario now accepts a grounded written update by chat or email instead of requiring one hidden exact medium
- Surfaced finish readiness, blockers, and warnings in the run and actor UI pages
- Added focused regression coverage for:
  - PM completion-readiness visibility
  - normalized assignment completion payloads
- Reverified the codebase after this pass:
  - `31 passed` in `test_runtime_invariants.py`
  - `41 passed, 1 skipped` in the full test suite

### Ontology / mechanism separation pass

- Added a dedicated perception layer in `app/services/perception_service.py`
- Kept deliveries, hidden obligation objects, and triggers as runtime mechanism
- Derived actor-facing `observations` from:
  - unread inbox artifacts
  - relevant visible work changes since the actor last checked in
- Derived actor-facing `commitments` from the hidden obligation objects
- Refactored actor context so Claude-backed actors now see:
  - observations
  - current commitments
  - visible work
  - memory
  - mission context
  instead of raw deliveries plus obligations
- Added `review_recent_observations` and renamed actor-facing commitment tools:
  - `list_my_commitments`
  - `complete_commitment`
  - `defer_commitment`
- Updated the actor detail API/UI to show:
  - recent observations
  - current commitments
  - inbox surfaces
  - visible work
  as distinct reviewer concepts
- Updated reviewer event phrasing so internal obligation events render as follow-ups rather than leaking mechanism language
- Reverified after the refactor:
  - `6 passed` in `test_claude_controller.py`
  - `31 passed` in `test_runtime_invariants.py`
  - `41 passed, 1 skipped` in the full test suite
- Resolved coworker names more consistently:
  - inbox deliveries now include sender names
  - relationship summaries now render names instead of raw actor IDs
  - newly created thread titles now use recipient names instead of raw actor IDs
- Filtered the live tool surface per actor permissions so PM-only or manager-only actions are not shown to everyone
- Added run-level reviewer speed control at `1x`, `10x`, and `100x`
  - the scale changes effective wall-clock tick pacing
  - it does not coarsen the one-minute simulation tick size
  - the local fallback loop now honors per-run effective tick pacing in `serve_forever()` while preserving manual `run_once()` semantics for tests
- Strengthened the live Claude path again after inspecting real stalled traces:
  - richer visible-work summaries in the prompt
  - stronger anti-browsing guidance in the system prompt
  - a controller-side nudge after repeated pure-read steps with no staged work
- Found that the live smoke harness itself had become too rigid:
  - it assumed `actor_eng_manager` had to be the live NPC participant
  - the new PM behavior naturally engaged `actor_backend_lead` first
- Updated the smoke harness so it now validates the right invariant:
  - the PM plus at least one live Claude coworker must actually participate
  - actor-generated live events are counted from the configured live Claude set, not a hard-coded actor pair
- Updated the default mixed live smoke override set to exercise PM + backend lead as the live Claude pair for the `launch_crunch` path
- Reverified everything after this pass:
  - `27 passed, 1 skipped`
  - `python -m compileall` passes
  - `scripts/live_stack_smoke.py` passes again against FastAPI/UI + Postgres + Temporal + MinIO + Claude actors

### Obligation, meeting-transcript, and reasoning-log pass

- Flipped the default `launch_crunch` actor seeds to `claude`
- Added explicit controller profiles:
  - `live_realism`
  - `scripted_demo`
- Made the UI create-run flow default to `live_realism`
- Added command-time working-hours enforcement in the toolkit and apply-phase guardrails in the runtime
- Wired workload state into actual response-delay timing instead of only prompt-visible profile text
- Added obligation support on the canonical world-object/event backbone:
  - `obligation` world objects
  - obligation completion and deferral commands
  - reply, meeting, and reminder obligation creation
  - obligation-due triggers
- Added recurring actor routines sourced from scenario profile data
- Added meeting speaking and shared transcript state:
  - `MeetingSpoken`
  - transcript storage on meeting objects
  - meeting transcript read tool
- Added reviewer-visible activity logs that merge:
  - event timeline entries
  - introspection/reasoning traces
- Added `GET /api/runs/{run_id}/activity`
- Added [Current Architecture](/Users/fleet/burhan-pm-simulation/docs/current-architecture.md) so the repo has one doc that describes the code that actually exists now
- Removed the stale `app/domain/meetings.py` placeholder file
- Reverified after this pass:
  - `32 passed, 1 skipped`
  - `python -m compileall` passes

### Final verification closeout

- Re-ran the full local suite successfully:
  - `32 passed, 1 skipped`
- Re-ran the live Docker smoke successfully against the current realism-first default stack:
  - FastAPI/UI
  - PostgreSQL
  - Temporal
  - MinIO
  - Claude-backed actors
- Confirmed reviewer-visible reasoning in both places:
  - run-level activity log
  - actor detail pages
- Confirmed the current live path now exercises:
  - mission-driven PM behavior
  - reply and reminder obligations
  - delayed-response timing
  - meeting speaking and transcript updates
  - live Temporal orchestration

### Review-driven hardening pass

- Fixed deferred reply-obligation semantics so deferring now suppresses the earlier `response_delay` wake instead of leaving stale attention triggers behind
- Validated explicit controller overrides at run creation:
  - unknown actor ids are rejected
  - unsupported controller types are rejected
- Fixed actor-turn record updates so optional fields can be explicitly cleared back to `None`
- Added native-tool-path memory summary derivation so Anthropic-backed turns no longer leave actor memory unchanged by default
- Hardened thread-participant lookup against `None` state payloads
- Made `mark_inbox_items_read()` return the real staging result instead of pretending success while discarding rejection state
- Unified `no_action` signaling onto `deliberate_no_action`
- Removed a dead `working_hours` field from the actor-turn context
- Made reducers fail loudly on unknown event types instead of silently ignoring them
- Added blob retrieval support so MinIO prompt/response artifacts are no longer write-only from the application layer
- Added minimal runtime logging on:
  - run creation and lifecycle transitions
  - orchestration attach failures
  - Temporal connection retries
  - actor-turn failure paths
- Added regression coverage for:
  - invalid controller overrides rejected at run creation
  - deferred reply obligations suppressing the earlier wake
  - actor-turn optional field clearing
  - native Claude memory-summary updates
- Reverified after this pass:
  - `36 passed, 1 skipped`
  - `python -m compileall` passes
  - `scripts/live_stack_smoke.py` passes against FastAPI/UI + Postgres + Temporal + MinIO + Claude actors

### Human-first tool-schema pass

- Removed actor-facing `_id`-style tool parameters from the Claude-visible tool surface
- Renamed tool inputs to workplace-language terms such as:
  - `coworker`
  - `conversation`
  - `task`
  - `document`
  - `meeting`
  - `obligation`
  - `items`
  - `message`
- Kept canonical ids strictly internal:
  - toolkit resolves human references into canonical ids
  - domain commands still operate on canonical ids
- Updated actor prompting so coworkers are told to use visible names, titles, and subjects rather than opaque handles
- Hid obligation ids from the actor prompt and kept obligation references human-readable
- Reverified after this pass:
  - `38 passed, 1 skipped`
  - `python -m compileall` passes
  - `scripts/live_stack_smoke.py` passes against FastAPI/UI + Postgres + Temporal + MinIO + Claude actors

### UI live-refresh interaction fix

- Replaced the raw HTML meta-refresh on reviewer pages with a pause-aware JavaScript refresh loop
- Live pages still auto-refresh, but refresh now pauses while a user is interacting with form controls or has just changed one
- Fixed the run-speed selector snapping back to the persisted value before the form submit completed
- Added a UI regression asserting:
  - the run page no longer uses `<meta http-equiv="refresh">`
  - the page shell now uses script-driven reload instead
- Reverified after this pass:
  - `38 passed, 1 skipped`

## 2026-03-22

### Production hardening and Claude Agent SDK migration

- Migrated the Claude controller from raw Anthropic API to Claude Agent SDK (`claude_agent_sdk`)
  - `ClaudeSDKClient`, `ClaudeAgentOptions`, `get_session_info` for session management
  - Sessions persist at `~/.claude/projects/<sanitized-cwd>/<session_id>.jsonl`
  - Fixed critical bug: `_sdk_session_file_exists()` was looking in the wrong path, causing every turn to be stateless. Replaced with SDK's `get_session_info`.
- Fixed PostgreSQL `varchar(255)` overflow: changed `WorldObjectRecord.title` from `String(255)` to `Text`
- Switched analysis LLM calls to streaming (`client.messages.stream()`) to handle long requests
- Changed analysis model to Claude Sonnet 4.6 (`claude-sonnet-4-6`)
- Added post-run analysis: story summary + qualitative judgment in background thread
- Added worker logging and improved error reporting with `exc_info=exc`
- Fixed Docker session persistence: SDK sessions use named volume at `/home/simuser/.claude`
- Removed MinIO from the stack

### UI improvements

- `<details>` state persistence via sessionStorage
- Actor name + role in activity stream
- "X ago" time format
- Reverse chronological activity stream

### New scenarios

- `onboarding_week` (5-day, Noor/Sam/Alex/Jordan)
- `smoke_test` (3-day, Ren/Kai/Dana/Luca)
- `fire_drill` (1-day, Mika/Finn/Zara/Viv)

### Documentation rewrite

- Rewrote all 9 docs to reflect current state
- Removed MinIO references, updated to Claude Agent SDK, consolidated historical plans
