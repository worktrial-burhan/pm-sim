from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from app.services.assignment_service import public_assignment
from app.services.run_service import effective_tick_wall_seconds, get_time_scale_multiplier

EventSummarizer = Callable[[object, dict[str, str]], str]
_EVENT_SUMMARIZERS: dict[str, EventSummarizer] = {}


def _event_summary(*event_types: str) -> Callable[[EventSummarizer], EventSummarizer]:
    def decorator(fn: EventSummarizer) -> EventSummarizer:
        for event_type in event_types:
            _EVENT_SUMMARIZERS[event_type] = fn
        return fn

    return decorator


# ---------------------------------------------------------------------------
# Relative-time formatting
# ---------------------------------------------------------------------------

def format_time_ago(sim_time: datetime | str, current_sim_time: datetime | str) -> str:
    """Format sim_time as 'X ago' relative to current_sim_time."""
    if isinstance(sim_time, str):
        sim_time = datetime.fromisoformat(sim_time)
    if isinstance(current_sim_time, str):
        current_sim_time = datetime.fromisoformat(current_sim_time)
    if sim_time.tzinfo is not None:
        sim_time = sim_time.replace(tzinfo=None)
    if current_sim_time.tzinfo is not None:
        current_sim_time = current_sim_time.replace(tzinfo=None)
    delta = current_sim_time - sim_time
    total_seconds = max(int(delta.total_seconds()), 0)
    if total_seconds < 60:
        return "just now"
    total_minutes = total_seconds // 60
    if total_minutes < 60:
        return f"{total_minutes}m ago"
    hours = total_minutes // 60
    minutes = total_minutes % 60
    if hours < 24:
        if minutes:
            return f"{hours}h {minutes}m ago"
        return f"{hours}h ago"
    days = hours // 24
    remaining_hours = hours % 24
    if remaining_hours:
        return f"{days}d {remaining_hours}h ago"
    return f"{days}d ago"


def format_relative_time(sim_time: datetime | str, start_time: datetime | str) -> str:
    """Format sim_time as human-readable elapsed time from start_time."""
    if isinstance(sim_time, str):
        sim_time = datetime.fromisoformat(sim_time)
    if isinstance(start_time, str):
        start_time = datetime.fromisoformat(start_time)
    # Normalize both to naive (strip timezone) to avoid mixed-offset subtraction
    if sim_time.tzinfo is not None:
        sim_time = sim_time.replace(tzinfo=None)
    if start_time.tzinfo is not None:
        start_time = start_time.replace(tzinfo=None)
    delta = sim_time - start_time
    total_seconds = max(int(delta.total_seconds()), 0)
    total_minutes = total_seconds // 60
    if total_minutes < 60:
        return f"+{total_minutes}min"
    hours = total_minutes // 60
    minutes = total_minutes % 60
    if hours < 24:
        return f"+{hours}h {minutes}min" if minutes else f"+{hours}h"
    days = hours // 24
    remaining_hours = hours % 24
    if remaining_hours:
        return f"+{days}d {remaining_hours}h"
    return f"+{days}d"


# ---------------------------------------------------------------------------
# Run / actor serialisation (unchanged)
# ---------------------------------------------------------------------------

def _format_duration(delta: timedelta) -> str:
    """Format a timedelta as a compact human-readable string."""
    total_seconds = max(int(delta.total_seconds()), 0)
    if total_seconds < 60:
        return f"{total_seconds}s"
    total_minutes = total_seconds // 60
    if total_minutes < 60:
        return f"{total_minutes}m"
    hours = total_minutes // 60
    minutes = total_minutes % 60
    if hours < 24:
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    days = hours // 24
    remaining_hours = hours % 24
    if remaining_hours:
        return f"{days}d {remaining_hours}h"
    return f"{days}d"


def serialize_run(run) -> dict:
    config = getattr(run, "config_json", {}) or {}
    assignment = public_assignment(config.get("assignment", {}))

    # Compute start_sim_time from config (set by compiler)
    start_sim_time_str = config.get("start_sim_time", "")
    if not start_sim_time_str:
        start_sim_time_str = run.current_sim_time.isoformat()

    # Sim elapsed: current_sim_time - start_sim_time
    sim_elapsed = ""
    try:
        start_sim = datetime.fromisoformat(start_sim_time_str)
        current_sim = run.current_sim_time
        if start_sim.tzinfo is not None:
            start_sim = start_sim.replace(tzinfo=None)
        if current_sim.tzinfo is not None:
            current_sim = current_sim.replace(tzinfo=None)
        sim_elapsed = _format_duration(current_sim - start_sim)
    except Exception:
        pass

    # Wall elapsed: ended_at - started_at (completed) or now - started_at (running)
    wall_elapsed = ""
    if run.started_at:
        end = run.ended_at or datetime.now(timezone.utc)
        started = run.started_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        wall_elapsed = _format_duration(end - started)

    return {
        "id": run.id,
        "scenario_id": run.scenario_id,
        "status": run.status,
        "orchestration_status": getattr(run, "orchestration_status", None),
        "orchestration_error": getattr(run, "orchestration_error", None),
        "assignment": assignment,
        "assignment_state": assignment.get("state"),
        "current_sim_time": run.current_sim_time.isoformat(),
        "start_sim_time": start_sim_time_str,
        "tick_sim_seconds": run.tick_sim_seconds,
        "tick_wall_seconds": run.tick_wall_seconds,
        "effective_tick_wall_seconds": effective_tick_wall_seconds(run),
        "time_scale_multiplier": get_time_scale_multiplier(run),
        "model": config.get("model"),
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "ended_at": run.ended_at.isoformat() if run.ended_at else None,
        "sim_elapsed": sim_elapsed,
        "wall_elapsed": wall_elapsed,
    }


def serialize_actor(actor, actor_state) -> dict:
    return {
        "id": actor.id,
        "name": actor.name,
        "role": actor.role,
        "team": actor.team,
        "timezone": actor.timezone,
        "controller_type": actor.controller_type,
        "next_eligible_wake_time": (
            actor_state.next_eligible_wake_time.isoformat()
            if actor_state and actor_state.next_eligible_wake_time
            else None
        ),
    }


def serialize_object(obj) -> dict:
    return {
        "id": obj.id,
        "kind": obj.kind,
        "title": obj.title,
        "state": obj.state_json,
        "visibility": obj.visibility_json,
    }


# ---------------------------------------------------------------------------
# Human-readable activity summarisation (clean stream)
# ---------------------------------------------------------------------------

# Events to skip entirely in the clean stream
_NOISE_EVENT_TYPES = {
    "ClockAdvanced",
    "RunCreated",
    "RunStarted",
    "RunResumed",
    "RunPaused",
    "RunStopped",
    "ActorCreated",
    "WorldObjectCreated",
    "StatePatchSkipped",
    "ScenarioTriggerFired",
    "InboxItemsRead",
}

# Trace types to show in clean stream
_VISIBLE_TRACE_TYPES = {"introspection_write", "command_rejected"}


def is_visible_activity(entry: dict) -> bool:
    """Return True if this entry should appear in the clean activity stream."""
    if entry["entry_type"] == "event":
        return entry["kind"] not in _NOISE_EVENT_TYPES
    if entry["entry_type"] == "trace":
        if entry["kind"] not in _VISIBLE_TRACE_TYPES:
            return False
        data = entry.get("data") or {}
        kind = data.get("kind", "")
        # sdk_step with reasoning → show
        if kind == "sdk_step":
            reasoning = str(data.get("reasoning") or "").strip()
            return bool(reasoning)
        # sdk_result → skip (internal bookkeeping)
        if kind == "sdk_result":
            return False
        return True
    return True


def summarize_activity(entry: dict, actor_lookup: dict[str, str]) -> str:
    """Produce a single human-readable line for the clean activity stream."""
    if entry["entry_type"] == "event":
        return _clean_event_summary(entry, actor_lookup)
    return _clean_trace_summary(entry, actor_lookup)


def _actor(entry: dict, actor_lookup: dict[str, str]) -> str:
    actor_id = entry.get("actor_id") or ""
    return actor_lookup.get(actor_id, entry.get("actor_name") or "Someone")


def _clean_event_summary(entry: dict, actor_lookup: dict[str, str]) -> str:
    name = _actor(entry, actor_lookup)
    data = entry.get("data") or {}
    event_type = entry["kind"]

    if event_type == "ChatMessageSent":
        body = data.get("body", "")
        recipient = actor_lookup.get(data.get("recipient_actor_id", ""), "")
        if recipient:
            return f'{name} messaged {recipient}: "{body}"'
        return f'{name} sent a message: "{body}"'

    if event_type == "EmailSent":
        subject = data.get("subject", "")
        body = data.get("body", "")
        recipient = actor_lookup.get(data.get("recipient_actor_id", ""), "")
        target = f" to {recipient}" if recipient else ""
        if subject:
            return f'{name} emailed{target} — "{subject}": {body}'
        return f'{name} emailed{target}: "{body}"'

    if event_type == "ThreadCreated":
        return f"{name} started a chat conversation."

    if event_type == "EmailThreadCreated":
        subject = data.get("subject") or data.get("title", "")
        return f'{name} started an email thread: "{subject}"'

    if event_type == "TaskStatusUpdated":
        status = data.get("status", "")
        title = data.get("title", "a task")
        reason = data.get("blocker_reason") or ""
        line = f'{name} marked "{title}" as {status}'
        if reason:
            line += f" — {reason}"
        return line

    if event_type == "TaskCreated":
        return f'{name} created task: "{data.get("title", "")}"'

    if event_type == "TaskAssigneeUpdated":
        assignee = actor_lookup.get(data.get("assignee_actor_id", ""), data.get("assignee_actor_id", "someone"))
        return f'{name} reassigned a task to {assignee}.'

    if event_type == "DocumentUpdated":
        title = data.get("title", "a document")
        return f'{name} updated "{title}".'

    if event_type == "DocumentCreated":
        return f'{name} created document: "{data.get("title", "")}"'

    if event_type == "MeetingScheduled":
        title = data.get("title", "a meeting")
        attendees = data.get("attendee_actor_ids") or []
        attendee_names = [actor_lookup.get(a, a) for a in attendees]
        if attendee_names:
            return f'{name} scheduled "{title}" with {", ".join(attendee_names)}.'
        return f'{name} scheduled "{title}".'

    if event_type == "MeetingStarted":
        title = data.get("title", "A meeting")
        return f'Meeting started: "{title}"'

    if event_type == "MeetingEnded":
        title = data.get("title", "A meeting")
        return f'Meeting ended: "{title}"'

    if event_type == "MeetingSpoken":
        message = data.get("message", "")
        return f'{name} (in meeting): "{message}"'

    if event_type == "MeetingNoteRecorded":
        return f"{name} added meeting notes."

    if event_type == "ProjectPriorityUpdated":
        return f'{name} changed project priority to {data.get("priority", "?")}.'

    if event_type == "ReminderScheduled":
        reason = data.get("reason", "")
        return f'{name} set a reminder: "{reason}"' if reason else f"{name} set a reminder."

    if event_type == "ObligationCreated":
        return f'{name} noted a follow-up: "{data.get("title", "")}"'

    if event_type == "ObligationCompleted":
        return f"{name} completed a follow-up."

    if event_type == "ObligationUpdated":
        return f"{name} updated a follow-up."

    if event_type == "AssignmentFinished":
        return f"{name} declared the assignment complete."

    if event_type == "RunCompleted":
        return "The simulation ended."

    if event_type == "LaunchRiskEscalated":
        reason = data.get("reason", "Launch risk escalated.")
        return reason

    return f"{name}: {event_type}"


def _clean_trace_summary(entry: dict, actor_lookup: dict[str, str]) -> str:
    name = _actor(entry, actor_lookup)
    data = entry.get("data") or {}
    kind = entry.get("kind", "")

    if kind == "command_rejected":
        reason = data.get("reason", "unknown reason")
        return f"{name} tried something that didn't work: {reason}"

    if kind == "introspection_write":
        data_kind = data.get("kind", "")
        if data_kind in ("sdk_step", "claude_step"):
            reasoning = str(data.get("reasoning") or "").strip()
            if reasoning:
                return f'[{name} thinking] "{reasoning}"'
        reasoning = str(data.get("reasoning") or "").strip()
        if reasoning:
            return f'[{name}] "{reasoning}"'

    return f"{name}: {kind}"


# ---------------------------------------------------------------------------
# Sort key
# ---------------------------------------------------------------------------

def activity_sort_key(entry: dict) -> tuple:
    return (
        datetime.fromisoformat(entry["sim_time"]),
        0 if entry["entry_type"] == "trace" else 1,
        int(entry.get("seq") or 0),
        str(entry.get("id") or ""),
    )


# ---------------------------------------------------------------------------
# Legacy summarizers (used by the JSON API endpoints)
# ---------------------------------------------------------------------------

def summarize_event(event, actor_lookup: dict[str, str] | None = None) -> str:
    actor_lookup = actor_lookup or {}
    handler = _EVENT_SUMMARIZERS.get(event.event_type)
    if handler is not None:
        return handler(event, actor_lookup)
    return event.event_type


def summarize_trace(trace, actor_lookup: dict[str, str] | None = None) -> str:
    actor_lookup = actor_lookup or {}
    actor_name = actor_lookup.get(getattr(trace, "actor_id", None), getattr(trace, "actor_id", None) or "System")
    data = getattr(trace, "data_json", {}) or {}
    trace_type = getattr(trace, "trace_type", "")
    if trace_type == "introspection_write":
        kind = str(data.get("kind") or "").strip()
        if kind in ("sdk_step", "claude_step"):
            reasoning = str(data.get("reasoning") or "").strip()
            if reasoning:
                return f"{actor_name} thinking: {reasoning}"
            tool_calls = data.get("tool_calls") or []
            if tool_calls:
                tool_names = ", ".join(call.get("name", "tool") for call in tool_calls)
                return f"{actor_name} considering tools: {tool_names}"
        if kind == "final_decision":
            reasoning = str(data.get("reasoning") or "").strip()
            signal = str(data.get("decision_signal") or "").strip()
            if reasoning:
                return f"{actor_name} deciding: {reasoning}"
            return f"{actor_name} stopped for now ({signal or 'no signal'})."
        if kind == "artifact_storage_warning":
            return f"{actor_name} artifact warning: {data.get('warning')}"
    if trace_type == "system_debug":
        message = str(data.get("message") or "").strip()
        if message:
            return f"System note for {actor_name}: {message}"
    if trace_type == "command_rejected":
        return f"{actor_name} attempted an invalid action: {data.get('reason')}"
    if trace_type == "controller_response":
        reasoning = str(data.get("final_reasoning") or "").strip()
        if reasoning:
            return f"{actor_name} controller response: {reasoning}"
    return f"{actor_name} {trace_type}"


def _actor_name(event, actor_lookup: dict[str, str]) -> str:
    return actor_lookup.get(event.actor_id, event.actor_id or "System")


def _event_data(event) -> dict:
    return event.data_json or {}


@_event_summary("RunCreated")
def _summarize_run_created(event, actor_lookup):
    return f"Run created from scenario {_event_data(event).get('scenario_id')}."

@_event_summary("RunStarted")
def _summarize_run_started(event, actor_lookup):
    return "Run started."

@_event_summary("RunResumed")
def _summarize_run_resumed(event, actor_lookup):
    return "Run resumed."

@_event_summary("RunPaused")
def _summarize_run_paused(event, actor_lookup):
    return "Run paused."

@_event_summary("RunStopped")
def _summarize_run_stopped(event, actor_lookup):
    return "Run stopped."

@_event_summary("RunCompleted")
def _summarize_run_completed(event, actor_lookup):
    return "Run completed."

@_event_summary("ClockAdvanced")
def _summarize_clock_advanced(event, actor_lookup):
    return f"Sim time advanced to {_event_data(event).get('current_sim_time')}."

@_event_summary("AssignmentFinished")
def _summarize_assignment_finished(event, actor_lookup):
    return f"{_actor_name(event, actor_lookup)} declared the assignment complete."

@_event_summary("ScenarioTriggerFired")
def _summarize_scenario_trigger(event, actor_lookup):
    return f"Trigger fired: {_event_data(event).get('trigger_type')}."

@_event_summary("ThreadCreated")
def _summarize_thread_created(event, actor_lookup):
    return f"{_actor_name(event, actor_lookup)} opened a chat thread."

@_event_summary("EmailThreadCreated")
def _summarize_email_thread_created(event, actor_lookup):
    data = _event_data(event)
    return f"{_actor_name(event, actor_lookup)} opened an email thread: {data.get('subject') or data.get('title')}."

@_event_summary("ChatMessageSent")
def _summarize_chat_message(event, actor_lookup):
    return f"{_actor_name(event, actor_lookup)} sent chat: {_event_data(event).get('body')}"

@_event_summary("EmailSent")
def _summarize_email_sent(event, actor_lookup):
    data = _event_data(event)
    return f"{_actor_name(event, actor_lookup)} sent email: {data.get('subject') or data.get('body')}"

@_event_summary("InboxItemsRead")
def _summarize_inbox_cleared(event, actor_lookup):
    return f"{_actor_name(event, actor_lookup)} cleared inbox items."

@_event_summary("ActorCreated")
def _summarize_actor_created(event, actor_lookup):
    data = _event_data(event)
    return f"Actor created: {data.get('name')} ({data.get('role')})."

@_event_summary("WorldObjectCreated")
def _summarize_world_object_created(event, actor_lookup):
    data = _event_data(event)
    return f"World object seeded: {data.get('title')} ({data.get('kind')})."

@_event_summary("TaskCreated")
def _summarize_task_created(event, actor_lookup):
    return f"{_actor_name(event, actor_lookup)} created task: {_event_data(event).get('title')}."

@_event_summary("TaskStatusUpdated")
def _summarize_task_status(event, actor_lookup):
    return f"{_actor_name(event, actor_lookup)} set task status to {_event_data(event).get('status')}."

@_event_summary("TaskAssigneeUpdated")
def _summarize_task_assignee(event, actor_lookup):
    return f"{_actor_name(event, actor_lookup)} reassigned task to {_event_data(event).get('assignee_actor_id')}."

@_event_summary("ProjectPriorityUpdated")
def _summarize_project_priority(event, actor_lookup):
    return f"{_actor_name(event, actor_lookup)} changed project priority to {_event_data(event).get('priority')}."

@_event_summary("DocumentUpdated")
def _summarize_document_updated(event, actor_lookup):
    return f"{_actor_name(event, actor_lookup)} updated a document."

@_event_summary("DocumentCreated")
def _summarize_document_created(event, actor_lookup):
    return f"{_actor_name(event, actor_lookup)} created a document: {_event_data(event).get('title')}."

@_event_summary("ObligationCreated")
def _summarize_obligation_created(event, actor_lookup):
    return f"{_actor_name(event, actor_lookup)} recorded a new follow-up: {_event_data(event).get('title')}."

@_event_summary("ObligationUpdated")
def _summarize_obligation_updated(event, actor_lookup):
    return f"{_actor_name(event, actor_lookup)} updated a follow-up."

@_event_summary("ObligationCompleted")
def _summarize_obligation_completed(event, actor_lookup):
    return f"{_actor_name(event, actor_lookup)} completed a follow-up."

@_event_summary("MeetingScheduled")
def _summarize_meeting_scheduled(event, actor_lookup):
    return f"{_actor_name(event, actor_lookup)} scheduled meeting: {_event_data(event).get('title')}."

@_event_summary("MeetingStarted")
def _summarize_meeting_started(event, actor_lookup):
    return "Meeting started."

@_event_summary("MeetingEnded")
def _summarize_meeting_ended(event, actor_lookup):
    return "Meeting ended."

@_event_summary("MeetingSpoken")
def _summarize_meeting_spoken(event, actor_lookup):
    return f"{_actor_name(event, actor_lookup)} said: {_event_data(event).get('message')}"

@_event_summary("MeetingNoteRecorded")
def _summarize_meeting_note(event, actor_lookup):
    return f"{_actor_name(event, actor_lookup)} added meeting notes."

@_event_summary("ReminderScheduled")
def _summarize_reminder_scheduled(event, actor_lookup):
    return f"{_actor_name(event, actor_lookup)} scheduled a follow-up reminder."

@_event_summary("LaunchRiskEscalated")
def _summarize_launch_risk(event, actor_lookup):
    return _event_data(event).get("reason", "Launch risk escalated.")

@_event_summary("StatePatchSkipped")
def _summarize_state_patch_skipped(event, actor_lookup):
    return "A conditional deadline transition was skipped."
