from __future__ import annotations

from datetime import UTC, datetime

from app.agents.claude_controller import (
    _build_system_prompt,
    _build_turn_prompt,
    _describe_work_affordances,
    _render_observations,
)
from app.agents.session_state import ActorTurnContext


def _make_context(**overrides) -> ActorTurnContext:
    defaults = dict(
        run_id="run_1",
        current_sim_time=datetime(2026, 3, 20, 9, 0, tzinfo=UTC),
        local_current_time=datetime(2026, 3, 20, 9, 0, tzinfo=UTC).isoformat(),
        actor_id="actor_pm",
        actor_name="Burhan PM",
        actor_role="pm",
        actor_team="product",
        profile={},
        permissions={"can_chat": True},
        observations=[],
        current_commitments=[],
        visible_objects=[],
    )
    defaults.update(overrides)
    return ActorTurnContext(**defaults)


def test_system_prompt_stays_in_world():
    context = _make_context()
    prompt = _build_system_prompt(context).lower()
    assert "simulation" not in prompt
    assert "controller" not in prompt
    assert "npc" not in prompt


def test_first_turn_prompt_includes_full_context():
    context = _make_context(
        observations=[
            {
                "kind": "inbound_message",
                "surface": "chat",
                "at": "2026-03-20T09:00:00+00:00",
                "headline": "Casey sent you a chat.",
                "summary": "Need an ETA on billing migration.",
            }
        ],
        actor_directory={"actor_casey": "Casey"},
    )
    prompt = _build_turn_prompt(context, is_first_turn=True)
    assert "You are Burhan PM" in prompt
    assert "Casey sent you a chat" in prompt
    assert "It is 2026-03-20" in prompt


def test_subsequent_turn_prompt_is_concise():
    context = _make_context(
        observations=[
            {
                "kind": "inbound_message",
                "surface": "chat",
                "at": "2026-03-20T09:05:00+00:00",
                "headline": "Casey replied.",
                "summary": "Schema decision blocked.",
            }
        ],
    )
    prompt = _build_turn_prompt(context, is_first_turn=False)
    assert "It is now" in prompt
    assert "Casey replied" in prompt
    assert "You are Burhan PM" not in prompt


def test_work_affordances_respect_permissions():
    assert "send and reply to chat" in _describe_work_affordances({"can_chat": True})
    assert "send and reply to chat" not in _describe_work_affordances({})
    assert "finish the assignment" in _describe_work_affordances({"can_finish_assignment": True})
    assert "finish the assignment" not in _describe_work_affordances({})


def test_observations_render_correctly():
    obs = [
        {"headline": "Casey sent you a chat.", "summary": "ETA on billing?", "at": "09:00"},
        {"headline": "System update.", "summary": "", "at": "09:01"},
    ]
    rendered = _render_observations(obs)
    assert "Casey sent you a chat" in rendered
    assert "ETA on billing" in rendered
    assert "System update" in rendered


def test_empty_observations_render_nothing_new():
    rendered = _render_observations([])
    assert "nothing new" in rendered
