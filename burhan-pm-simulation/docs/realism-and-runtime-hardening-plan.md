# Runtime Design

This document describes the runtime correctness mechanics that have been implemented. It was originally a remediation plan; most items are now complete.

## Implemented

### Transactionally Safe Ticking

`process_run_tick` is atomic: locks the run row, checks tickable state, advances clock, fires triggers, materializes turn records, returns ordered turn IDs. Paused/stopped/unattached runs are no-ops.

### Orchestration Attachment

Runs have explicit orchestration state: `unattached`, `attaching`, `attached`, `error`. Start/resume use an attach handshake — runs are not marked active before Temporal attachment succeeds.

### Durable Actor Turn Records

`ActorTurnRecord` is the idempotency boundary. Statuses: `prepared → deciding → decided → applied | failed | cancelled`. Temporal executes turns by stable `turn_id`. Retrying a completed turn does not duplicate side effects.

### Three-Phase Actor Turns

1. **Prepare** — short DB session loads context, closes
2. **Decide** — controller runs with no write transaction open; toolkit reads use short-lived sessions
3. **Apply** — short DB session persists traces, commands, memory updates, cooldown

### Self-Wake Safeguards

- Cap outstanding self-wake triggers per actor
- Deduplicate repeated reasons

### Working-Hours Enforcement

- Actors are not woken outside working hours (unless on-call)
- Commands that would land after workday ends are not applied

### Response Delays

`RESPONSE_DELAY` triggers are real. Delay is computed from actor response profile, workload, focus, urgency, and relationship. Actors don't wake instantly on every unread message.

### Recurring Routines

Scenario-authored routines (e.g., morning inbox sweep, afternoon check-in) create recurring triggers. Each fired routine reschedules itself.

### Obligations

Durable async work items stored as `WorldObject(kind="obligation")`. Created from message deliveries, meeting starts, and self-reminders. Actors experience them as commitments. Deferring an obligation cancels the earlier wake trigger.

### In-World Prompting

No actor-facing prompt contains `simulation`, `npc`, or `controller`. Prompts use authored character voice with identity, stressors, relationships, and communication examples.

### Tool Registry

Single `ToolSpec` registry drives dispatch, prompt exposure, and tool definitions. Workplace-language parameter names (`coworker`, `conversation`, `task`). Permission-filtered per actor role.

### Session Persistence

Claude Agent SDK sessions are persisted at `~/.claude/projects/<sanitized-cwd>/`. Session existence is verified via `get_session_info(session_id, directory=cwd)`. Missing sessions fall back to first-turn prompts.

### Post-Run Analysis

Background thread generates story summary and qualitative judgment using Claude Sonnet 4.6 with streaming via the Anthropic SDK. Results stored in `RunAnalysisRecord`.

## Still Incomplete

- Trust, workload, and focus are more visible than causal
- Deeper multi-round meeting dynamics
- Richer obligation creation from more work surfaces
- Cost-tier model selection by actor context
- Concurrent decision generation (currently sequential per run)
