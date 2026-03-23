# Development Guide

This file provides guidance for working on the codebase.

## Build Order (Historical)

The system was built in vertical slices:

1. **First chat loop** — one scripted PM sends a chat, recipient wakes on next tick, replies
2. **World widening** — tasks, docs, meetings, reminders, richer triggers
3. **Real agents** — Claude Agent SDK controllers, native tool use, session persistence
4. **Reviewer completeness** — UI, evaluation, post-run analysis, 4 scenarios

## High-Conflict Files

These files should have one active editor at a time:

- `app/domain/engine.py` and its submodules
- `app/services/tick_loop.py`
- `app/services/simulation_service.py`
- `app/agents/claude_controller.py`
- `app/agents/toolkit.py`
- `app/api/routes/ui.py`

## Core Invariants

These must not be violated:

- Event log is canonical truth
- Controllers never mutate world state directly
- Deliveries are explicit records
- Traces are first-class
- Simulated time is owned by the simulator
- PM and NPCs use the same controller contract
- Actor memory is simulator-owned, not session-owned

## Testing

Tests live in `tests/` with subdirectories for unit, integration, e2e, and replay.

Run tests with SQLite (fast, no Docker needed):

```bash
PM_SIM_DATABASE_URL=sqlite+pysqlite:///./test.db pytest tests -q
```

The test suite uses an autouse fixture that:

- Disables Temporal
- Clears `ANTHROPIC_API_KEY`
- Defaults to offline paths

## Scenario Authoring

Each scenario is a directory under `app/scenarios/` with 5 YAML files. See existing scenarios for reference:

- `fire_drill` — simplest (1 day, 4 actors, 14 triggers)
- `smoke_test` — medium (3 days, 4 actors, 19 triggers)
- `onboarding_week` — full week (5 days, 4 actors, 24 triggers)
- `launch_crunch` — richest (5 days, 6 actors, full character prompts)
