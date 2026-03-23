from __future__ import annotations

import json
from typing import Any

from claude_agent_sdk import tool as sdk_tool, create_sdk_mcp_server


# Tool definitions: (tool_name, method_name, description, input_schema, permission_key)
TOOL_DEFS: list[tuple[str, str, str, dict[str, Any], str | None]] = [
    ("check_profile", "who_am_i", "Check your own role, team, and time zone.", {}, None),
    ("check_time", "get_current_time", "Check the current local work time.", {}, None),
    ("check_inbox", "get_my_inbox", "Review the latest items across all of your work surfaces.", {"limit": int}, None),
    ("check_chat", "get_my_chat", "Review the latest chat messages sent to you.", {"limit": int}, None),
    ("check_email", "get_my_email", "Review the latest email messages sent to you.", {"limit": int}, None),
    ("check_recent_changes", "review_recent_observations", "Review what has changed around you since you last properly checked in.", {}, None),
    ("review_projects", "list_visible_projects", "List the projects you can currently see.", {}, None),
    ("review_tasks", "list_visible_tasks", "List the tasks you can currently see.", {}, None),
    ("check_calendar", "list_my_meetings", "Check the meetings on your calendar.", {}, None),
    ("review_documents", "list_visible_documents", "List the documents you can currently see.", {}, None),
    ("review_commitments", "list_my_commitments", "Review your active commitments, follow-ups, and due work.", {}, None),
    ("open_document", "read_document", "Read a specific document by its visible title.", {"document": str}, None),
    ("read_conversation", "get_thread_messages", "Read the visible message history for a conversation by its visible title or subject.", {"conversation": str}, None),
    ("read_meeting_notes", "get_meeting_transcript", "Read the live transcript and decisions for a meeting you can see by its visible title.", {"meeting": str}, None),
    ("look_up_people", "list_colleagues", "Look up the people in the company and what they do.", {}, None),
    ("clear_inbox_items", "mark_inbox_items_read", "Mark specific delivered inbox items as read.", {
        "type": "object",
        "properties": {"items": {"type": "array", "items": {"type": "string"}}},
        "required": ["items"],
    }, None),
    ("message_coworker", "send_chat", "Send a chat message to a coworker.", {
        "type": "object",
        "properties": {
            "coworker": {"type": "string"},
            "message": {"type": "string"},
            "conversation": {"type": "string"},
        },
        "required": ["coworker", "message"],
    }, "can_chat"),
    ("email_coworker", "send_email", "Send an email to a coworker.", {
        "type": "object",
        "properties": {
            "coworker": {"type": "string"},
            "subject": {"type": "string"},
            "message": {"type": "string"},
            "conversation": {"type": "string"},
        },
        "required": ["coworker", "subject", "message"],
    }, "can_email"),
    ("reply_in_chat", "reply_thread", "Reply in an existing chat conversation by its visible title.", {
        "type": "object",
        "properties": {"conversation": {"type": "string"}, "message": {"type": "string"}},
        "required": ["conversation", "message"],
    }, "can_chat"),
    ("reply_in_email", "reply_email_thread", "Reply in an existing email conversation by its visible subject line.", {
        "type": "object",
        "properties": {"conversation": {"type": "string"}, "message": {"type": "string"}},
        "required": ["conversation", "message"],
    }, "can_email"),
    ("change_task_status", "update_task_status", "Change the status of a task.", {
        "type": "object",
        "properties": {
            "task": {"type": "string"},
            "status": {"type": "string"},
            "blocker_reason": {"type": "string"},
        },
        "required": ["task", "status"],
    }, "can_edit_tasks"),
    ("reassign_task", "update_task_assignee", "Reassign a task to another coworker.", {
        "type": "object",
        "properties": {"task": {"type": "string"}, "assignee": {"type": "string"}},
        "required": ["task", "assignee"],
    }, "can_edit_tasks"),
    ("add_task", "create_task", "Create a new task.", {
        "type": "object",
        "properties": {
            "title": {"type": "string"}, "assignee": {"type": "string"},
            "project": {"type": "string"}, "description": {"type": "string"},
            "priority": {"type": "string"}, "due_at": {"type": "string"},
        },
        "required": ["title"],
    }, "can_edit_tasks"),
    ("change_project_priority", "update_project_priority", "Change a project's priority.", {
        "type": "object",
        "properties": {"project": {"type": "string"}, "priority": {"type": "string"}},
        "required": ["project", "priority"],
    }, "can_manage_projects"),
    ("write_document", "create_document", "Create a new document.", {
        "type": "object",
        "properties": {"title": {"type": "string"}, "content": {"type": "string"}, "visibility": {"type": "object"}},
        "required": ["title"],
    }, "can_edit_docs"),
    ("edit_document", "update_document", "Update an existing document by its visible title.", {
        "type": "object",
        "properties": {"document": {"type": "string"}, "content": {"type": "string"}, "append": {"type": "boolean"}},
        "required": ["document", "content"],
    }, "can_edit_docs"),
    ("schedule_meeting", "schedule_meeting", "Schedule a meeting with coworkers.", {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "attendees": {"type": "array", "items": {"type": "string"}},
            "starts_in_minutes": {"type": "integer", "minimum": 1},
            "duration_minutes": {"type": "integer", "minimum": 1},
            "agenda": {"type": "string"},
            "related_work": {"type": "string"},
        },
        "required": ["title", "attendees", "starts_in_minutes", "duration_minutes"],
    }, "can_schedule_meetings"),
    ("add_meeting_notes", "record_meeting_note", "Add notes to a meeting you are attending.", {
        "type": "object",
        "properties": {"meeting": {"type": "string"}, "note": {"type": "string"}},
        "required": ["meeting", "note"],
    }, None),
    ("speak_in_meeting", "speak_in_meeting", "Say something in a live meeting you are attending.", {
        "type": "object",
        "properties": {"meeting": {"type": "string"}, "message": {"type": "string"}},
        "required": ["meeting", "message"],
    }, None),
    ("mark_commitment_done", "complete_commitment", "Mark one of your commitments complete.", {
        "type": "object",
        "properties": {"commitment": {"type": "string"}, "resolution_note": {"type": "string"}},
        "required": ["commitment"],
    }, None),
    ("delay_commitment", "defer_commitment", "Defer one of your commitments to a later time.", {
        "type": "object",
        "properties": {
            "commitment": {"type": "string"},
            "minutes_from_now": {"type": "integer", "minimum": 1},
            "reason": {"type": "string"},
        },
        "required": ["commitment", "minutes_from_now", "reason"],
    }, None),
    ("set_reminder", "schedule_self_wake", "Set a personal reminder to revisit work later.", {
        "type": "object",
        "properties": {"minutes_from_now": {"type": "integer", "minimum": 1}, "reason": {"type": "string"}},
        "required": ["minutes_from_now", "reason"],
    }, None),
    ("review_wrap_up", "review_completion_readiness", "Review whether your assignment is actually buttoned up before you stop.", {}, "can_finish_assignment"),
    ("wrap_up_assignment", "finish_assignment", "Conclude your assignment.", {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "remaining_risks": {"type": "array", "items": {"type": "string"}},
            "confidence": {"type": "string"},
        },
        "required": ["summary"],
    }, "can_finish_assignment"),
]


def _tool_available(permission_key: str | None, permissions: dict[str, Any]) -> bool:
    if permission_key is None:
        return True
    return bool(permissions.get(permission_key, False))


def build_mcp_server(toolkit, permissions: dict[str, Any]):
    """Build an in-process MCP server from the toolkit, respecting actor permissions."""
    tools = []
    for tool_name, method_name, description, schema, perm_key in TOOL_DEFS:
        if not _tool_available(perm_key, permissions):
            continue

        handler_fn = getattr(toolkit, method_name, None)
        if handler_fn is None:
            continue

        # Normalize schema: simple dict like {"name": str} → auto, full JSON schema → pass through
        if schema and "type" not in schema:
            input_schema = schema
        else:
            input_schema = schema or {}

        @sdk_tool(tool_name, description, input_schema)
        async def _handler(args: dict[str, Any], _fn=handler_fn) -> dict[str, Any]:
            result = _fn(**args)
            text = json.dumps(result, ensure_ascii=False, default=str)
            return {"content": [{"type": "text", "text": text}]}

        tools.append(_handler)

    return create_sdk_mcp_server(name="sim", version="1.0.0", tools=tools)


def available_tool_names(permissions: dict[str, Any]) -> list[str]:
    """Return MCP tool names (mcp__sim__<name>) available for the given permissions."""
    return [
        f"mcp__sim__{tool_name}"
        for tool_name, _, _, _, perm_key in TOOL_DEFS
        if _tool_available(perm_key, permissions)
    ]
