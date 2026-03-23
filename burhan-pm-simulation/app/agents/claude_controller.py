from __future__ import annotations

import logging
from pathlib import Path
from textwrap import dedent
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    get_session_info,
)

from app.agents.base import BaseController, ControllerDecision
from app.agents.session_state import ActorTurnContext
from app.agents.tool_registry import available_tool_names, build_mcp_server
from app.services import state_store
from app.services.config import get_settings
from app.services.db import session_scope

logger = logging.getLogger(__name__)


class ClaudeController(BaseController):

    async def decide(self, context: ActorTurnContext, toolkit: Any) -> ControllerDecision:
        settings = get_settings()
        permissions = context.permissions or {}

        mcp_server = build_mcp_server(toolkit, permissions)
        allowed = available_tool_names(permissions)

        # Look up existing session for this actor
        session_id = None
        with session_scope() as session:
            session_id = state_store.get_sdk_session_id(session, context.run_id, context.actor_id)

        # Session working directory — unique per run so sessions don't bleed
        session_cwd = settings.sdk_session_dir / context.run_id
        session_cwd.mkdir(parents=True, exist_ok=True)

        # Verify session file still exists on disk; if wiped (e.g. container restart),
        # fall back to a fresh first-turn with full context rather than crashing.
        if session_id and get_session_info(session_id, directory=str(session_cwd)) is None:
            logger.warning(
                "SDK session file missing on disk, falling back to first-turn prompt: "
                "run_id=%s actor_id=%s session_id=%s",
                context.run_id, context.actor_id, session_id,
            )
            session_id = None

        options = ClaudeAgentOptions(
            system_prompt=_build_system_prompt(context),
            model=context.model or settings.claude_model,
            mcp_servers={"sim": mcp_server},
            allowed_tools=allowed,
            permission_mode="bypassPermissions",
            max_turns=settings.claude_max_turns,
            cwd=str(session_cwd),
            resume=session_id,
            tools=[],
        )

        prompt = _build_turn_prompt(context, is_first_turn=(session_id is None))

        introspection: list[dict[str, Any]] = []
        final_text = ""
        result_session_id = session_id
        cost_usd = None
        step_index = 0

        async with ClaudeSDKClient(options=options) as client:
            await client.query(prompt)
            async for msg in client.receive_response():
                if isinstance(msg, AssistantMessage):
                    step_index += 1
                    text_parts = []
                    tool_calls = []
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            text_parts.append(block.text)
                        elif isinstance(block, ToolUseBlock):
                            tool_calls.append({"name": block.name, "input": block.input})
                    step_text = "\n".join(text_parts).strip()
                    if step_text:
                        final_text = step_text
                    introspection.append({
                        "kind": "sdk_step",
                        "step_index": step_index,
                        "reasoning": step_text,
                        "tool_calls": tool_calls,
                        "sim_time": context.current_sim_time.isoformat(),
                    })
                elif isinstance(msg, ResultMessage):
                    result_session_id = msg.session_id
                    cost_usd = msg.total_cost_usd
                    if msg.result:
                        final_text = msg.result
                    introspection.append({
                        "kind": "sdk_result",
                        "session_id": msg.session_id,
                        "num_turns": msg.num_turns,
                        "duration_ms": msg.duration_ms,
                        "cost_usd": msg.total_cost_usd,
                        "stop_reason": msg.stop_reason,
                        "is_error": msg.is_error,
                    })

        # Persist session ID for next turn
        if result_session_id:
            with session_scope() as session:
                state_store.update_sdk_session_id(
                    session,
                    run_id=context.run_id,
                    actor_id=context.actor_id,
                    sdk_session_id=result_session_id,
                )

        has_actions = len(toolkit.executed_commands) > 0
        decision_signal = "commands_executed" if has_actions else "deliberate_no_action"

        return ControllerDecision(
            introspection_entries=introspection,
            decision_signal=decision_signal,
            final_reasoning=final_text or "Nothing else needs attention right now.",
            session_id=result_session_id,
            cost_usd=cost_usd,
        )


def _build_system_prompt(context: ActorTurnContext) -> str:
    return dedent(f"""
        You are {context.actor_name}.
    """).strip()


def _build_turn_prompt(context: ActorTurnContext, *, is_first_turn: bool) -> str:
    if is_first_turn:
        return _build_full_context_prompt(context)
    return _build_update_prompt(context)


def _build_full_context_prompt(context: ActorTurnContext) -> str:
    """First turn: full identity + world state."""
    character = _character_prompt(context)
    assignment_text = _format_assignment(context)
    on_clock = context.work_availability.get("within_working_hours")
    deadline = _format_deadline(context)

    parts = [
        f"You are {context.actor_name}, {context.actor_role}{f' on the {context.actor_team} team' if context.actor_team else ''}.",
        "",
        character,
        "",
        f"It is {context.local_current_time}. {'You are at work.' if on_clock else 'You are outside your normal working hours.'}",
    ]

    if deadline != "no hard deadline set":
        parts.append(f"Just so you know — {deadline}")

    if assignment_text:
        parts.append("")
        parts.append(assignment_text)

    obs = _render_observations(context.observations)
    if context.observations:
        parts.append("")
        parts.append(f"Since you last checked in:\n{obs}")

    commits = _render_commitments(context.current_commitments, context.actor_directory)
    if context.current_commitments:
        parts.append("")
        parts.append(f"You owe follow-ups on:\n{commits}")

    vis = _render_visible_objects(context.visible_objects, context.actor_directory)
    if context.visible_objects:
        parts.append("")
        parts.append(f"Work around you right now:\n{vis}")

    parts.append("")
    parts.append(f"You can {_describe_work_affordances(context.permissions)}.")

    return "\n".join(parts)


def _build_update_prompt(context: ActorTurnContext) -> str:
    """Subsequent turn: just inject what's new."""
    parts = [f"It is now {context.local_current_time}."]

    if context.observations:
        parts.append(f"\nSince you last checked in:\n{_render_observations(context.observations)}")

    if context.current_commitments:
        parts.append(f"\nYou still owe:\n{_render_commitments(context.current_commitments, context.actor_directory)}")

    parts.append("")
    return "\n".join(parts)


def _format_assignment(context: ActorTurnContext) -> str:
    if not context.assignment:
        return ""
    brief = context.assignment.get("visible_brief", "").strip()
    if brief:
        return brief
    title = context.assignment.get("title", "")
    goal = context.assignment.get("primary_goal", "")
    return f"{title}. {goal}".strip() if goal else title


def _character_prompt(context: ActorTurnContext) -> str:
    explicit = str((context.profile or {}).get("character_prompt") or "").strip()
    if explicit:
        return explicit
    return ""


def _render_observations(observations: list[dict[str, Any]]) -> str:
    if not observations:
        return "- nothing new has become salient since you last checked in"
    lines = []
    for item in observations:
        headline = str(item.get("headline") or "").strip()
        summary = str(item.get("summary") or "").strip()
        at = item.get("at")
        if summary:
            lines.append(f"- {headline} At {at}: {summary}")
        else:
            lines.append(f"- {headline} At {at}.")
    return "\n".join(lines)


def _render_commitments(commitments: list[dict[str, Any]], actor_directory: dict[str, str]) -> str:
    if not commitments:
        return "- you do not currently owe any explicit follow-up"
    lines = []
    for c in commitments:
        due_at = c.get("due_at") or "no due time"
        summary = c.get("summary") or c.get("title")
        related = c.get("related_person")
        if related:
            summary = f"{summary} (with {related})"
        lines.append(f"- {c.get('title')}: {summary}; due {due_at}")
    return "\n".join(lines)


def _render_visible_objects(objects: list[dict[str, Any]], actor_directory: dict[str, str]) -> str:
    if not objects:
        return "- no visible projects, tasks, meetings, or documents"
    grouped: dict[str, list[str]] = {}
    for obj in objects:
        kind = obj.get("kind", "other")
        state = obj.get("state") or {}
        title = str(obj.get("title"))
        details = []
        if state.get("status"):
            details.append(str(state["status"]))
        assignee = state.get("assignee_actor_id")
        if assignee:
            details.append(f"owner {actor_directory.get(assignee, assignee)}")
        summary = f"{title} ({'; '.join(details)})" if details else title
        grouped.setdefault(kind, []).append(summary)
    lines = []
    for kind in sorted(grouped):
        lines.append(f"- {kind}: {'; '.join(grouped[kind])}")
    return "\n".join(lines)


def _format_deadline(context: ActorTurnContext) -> str:
    assignment = context.assignment or {}
    deadline_days = assignment.get("deadline_days")
    if not deadline_days:
        return "no hard deadline set"
    start_str = assignment.get("start_sim_time") or ""
    if start_str:
        try:
            from datetime import datetime, timedelta
            start = datetime.fromisoformat(start_str)
            deadline = start + timedelta(days=int(deadline_days))
            return f"the simulation ends at {deadline.strftime('%A %I:%M %p')} ({deadline_days} day{'s' if int(deadline_days) != 1 else ''} total). Work within this window — when time is up, the run stops automatically."
        except Exception:
            pass
    return f"{deadline_days} day{'s' if int(deadline_days) != 1 else ''} from start"


def _describe_work_affordances(permissions: dict[str, Any]) -> str:
    capabilities = [
        "check inboxes and read thread history",
        "review what changed since you last checked in",
        "review and manage your current commitments and follow-ups",
        "inspect visible projects, tasks, meetings, and documents",
        "look up coworkers in the company directory",
        "mark inbox items read",
    ]
    if permissions.get("can_chat"):
        capabilities.append("send and reply to chat messages")
    if permissions.get("can_email"):
        capabilities.append("send and reply to email")
    if permissions.get("can_edit_tasks"):
        capabilities.append("create tasks and update task ownership or status")
    if permissions.get("can_manage_projects"):
        capabilities.append("change project priority")
    if permissions.get("can_edit_docs"):
        capabilities.append("create and edit documents")
    if permissions.get("can_schedule_meetings"):
        capabilities.append("schedule meetings")
    capabilities.append("read meeting transcripts, speak, and record notes when attending a meeting")
    capabilities.append("leave yourself reminders")
    if permissions.get("can_finish_assignment"):
        capabilities.append("review completion readiness and finish the assignment when the work is handled")
    return ", ".join(capabilities)


