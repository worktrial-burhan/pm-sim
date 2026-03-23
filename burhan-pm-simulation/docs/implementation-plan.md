# Implementation Plan

This document describes the system's design principles and semantic model. It was originally the v1 blueprint and has been updated to reflect the current implementation.

## Design Principles

- One canonical company world — chat, email, meetings, tasks, docs are surfaces over it
- PM and NPCs are the same primitive (`Actor`)
- Agents propose commands, the domain engine decides outcomes
- Partial observability per actor
- Simulated time decoupled from model latency
- Full interaction traceability
- Outcome-oriented evaluation

## Semantic Model

### Core Types

| Type | Purpose |
| --- | --- |
| `SimulationRun` | One execution of one scenario with configuration and clock state |
| `Actor` | A person-like participant including the PM and coworkers |
| `WorldObject` | Durable objects: projects, tasks, threads, meetings, documents, obligations |
| `DomainEvent` | An accepted authoritative mutation |
| `Delivery` | Actor-facing inbox/notification record with unread state |
| `Trigger` | Future event that fires at a simulated time |
| `Trace` | Non-authoritative observability log |

### World Objects

`WorldObject` is a single model with a `kind` field:

- `project`, `task`, `thread`, `meeting`, `document`, `obligation`

### Visibility

Each object or event has a visibility scope: actor IDs, role groups, team groups, company-wide, or admin-only. Deliveries are explicit — not all visible events create inbox items.

## Runtime Topology

Five Docker services:

- `api` — FastAPI app + reviewer UI
- `worker` — simulation tick loop, actor execution, post-run analysis
- `postgres` — canonical data store
- `temporal` — durable run orchestration
- `temporal-ui` — Temporal dashboard

## Tick Loop

Each tick:

1. Advance simulated clock
2. Fire due triggers
3. Determine wake candidates
4. Invoke controllers subject to budget
5. Validate and apply commands
6. Write deliveries and traces

Within a single run, state mutation is serialized. Actor turns run sequentially.

## Controller Contract

Input: actor profile, state, observations, commitments, visible work, assignment context.

Output: zero or more commands, optional memory update, optional introspection traces.

Both `claude` and `scripted` controllers implement the same contract.

## Scenario Authoring

Each scenario is a directory under `app/scenarios/` with five YAML files:

- `scenario.yaml` — metadata, mission, completion checks, stop conditions
- `actors.yaml` — actor profiles, character prompts, working hours, permissions
- `world.yaml` — initial projects, tasks, documents
- `triggers.yaml` — timed events, routine wakes, deadlines
- `rubric.yaml` — hidden truth, scoring checks

## Evaluation

Two evaluation layers:

1. **Rubric checks** — structural and temporal checks defined in `rubric.yaml` (event existence, timing, state conditions)
2. **Post-run analysis** — LLM-generated story summary and qualitative judgment using Claude Sonnet 4.6 with streaming

## Tool Surface

Actors interact through workplace-language tools:

### Awareness

`check_inbox`, `read_conversation`, `get_current_time`, `list_colleagues`, `list_visible_projects`, `list_visible_tasks`, `review_recent_observations`, `list_my_commitments`, `review_completion_readiness`

### Action

`message_coworker`, `reply_to_conversation`, `mark_inbox_read`, `update_task`, `create_document`, `update_document`, `schedule_meeting`, `speak_in_meeting`, `record_meeting_note`, `complete_commitment`, `defer_commitment`, `schedule_follow_up`, `wrap_up_assignment`

Tools use workplace language for parameters (`coworker`, `conversation`, `task`, `document`, `meeting`) — canonical IDs are internal only.
