# Simulation Design

This document describes the design principles and target model for the simulator. It was originally the master roadmap. Most of the described mechanics are now implemented.

## Product Thesis

The system is a digital workplace with:

- One canonical company world
- People with bounded awareness and ongoing pressures
- Time that moves independently of model latency
- Work that creates follow-ups, waiting, interruptions, and consequences
- A project manager given a real assignment who must decide when the job is done

## What Must Feel Real

- Why information is hidden and how it becomes discoverable
- Why some people reply quickly and others don't
- How meetings, docs, tasks, and messages interact
- How long actions take
- How work accumulates and competes for attention
- How a PM decides the assignment is complete

## Implemented Mechanics

### Mission-Driven Scenarios

Every scenario has a first-class PM assignment with:

- Visible brief, constraints, and completion guidance
- Hidden success/failure conditions for evaluation
- Visible completion checks the PM can review before finishing
- PM-only `wrap_up_assignment` tool for explicit closure

### Action-Time Progression

Actions consume simulated minutes. Within one attention window, an actor's actions get increasing timestamps. The world does not freeze while one actor thinks.

### Obligations as Async Work

Messages, meetings, and reminders create durable obligation objects. Actors experience these as commitments they can list, complete, or defer. Obligations drive wake behavior, not just unread deliveries.

### In-World Prompting

Actor prompts start with `YOU ARE [name]` and include character, relationships, stressors, and communication style. No simulation/controller/NPC framing. The Claude Agent SDK provides native tool use.

### Time Realism

- Timezone-aware local time per actor
- Working-hours enforcement
- Response delays shaped by actor profile, urgency, relationship, workload
- Recurring routines (morning check-ins, afternoon updates)
- Deadlines that mutate the world even if the PM does nothing

### Meeting Model

- Scheduling creates meeting objects with start/end triggers
- Participants can speak, creating shared transcript entries
- Meeting notes can be recorded
- Meetings create attendance obligations for participants

### Closure Semantics

The PM can review visible completion readiness and explicitly finish the assignment. The finish stores a readiness snapshot with blockers and warnings. Evaluation scores closure quality separately.

Run end modes: `completed_by_pm`, `timed_out`, `hard_failure`, `operator_stopped`.

## Areas for Future Depth

- Deeper multi-round meeting conversations
- Trust, workload, and focus becoming more causal over time
- Richer obligation creation from more work surfaces
- Broader evaluation beyond rubric checks
- Cost-tier model selection by actor role and context
