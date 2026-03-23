from __future__ import annotations

from typing import Any

from app.agents.base import BaseController, ControllerDecision
from app.agents.session_state import ActorTurnContext
from app.services import event_store
from app.services.db import session_scope


class ScriptedController(BaseController):
    async def decide(self, context: ActorTurnContext, toolkit: Any) -> ControllerDecision:
        script = context.profile.get("script")
        if script == "pm_smoke_test":
            return self._pm_smoke_test(context, toolkit)
        if script == "npc_engineer":
            return self._npc_engineer(context, toolkit)
        if script == "npc_designer":
            return self._npc_designer(context, toolkit)
        return ControllerDecision(
            decision_signal="deliberate_no_action",
            final_reasoning="No scripted behavior matched for this actor.",
        )

    def _get_past_events(self, context: ActorTurnContext) -> list[str]:
        """Return event types already emitted by this actor in this run."""
        with session_scope() as session:
            events = event_store.list_events(session, run_id=context.run_id, limit=500)
            return [
                e.event_type for e in events if e.actor_id == context.actor_id
            ]

    # ── smoke_test: PM (Riley) ────────────────────────────────────────────

    def _pm_smoke_test(self, context: ActorTurnContext, toolkit: Any) -> ControllerDecision:
        inbox = toolkit.get_my_inbox(limit=20)
        deliveries_to_mark: list[str] = []
        past = self._get_past_events(context)

        engineer_id = context.profile.get("engineer_actor_id", "actor_sam")
        designer_id = context.profile.get("designer_actor_id", "actor_pat")
        mockup_doc_id = context.profile.get("mockup_doc_id", "obj_doc_mockups")
        analytics_task_id = context.profile.get("analytics_task_id", "obj_task_analytics_spec")
        mockup_task_id = context.profile.get("mockup_task_id", "obj_task_mockup_review")

        # Step 1: Read mockup doc and send initial chats (once)
        if "ChatMessageSent" not in past:
            toolkit.list_visible_documents()
            toolkit.read_document(mockup_doc_id)
            toolkit.send_chat(
                coworker=engineer_id,
                message=(
                    "Hey Sam! I'll get the analytics spec to you today. "
                    "For the landing page, we need: page_view on load, "
                    "cta_click on the main button, and scroll_depth at 25/50/75/100%. "
                    "Let me know if you need anything else to start."
                ),
            )
            toolkit.send_chat(
                coworker=designer_id,
                message=(
                    "Hey Pat! Looking at your mockups now. I'll have feedback "
                    "for you this morning."
                ),
            )

        # Step 2: After designer replies, send email with detailed feedback (once)
        if "EmailSent" not in past:
            designer_msg = next(
                (
                    item for item in inbox
                    if item.get("surface") == "chat"
                    and item.get("event", {}).get("sender_actor_id") == designer_id
                ),
                None,
            )
            if designer_msg is not None:
                deliveries_to_mark.append(designer_msg["delivery_id"])
                toolkit.send_email(
                    coworker=designer_id,
                    subject="Landing page mockup feedback",
                    message=(
                        "Pat, mockups look great overall. Two notes:\n\n"
                        "1. Headline: Go with 'Ship faster with Beacon'. "
                        "It's more specific than 'Grow faster' and matches our positioning.\n"
                        "2. CTA: Keep it centered. The data Sam referenced was for a "
                        "different page layout — centered works better with the hero section.\n\n"
                        "Once you make those final, please hand off to Sam directly. Thanks!"
                    ),
                )
                toolkit.update_task_status(
                    task=mockup_task_id,
                    status="in_progress",
                )

        # Step 3: After engineer replies, update task/doc and schedule sync (once)
        if "TaskStatusUpdated" not in past or "MeetingScheduled" not in past:
            engineer_msg = next(
                (
                    item for item in inbox
                    if item.get("surface") == "chat"
                    and item.get("event", {}).get("sender_actor_id") == engineer_id
                ),
                None,
            )
            if engineer_msg is not None:
                deliveries_to_mark.append(engineer_msg["delivery_id"])
                if "TaskStatusUpdated" not in past:
                    toolkit.update_task_status(
                        task=analytics_task_id,
                        status="done",
                    )
                if "DocumentUpdated" not in past:
                    toolkit.update_document(
                        document=mockup_doc_id,
                        content=(
                            "\n\n--- PM Review (Riley) ---\n"
                            "Headline: approved 'Ship faster with Beacon'\n"
                            "CTA: keep centered\n"
                            "Analytics events: page_view, cta_click, scroll_depth\n"
                            "Status: ready for implementation handoff\n"
                        ),
                        append=True,
                    )
                if "MeetingScheduled" not in past:
                    toolkit.schedule_meeting(
                        title="Landing page handoff sync",
                        attendees=[engineer_id, designer_id],
                        starts_in_minutes=30,
                        duration_minutes=15,
                        agenda=(
                            "Quick sync: Pat hands off final mockups to Sam. "
                            "Confirm analytics spec. Align on timeline."
                        ),
                    )

        if deliveries_to_mark:
            toolkit.mark_inbox_items_read(sorted(set(deliveries_to_mark)))

        # Step 4: If a meeting is in progress, speak in it
        if "MeetingSpoken" not in past:
            meetings = toolkit.list_my_meetings()
            active_meeting = next(
                (m for m in meetings if (m.get("state") or {}).get("status") == "in_progress"),
                None,
            )
            if active_meeting:
                toolkit.speak_in_meeting(
                    meeting=active_meeting["id"],
                    message=(
                        "Quick recap: Sam has the analytics events spec — page_view, "
                        "cta_click, scroll_depth. Pat is finalizing mockups with the "
                        "approved hero copy and centered CTA. Pat will hand off final "
                        "assets to Sam directly. We are on track for today."
                    ),
                )

        has_actions = len(toolkit.executed_commands) > 0
        return ControllerDecision(
            decision_signal="commands_executed" if has_actions else "deliberate_no_action",
            final_reasoning="PM reviewed inbox, coordinated handoff between designer and engineer.",
        )

    # ── smoke_test: Engineer NPC (Sam) ────────────────────────────────────

    def _npc_engineer(self, context: ActorTurnContext, toolkit: Any) -> ControllerDecision:
        inbox = toolkit.get_my_inbox(limit=20)
        past = self._get_past_events(context)
        pm_id = context.profile.get("pm_actor_id", "actor_pm")

        # Step 1: Ask PM about analytics spec (once)
        if "ChatMessageSent" not in past:
            question = context.profile.get(
                "analytics_question",
                (
                    "Hey Riley, quick question about the landing page — the spec says "
                    "'add tracking' but I need the specific event names. Which analytics "
                    "events should I fire? page_view on load? cta_click on the button? "
                    "scroll_depth at thresholds? Need this before I can finalize."
                ),
            )
            toolkit.send_chat(coworker=pm_id, message=question)
            return ControllerDecision(
                decision_signal="commands_executed",
                final_reasoning="Engineer asked PM about analytics spec.",
            )

        # Step 2: If PM replied, acknowledge
        pm_reply = next(
            (
                item for item in inbox
                if item.get("surface") == "chat"
                and item.get("event", {}).get("sender_actor_id") == pm_id
            ),
            None,
        )
        if pm_reply is not None:
            thread_id = pm_reply.get("event", {}).get("thread_id")
            toolkit.mark_inbox_items_read([pm_reply["delivery_id"]])
            if thread_id:
                toolkit.reply_thread(
                    conversation=thread_id,
                    message=(
                        "Perfect, that's exactly what I needed. page_view, cta_click, "
                        "scroll_depth — got it. I'll wire those up. Thanks Riley!"
                    ),
                )
            return ControllerDecision(
                decision_signal="commands_executed",
                final_reasoning="Engineer acknowledged PM's analytics spec.",
            )

        return ControllerDecision(
            decision_signal="deliberate_no_action",
            final_reasoning="Engineer waiting for PM reply on analytics spec.",
        )

    # ── smoke_test: Designer NPC (Pat) ────────────────────────────────────

    def _npc_designer(self, context: ActorTurnContext, toolkit: Any) -> ControllerDecision:
        inbox = toolkit.get_my_inbox(limit=20)
        past = self._get_past_events(context)
        pm_id = context.profile.get("pm_actor_id", "actor_pm")

        # Step 1: Message PM about mockup review (once)
        if "ChatMessageSent" not in past:
            message = context.profile.get(
                "feedback_request",
                (
                    "Hey Riley! Mockups for the landing page redesign are ready for "
                    "review — I updated the doc (Landing Page Mockups v2). Specifically "
                    "need your take on: (1) the headline — 'Ship faster with Beacon' vs "
                    "'Grow faster with Beacon' that marketing suggested, and (2) CTA "
                    "placement — centered vs right-aligned? Let me know so I can finalize!"
                ),
            )
            toolkit.send_chat(coworker=pm_id, message=message)
            return ControllerDecision(
                decision_signal="commands_executed",
                final_reasoning="Designer requested mockup feedback from PM.",
            )

        # Step 2: If PM replied, acknowledge
        pm_reply = next(
            (
                item for item in inbox
                if item.get("surface") == "chat"
                and item.get("event", {}).get("sender_actor_id") == pm_id
            ),
            None,
        )
        if pm_reply is not None:
            thread_id = pm_reply.get("event", {}).get("thread_id")
            toolkit.mark_inbox_items_read([pm_reply["delivery_id"]])
            if thread_id:
                toolkit.reply_thread(
                    conversation=thread_id,
                    message=(
                        "Great, thanks for the quick review! I'll finalize with "
                        "'Ship faster' and centered CTA. Will ping Sam directly "
                        "once the final assets are ready."
                    ),
                )
            return ControllerDecision(
                decision_signal="commands_executed",
                final_reasoning="Designer acknowledged PM's mockup feedback.",
            )

        return ControllerDecision(
            decision_signal="deliberate_no_action",
            final_reasoning="Designer waiting for PM feedback on mockups.",
        )
