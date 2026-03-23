from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from app.domain.commands import IntentCommand
from app.domain.engine import CommandRejected, apply_command
from app.domain.models import TraceType
from app.domain.visibility import actor_can_view
from app.services import (
    attention_service,
    closure_service,
    delivery_service,
    event_store,
    perception_service,
    state_store,
    trace_store,
)
from app.services.reference_resolver import (
    ActorIdentity,
    ReferenceResolutionError,
    resolve_actor_id,
    resolve_delivery_references,
    resolve_owned_obligation_id,
    resolve_visible_object_id,
)
from app.services.db import session_scope

logger = logging.getLogger(__name__)


class ControllerToolkit:
    def __init__(
        self,
        *,
        run_id: str,
        actor,
        current_sim_time: datetime,
    ) -> None:
        self.run_id = run_id
        self.actor = actor
        self.current_sim_time = current_sim_time
        self._trace_entries: list[dict[str, Any]] = []
        self._executed_commands: list[dict[str, Any]] = []

    def _read_current_sim_time(self) -> datetime:
        """Read the current sim time fresh from DB so each action sees the real clock."""
        with session_scope() as session:
            run = state_store.get_run(session, self.run_id)
            return run.current_sim_time if run else self.current_sim_time

    @property
    def executed_commands(self) -> list[dict[str, Any]]:
        return list(self._executed_commands)

    def consume_trace_entries(self) -> list[dict[str, Any]]:
        entries = list(self._trace_entries)
        self._trace_entries.clear()
        return entries

    def _append_trace(
        self,
        trace_type: TraceType,
        tool_name: str,
        data: dict[str, Any],
        *,
        sim_time: datetime | None = None,
    ) -> None:
        self._trace_entries.append(
            {
                "trace_type": trace_type.value,
                "data": {"tool_name": tool_name, **data},
                "sim_time": (sim_time or self.current_sim_time).isoformat(),
            }
        )


    def _actor_identity(self) -> ActorIdentity:
        return ActorIdentity(
            id=self.actor.id,
            role=getattr(self.actor, "role", None),
            team=getattr(self.actor, "team", None),
        )

    # -- Reference resolution helpers --

    def _resolve_actor(self, raw: str) -> str:
        with session_scope() as session:
            return resolve_actor_id(session, run_id=self.run_id, raw_reference=raw)

    def _resolve_object(self, raw: str, *, kind: str | None, surface: str | None = None) -> str:
        with session_scope() as session:
            return resolve_visible_object_id(
                session,
                run_id=self.run_id,
                actor=self._actor_identity(),
                raw_reference=raw,
                kind=kind,
                surface=surface,
            )

    def _resolve_commitment(self, raw: str) -> str:
        with session_scope() as session:
            return resolve_owned_obligation_id(
                session,
                run_id=self.run_id,
                actor_id=self.actor.id,
                raw_reference=raw,
            )

    # -- Command execution --

    def _execute_command(
        self,
        *,
        tool_name: str,
        command_type: str,
        target_ref: dict[str, Any],
        payload: dict[str, Any],
        trace_data: dict[str, Any],
    ) -> dict[str, Any]:
        # Read the current sim time fresh from DB so each action sees the real clock.
        effective_at = self._read_current_sim_time()

        if not attention_service.is_within_working_hours(self.actor, effective_at):
            self._append_trace(
                TraceType.ACTION_ATTEMPT,
                tool_name,
                {**trace_data, "effective_at": effective_at.isoformat(), "executed": False, "reason": "outside_working_hours"},
                sim_time=effective_at,
            )
            return {"error": "outside_working_hours", "command_type": command_type}

        command = IntentCommand(
            command_type=command_type,
            actor_id=self.actor.id,
            issued_at_sim=effective_at,
            target_ref=target_ref,
            payload=payload,
        )

        with session_scope() as session:
            run = state_store.get_run(session, self.run_id)
            actor = state_store.get_actor(session, self.run_id, self.actor.id)
            if run is None or actor is None:
                return {"error": "missing_run_or_actor"}

            try:
                apply_command(session, run=run, actor=actor, command=command)
            except CommandRejected as exc:
                self._append_trace(
                    TraceType.COMMAND_REJECTED,
                    tool_name,
                    {**trace_data, "effective_at": effective_at.isoformat(), "reason": str(exc)},
                    sim_time=effective_at,
                )
                return {"error": str(exc), "command_type": command_type}

        self._append_trace(
            TraceType.ACTION_ATTEMPT,
            tool_name,
            {**trace_data, "effective_at": effective_at.isoformat(), "executed": True},
            sim_time=effective_at,
        )
        result = {"ok": True, "command_type": command_type, "effective_at": effective_at.isoformat()}
        self._executed_commands.append(result)
        return result

    # -- Read tools --

    def who_am_i(self) -> dict[str, Any]:
        payload = {
            "actor_id": self.actor.id,
            "name": self.actor.name,
            "role": self.actor.role,
            "team": self.actor.team,
            "timezone": self.actor.timezone,
        }
        self._append_trace(TraceType.AWARENESS_READ, "check_profile", payload)
        return payload

    def get_current_time(self) -> str:
        now = self._read_current_sim_time()
        local_time = attention_service.local_time_for_actor(self.actor, now)
        self._append_trace(
            TraceType.AWARENESS_READ,
            "check_time",
            {"company_time": now.isoformat(), "local_time": local_time.isoformat()},
        )
        return local_time.isoformat()

    def get_my_inbox(self, limit: int = 20) -> list[dict]:
        self._append_trace(TraceType.AWARENESS_READ, "check_inbox", {"limit": limit})
        with session_scope() as session:
            return delivery_service.list_inbox(
                session, run_id=self.run_id, actor_id=self.actor.id,
                current_sim_time=self.current_sim_time, limit=limit,
            )

    def get_my_chat(self, limit: int = 20) -> list[dict]:
        self._append_trace(TraceType.AWARENESS_READ, "check_chat", {"limit": limit})
        with session_scope() as session:
            return delivery_service.list_inbox(
                session, run_id=self.run_id, actor_id=self.actor.id,
                current_sim_time=self.current_sim_time, surface="chat", limit=limit,
            )

    def get_my_email(self, limit: int = 20) -> list[dict]:
        self._append_trace(TraceType.AWARENESS_READ, "check_email", {"limit": limit})
        with session_scope() as session:
            return delivery_service.list_inbox(
                session, run_id=self.run_id, actor_id=self.actor.id,
                current_sim_time=self.current_sim_time, surface="email", limit=limit,
            )

    def review_recent_observations(self) -> list[dict]:
        self._append_trace(TraceType.AWARENESS_READ, "check_recent_changes", {})
        with session_scope() as session:
            run = state_store.get_run(session, self.run_id)
            actor_state = state_store.get_actor_state(session, self.run_id, self.actor.id)
            if run is None or actor_state is None:
                return []
            actor_directory = {c.id: c.name for c in state_store.list_actors(session, self.run_id)}
            return perception_service.list_actor_observations(
                session, run=run, actor=self.actor, actor_state=actor_state, actor_directory=actor_directory,
            )

    def list_visible_projects(self) -> list[dict]:
        self._append_trace(TraceType.AWARENESS_READ, "review_projects", {})
        with session_scope() as session:
            objects = state_store.list_visible_world_objects(
                session, run_id=self.run_id, actor_id=self.actor.id,
                actor_role=self.actor.role, actor_team=self.actor.team, kind="project",
            )
        return [_serialize_object(r) for r in objects]

    def list_visible_tasks(self) -> list[dict]:
        self._append_trace(TraceType.AWARENESS_READ, "review_tasks", {})
        with session_scope() as session:
            objects = state_store.list_visible_world_objects(
                session, run_id=self.run_id, actor_id=self.actor.id,
                actor_role=self.actor.role, actor_team=self.actor.team, kind="task",
            )
        return [_serialize_object(r) for r in objects]

    def list_my_meetings(self) -> list[dict]:
        self._append_trace(TraceType.AWARENESS_READ, "check_calendar", {})
        with session_scope() as session:
            meetings = state_store.list_visible_world_objects(
                session, run_id=self.run_id, actor_id=self.actor.id,
                actor_role=self.actor.role, actor_team=self.actor.team, kind="meeting",
            )
        return [
            _serialize_object(r) for r in meetings
            if self.actor.id in (r.state_json or {}).get("attendee_actor_ids", [])
        ]

    def list_visible_documents(self) -> list[dict]:
        self._append_trace(TraceType.AWARENESS_READ, "review_documents", {})
        with session_scope() as session:
            objects = state_store.list_visible_world_objects(
                session, run_id=self.run_id, actor_id=self.actor.id,
                actor_role=self.actor.role, actor_team=self.actor.team, kind="document",
            )
        return [_serialize_object(r) for r in objects]

    def list_my_commitments(self) -> list[dict]:
        self._append_trace(TraceType.AWARENESS_READ, "review_commitments", {})
        with session_scope() as session:
            actor_directory = {c.id: c.name for c in state_store.list_actors(session, self.run_id)}
            return perception_service.list_actor_commitments(
                session, run_id=self.run_id, actor_id=self.actor.id, actor_directory=actor_directory,
            )

    def read_document(self, document: str) -> dict[str, Any]:
        self._append_trace(TraceType.AWARENESS_READ, "open_document", {"document": document})
        with session_scope() as session:
            try:
                doc_id = resolve_visible_object_id(
                    session, run_id=self.run_id, actor=self._actor_identity(),
                    raw_reference=document, kind="document",
                )
            except ReferenceResolutionError:
                return {"error": "document not found"}
            doc = state_store.get_world_object(session, self.run_id, doc_id)
            if doc is None or doc.kind != "document":
                return {"error": "document not found"}
            if not actor_can_view(
                actor_id=self.actor.id, actor_role=self.actor.role,
                actor_team=self.actor.team, visibility=doc.visibility_json,
            ):
                return {"error": "document not visible"}
            return _serialize_object(doc)

    def get_thread_messages(self, conversation: str) -> list[dict[str, Any]]:
        self._append_trace(TraceType.AWARENESS_READ, "read_conversation", {"conversation": conversation})
        with session_scope() as session:
            try:
                thread_id = resolve_visible_object_id(
                    session, run_id=self.run_id, actor=self._actor_identity(),
                    raw_reference=conversation, kind="thread",
                )
            except ReferenceResolutionError:
                return [{"error": "thread not found"}]
            thread = state_store.get_world_object(session, self.run_id, thread_id)
            if thread is None or thread.kind != "thread":
                return [{"error": "thread not found"}]
            if not actor_can_view(
                actor_id=self.actor.id, actor_role=self.actor.role,
                actor_team=self.actor.team, visibility=thread.visibility_json,
            ):
                return [{"error": "thread not visible"}]
            events = event_store.list_thread_messages(
                session, run_id=self.run_id, thread_id=thread_id,
                actor_id=self.actor.id, actor_role=self.actor.role, actor_team=self.actor.team,
            )
            delivery_service.mark_thread_deliveries_read(
                session, run_id=self.run_id, actor_id=self.actor.id,
                thread_id=thread_id, read_at_sim=self._read_current_sim_time(),
            )
            actor_lookup = {a.id: a.name for a in state_store.list_actors(session, self.run_id)}
        return [
            {
                "event_id": e.id, "event_type": e.event_type,
                "sender_actor_id": e.actor_id,
                "sender_name": actor_lookup.get(e.actor_id, e.actor_id),
                "sim_time": e.sim_time.isoformat(),
                "body": (e.data_json or {}).get("body"),
                "subject": (e.data_json or {}).get("subject"),
            }
            for e in events
        ]

    def get_meeting_transcript(self, meeting: str) -> dict[str, Any]:
        self._append_trace(TraceType.AWARENESS_READ, "read_meeting_notes", {"meeting": meeting})
        with session_scope() as session:
            try:
                meeting_id = resolve_visible_object_id(
                    session, run_id=self.run_id, actor=self._actor_identity(),
                    raw_reference=meeting, kind="meeting",
                )
            except ReferenceResolutionError:
                return {"error": "meeting not found"}
            obj = state_store.get_world_object(session, self.run_id, meeting_id)
            if obj is None or obj.kind != "meeting":
                return {"error": "meeting not found"}
            if not actor_can_view(
                actor_id=self.actor.id, actor_role=self.actor.role,
                actor_team=self.actor.team, visibility=obj.visibility_json,
            ):
                return {"error": "meeting not visible"}
            return _serialize_object(obj)

    def list_colleagues(self) -> list[dict[str, Any]]:
        self._append_trace(TraceType.AWARENESS_READ, "look_up_people", {})
        with session_scope() as session:
            actors = state_store.list_actors(session, self.run_id)
        return [
            {"actor_id": a.id, "name": a.name, "role": a.role, "team": a.team}
            for a in actors
        ]

    def review_completion_readiness(self) -> dict[str, Any]:
        self._append_trace(TraceType.AWARENESS_READ, "review_wrap_up", {})
        with session_scope() as session:
            return closure_service.compute_completion_readiness(
                session, run_id=self.run_id, actor_id=self.actor.id,
            )

    # -- Write tools (execute commands directly) --

    def mark_inbox_items_read(self, items: list[str]) -> dict[str, Any]:
        all_look_like_ids = all(item.strip().startswith("del_") for item in items if item.strip())
        if all_look_like_ids:
            delivery_ids = [item.strip() for item in items if item.strip()]
        else:
            with session_scope() as session:
                delivery_ids = resolve_delivery_references(
                    session, run_id=self.run_id, actor_id=self.actor.id,
                    raw_references=items, current_sim_time=self.current_sim_time,
                )
            if not delivery_ids:
                return {"error": f"could not resolve inbox items from: {items}"}
        return self._execute_command(
            tool_name="clear_inbox_items",
            command_type="inbox.mark_read",
            target_ref={"actor_id": self.actor.id},
            payload={"delivery_ids": delivery_ids},
            trace_data={"items": items, "resolved_delivery_ids": delivery_ids},
        )

    def send_chat(self, *, coworker: str, message: str, conversation: str | None = None) -> dict[str, Any]:
        try:
            recipient_id = self._resolve_actor(coworker)
        except ReferenceResolutionError as exc:
            return {"error": str(exc)}
        thread_id = None
        if conversation:
            try:
                thread_id = self._resolve_object(conversation, kind="thread", surface="chat")
            except ReferenceResolutionError as exc:
                return {"error": str(exc)}
        return self._execute_command(
            tool_name="message_coworker",
            command_type="communicate.send_chat",
            target_ref={"recipient_actor_id": recipient_id, "thread_id": thread_id},
            payload={"body": message},
            trace_data={"coworker": coworker, "resolved_coworker_id": recipient_id, "message": message},
        )

    def send_email(self, *, coworker: str, subject: str, message: str, conversation: str | None = None) -> dict[str, Any]:
        try:
            recipient_id = self._resolve_actor(coworker)
        except ReferenceResolutionError as exc:
            return {"error": str(exc)}
        thread_id = None
        if conversation:
            try:
                thread_id = self._resolve_object(conversation, kind="thread", surface="email")
            except ReferenceResolutionError as exc:
                return {"error": str(exc)}
        return self._execute_command(
            tool_name="email_coworker",
            command_type="communicate.send_email",
            target_ref={"recipient_actor_id": recipient_id, "thread_id": thread_id},
            payload={"subject": subject, "body": message},
            trace_data={"coworker": coworker, "subject": subject, "message": message},
        )

    def reply_thread(self, *, conversation: str, message: str) -> dict[str, Any]:
        try:
            thread_id = self._resolve_object(conversation, kind="thread", surface="chat")
        except ReferenceResolutionError as exc:
            return {"error": str(exc)}
        return self._execute_command(
            tool_name="reply_in_chat",
            command_type="communicate.reply_thread",
            target_ref={"thread_id": thread_id},
            payload={"body": message},
            trace_data={"conversation": conversation, "message": message},
        )

    def reply_email_thread(self, *, conversation: str, message: str) -> dict[str, Any]:
        try:
            thread_id = self._resolve_object(conversation, kind="thread", surface="email")
        except ReferenceResolutionError as exc:
            return {"error": str(exc)}
        return self._execute_command(
            tool_name="reply_in_email",
            command_type="communicate.reply_email_thread",
            target_ref={"thread_id": thread_id},
            payload={"body": message},
            trace_data={"conversation": conversation, "message": message},
        )

    def update_task_status(self, *, task: str, status: str, blocker_reason: str | None = None) -> dict[str, Any]:
        try:
            task_id = self._resolve_object(task, kind="task")
        except ReferenceResolutionError as exc:
            return {"error": str(exc)}
        return self._execute_command(
            tool_name="change_task_status",
            command_type="tasks.update_status",
            target_ref={"task_id": task_id},
            payload={"status": status, "blocker_reason": blocker_reason},
            trace_data={"task": task, "status": status, "blocker_reason": blocker_reason},
        )

    def update_task_assignee(self, *, task: str, assignee: str) -> dict[str, Any]:
        try:
            task_id = self._resolve_object(task, kind="task")
        except ReferenceResolutionError as exc:
            return {"error": str(exc)}
        try:
            assignee_id = self._resolve_actor(assignee)
        except ReferenceResolutionError as exc:
            return {"error": str(exc)}
        return self._execute_command(
            tool_name="reassign_task",
            command_type="tasks.update_assignee",
            target_ref={"task_id": task_id},
            payload={"assignee_actor_id": assignee_id},
            trace_data={"task": task, "assignee": assignee},
        )

    def create_task(
        self, *, title: str, assignee: str | None = None, project: str | None = None,
        description: str = "", priority: str = "medium", due_at: str | None = None,
    ) -> dict[str, Any]:
        assignee_id = None
        if assignee:
            try:
                assignee_id = self._resolve_actor(assignee)
            except ReferenceResolutionError as exc:
                return {"error": str(exc)}
        project_id = None
        if project:
            try:
                project_id = self._resolve_object(project, kind="project")
            except ReferenceResolutionError as exc:
                return {"error": str(exc)}
        return self._execute_command(
            tool_name="add_task",
            command_type="tasks.create",
            target_ref={},
            payload={
                "title": title, "assignee_actor_id": assignee_id, "project_id": project_id,
                "description": description, "priority": priority, "due_at": due_at,
            },
            trace_data={"title": title, "assignee": assignee, "project": project, "priority": priority},
        )

    def update_project_priority(self, *, project: str, priority: str) -> dict[str, Any]:
        try:
            project_id = self._resolve_object(project, kind="project")
        except ReferenceResolutionError as exc:
            return {"error": str(exc)}
        return self._execute_command(
            tool_name="change_project_priority",
            command_type="projects.update_priority",
            target_ref={"project_id": project_id},
            payload={"priority": priority},
            trace_data={"project": project, "priority": priority},
        )

    def update_document(self, *, document: str, content: str, append: bool = False) -> dict[str, Any]:
        try:
            doc_id = self._resolve_object(document, kind="document")
        except ReferenceResolutionError as exc:
            return {"error": str(exc)}
        return self._execute_command(
            tool_name="edit_document",
            command_type="documents.update",
            target_ref={"document_id": doc_id},
            payload={"body": content, "append": append},
            trace_data={"document": document, "append": append},
        )

    def create_document(self, *, title: str, content: str = "", visibility: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._execute_command(
            tool_name="write_document",
            command_type="documents.create",
            target_ref={},
            payload={"title": title, "body": content, "visibility": visibility or {"scope": "company"}},
            trace_data={"title": title},
        )

    def schedule_meeting(
        self, *, title: str, attendees: list[str], starts_in_minutes: int,
        duration_minutes: int, agenda: str = "", related_work: str | None = None,
    ) -> dict[str, Any]:
        resolved_attendees = []
        for ref in attendees:
            try:
                aid = self._resolve_actor(ref)
            except ReferenceResolutionError as exc:
                return {"error": str(exc)}
            if aid not in resolved_attendees:
                resolved_attendees.append(aid)
        related_id = None
        if related_work:
            try:
                related_id = self._resolve_object(related_work, kind=None)
            except ReferenceResolutionError as exc:
                return {"error": str(exc)}
        return self._execute_command(
            tool_name="schedule_meeting",
            command_type="meetings.schedule",
            target_ref={},
            payload={
                "title": title, "attendee_actor_ids": resolved_attendees,
                "starts_in_minutes": starts_in_minutes, "duration_minutes": duration_minutes,
                "agenda": agenda, "related_object_id": related_id,
            },
            trace_data={"title": title, "attendees": attendees},
        )

    def record_meeting_note(self, *, meeting: str, note: str) -> dict[str, Any]:
        try:
            meeting_id = self._resolve_object(meeting, kind="meeting")
        except ReferenceResolutionError as exc:
            return {"error": str(exc)}
        return self._execute_command(
            tool_name="add_meeting_notes",
            command_type="meetings.record_note",
            target_ref={"meeting_id": meeting_id},
            payload={"note": note},
            trace_data={"meeting": meeting, "note": note},
        )

    def speak_in_meeting(self, *, meeting: str, message: str) -> dict[str, Any]:
        try:
            meeting_id = self._resolve_object(meeting, kind="meeting")
        except ReferenceResolutionError as exc:
            return {"error": str(exc)}
        return self._execute_command(
            tool_name="speak_in_meeting",
            command_type="meetings.speak",
            target_ref={"meeting_id": meeting_id},
            payload={"message": message},
            trace_data={"meeting": meeting, "message": message},
        )

    def complete_commitment(self, *, commitment: str, resolution_note: str = "") -> dict[str, Any]:
        try:
            obligation_id = self._resolve_commitment(commitment)
        except ReferenceResolutionError as exc:
            return {"error": str(exc)}
        return self._execute_command(
            tool_name="mark_commitment_done",
            command_type="obligations.complete",
            target_ref={"obligation_id": obligation_id},
            payload={"resolution_note": resolution_note},
            trace_data={"commitment": commitment},
        )

    def defer_commitment(self, *, commitment: str, minutes_from_now: int, reason: str) -> dict[str, Any]:
        try:
            obligation_id = self._resolve_commitment(commitment)
        except ReferenceResolutionError as exc:
            return {"error": str(exc)}
        return self._execute_command(
            tool_name="delay_commitment",
            command_type="obligations.defer",
            target_ref={"obligation_id": obligation_id},
            payload={"minutes_from_now": minutes_from_now, "reason": reason},
            trace_data={"commitment": commitment, "minutes_from_now": minutes_from_now, "reason": reason},
        )

    def schedule_self_wake(self, *, minutes_from_now: int, reason: str) -> dict[str, Any]:
        return self._execute_command(
            tool_name="set_reminder",
            command_type="system.schedule_self_wake",
            target_ref={"actor_id": self.actor.id},
            payload={"minutes_from_now": minutes_from_now, "reason": reason},
            trace_data={"minutes_from_now": minutes_from_now, "reason": reason},
        )

    def finish_assignment(
        self, *, summary: str, remaining_risks: list[str] | None = None,
        confidence: str | None = None,
    ) -> dict[str, Any]:
        return self._execute_command(
            tool_name="wrap_up_assignment",
            command_type="system.finish_assignment",
            target_ref={"actor_id": self.actor.id},
            payload={
                "summary": summary,
                "remaining_risks": list(remaining_risks or []),
                "confidence": confidence,
            },
            trace_data={"summary": summary, "remaining_risks": remaining_risks or []},
        )


def _serialize_object(record) -> dict[str, Any]:
    return {
        "id": record.id,
        "kind": record.kind,
        "title": record.title,
        "state": record.state_json,
        "visibility": record.visibility_json,
    }
