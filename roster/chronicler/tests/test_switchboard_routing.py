"""Tests for Chronicler's switchboard routing boundary (RFC 0014 §D6)."""

from __future__ import annotations

import pytest

from roster.switchboard.tools.routing.classify import (
    _build_routing_guidance,
    _is_food_intent,
    _is_scheduling_intent,
    is_retrospective_time_intent,
)

# ── Retrospective intent detection ─────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "What did I do yesterday?",
        "what did I listen to last week",
        "when did I last go running",
        "How much time did I spend working today?",
        "time spent listening to music",
        "recap of last weekend",
        "Looking back at yesterday, what happened?",
        "fix the start time of yesterday's 3pm meeting",
        "actually that session ended at 4pm",
        "correct the title of yesterday's session",
    ],
)
def test_explicit_retrospective_matches(text: str) -> None:
    assert is_retrospective_time_intent(text), text


@pytest.mark.parametrize(
    "text",
    [
        # Domain next-action — NOT retrospective.
        "recommend me some jazz",
        "what music should I listen to",
        "schedule lunch with Alice",
        "set up a meeting for 3pm tomorrow",
        "what's the weather like",
        "I just had ramen at Ippudo",
        # Passive-sounding but not retrospective-shaped.
        "playing a new game",
        "I finished a run",
    ],
)
def test_non_retrospective_does_not_match(text: str) -> None:
    assert not is_retrospective_time_intent(text), text


def test_scheduling_intent_does_not_collide_with_retrospective() -> None:
    text = "schedule a meeting"
    assert _is_scheduling_intent(text)
    assert not is_retrospective_time_intent(text)


def test_food_intent_does_not_collide_with_retrospective() -> None:
    text = "I had lunch at Ippudo"
    assert _is_food_intent(text)
    assert not is_retrospective_time_intent(text)


# ── Routing guidance string ────────────────────────────────────────────────


def test_routing_guidance_mentions_chronicler_when_present() -> None:
    butlers = [
        {"name": "chronicler", "modules": []},
        {"name": "lifestyle", "modules": ["memory"]},
    ]
    guidance = _build_routing_guidance(butlers)
    assert "chronicler" in guidance.lower()
    assert "retrospective" in guidance.lower()
    # It must mention that passive events do NOT route to chronicler.
    assert "passive" in guidance.lower()


def test_routing_guidance_omits_chronicler_when_absent() -> None:
    butlers = [{"name": "lifestyle", "modules": []}]
    guidance = _build_routing_guidance(butlers)
    assert "chronicler" not in guidance.lower()


def test_routing_guidance_still_recognises_lifestyle_vs_health() -> None:
    butlers = [
        {"name": "chronicler", "modules": []},
        {"name": "lifestyle", "modules": ["memory"]},
        {"name": "health", "modules": ["memory"]},
    ]
    guidance = _build_routing_guidance(butlers)
    # Lifestyle / Health separation must survive the chronicler addition.
    assert "lifestyle" in guidance.lower()
    assert "nutrition" in guidance.lower()
