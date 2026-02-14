"""Tests for butlers.tools.switchboard.aggregate_responses — multi-butler reply aggregation."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from butlers.tools.switchboard import ButlerResult, aggregate_responses

pytestmark = pytest.mark.unit

# ------------------------------------------------------------------
# Data model
# ------------------------------------------------------------------


class TestButlerResult:
    """ButlerResult dataclass basics."""

    def test_success_result(self):
        r = ButlerResult(butler="health", response="You are fine", success=True)
        assert r.butler == "health"
        assert r.response == "You are fine"
        assert r.success is True
        assert r.error is None

    def test_failure_result(self):
        r = ButlerResult(butler="email", response=None, success=False, error="Timeout")
        assert r.butler == "email"
        assert r.response is None
        assert r.success is False
        assert r.error == "Timeout"


# ------------------------------------------------------------------
# Single response — returned as-is (no CC overhead)
# ------------------------------------------------------------------


class TestSingleResponse:
    """When only one butler responded, return it directly."""

    async def test_single_success_returned_as_is(self):
        results = [ButlerResult(butler="general", response="Hello there!", success=True)]

        async def should_not_be_called(**kwargs):
            raise AssertionError("dispatch_fn should not be called for single results")

        reply = await aggregate_responses(results, dispatch_fn=should_not_be_called)
        assert reply == "Hello there!"

    async def test_single_failure_returns_error_message(self):
        results = [
            ButlerResult(butler="health", response=None, success=False, error="DB down"),
        ]

        async def should_not_be_called(**kwargs):
            raise AssertionError("dispatch_fn should not be called for single results")

        reply = await aggregate_responses(results, dispatch_fn=should_not_be_called)
        low = reply.lower()
        assert "health" in low or "error" in low or "unavailable" in low


# ------------------------------------------------------------------
# Multiple responses — CC-based aggregation
# ------------------------------------------------------------------


class TestMultipleResponses:
    """When multiple butlers responded, spawn CC to synthesize."""

    async def test_multiple_success_spawns_cc(self):
        results = [
            ButlerResult(butler="health", response="Your BMI is 22.", success=True),
            ButlerResult(butler="general", response="You have a meeting at 3pm.", success=True),
        ]

        @dataclass
        class FakeResult:
            result: str = "Your BMI is 22 and you have a meeting at 3pm."

        dispatched_prompts: list[str] = []

        async def fake_dispatch(**kwargs):
            dispatched_prompts.append(kwargs.get("prompt", ""))
            return FakeResult()

        reply = await aggregate_responses(results, dispatch_fn=fake_dispatch)
        assert reply == "Your BMI is 22 and you have a meeting at 3pm."
        assert len(dispatched_prompts) == 1
        # The prompt should include both butler responses
        assert "BMI" in dispatched_prompts[0]
        assert "meeting" in dispatched_prompts[0]

    async def test_mixed_success_and_failure(self):
        results = [
            ButlerResult(butler="health", response="Blood pressure is normal.", success=True),
            ButlerResult(butler="email", response=None, success=False, error="Connection refused"),
            ButlerResult(butler="general", response="Weather is sunny.", success=True),
        ]

        @dataclass
        class FakeResult:
            result: str = (
                "Your blood pressure is normal and the weather is sunny. "
                "I was unable to check your email at this time."
            )

        dispatched_prompts: list[str] = []

        async def fake_dispatch(**kwargs):
            dispatched_prompts.append(kwargs.get("prompt", ""))
            return FakeResult()

        reply = await aggregate_responses(results, dispatch_fn=fake_dispatch)
        assert "blood pressure" in reply.lower()
        assert "email" in reply.lower() or "unable" in reply.lower()
        # Prompt should mention the failure
        prompt = dispatched_prompts[0]
        assert "email" in prompt.lower()
        assert "failed" in prompt.lower() or "error" in prompt.lower()

    async def test_all_failures(self):
        results = [
            ButlerResult(butler="health", response=None, success=False, error="Timeout"),
            ButlerResult(butler="email", response=None, success=False, error="Auth error"),
        ]

        @dataclass
        class FakeResult:
            result: str = (
                "I'm sorry, I wasn't able to get responses from health or email right now."
            )

        async def fake_dispatch(**kwargs):
            return FakeResult()

        reply = await aggregate_responses(results, dispatch_fn=fake_dispatch)
        # Should still produce a reply (CC synthesizes the error message)
        assert isinstance(reply, str)
        assert len(reply) > 0

    async def test_conflict_arbitration_prefers_higher_priority(self):
        results = [
            {
                "butler": "health",
                "result": "Book 9am slot",
                "error": None,
                "arbitration": {"group": "schedule", "priority": 1},
                "subrequest_id": "s1",
            },
            {
                "butler": "general",
                "result": "Book 11am slot",
                "error": None,
                "arbitration": {"group": "schedule", "priority": 10},
                "subrequest_id": "s2",
            },
        ]

        reply = aggregate_responses(results)
        assert reply == "Book 11am slot"


# ------------------------------------------------------------------
# Edge cases
# ------------------------------------------------------------------


class TestEdgeCases:
    """Edge cases and error handling."""

    async def test_empty_results_returns_fallback(self):
        async def should_not_be_called(**kwargs):
            raise AssertionError("dispatch_fn should not be called for empty results")

        reply = await aggregate_responses([], dispatch_fn=should_not_be_called)
        assert isinstance(reply, str)
        assert len(reply) > 0

    async def test_cc_spawner_failure_falls_back_to_concatenation(self):
        """If CC spawner fails, fall back to simple concatenation."""
        results = [
            ButlerResult(butler="health", response="BP is 120/80.", success=True),
            ButlerResult(butler="general", response="All tasks done.", success=True),
        ]

        async def broken_dispatch(**kwargs):
            raise RuntimeError("CC spawner crashed")

        reply = await aggregate_responses(results, dispatch_fn=broken_dispatch)
        # Should fall back to simple concatenation
        assert "120/80" in reply
        assert "tasks done" in reply

    async def test_cc_spawner_returns_none_falls_back(self):
        """If CC spawner returns None result, fall back to concatenation."""
        results = [
            ButlerResult(butler="health", response="BP is normal.", success=True),
            ButlerResult(butler="general", response="Calendar is clear.", success=True),
        ]

        @dataclass
        class EmptyResult:
            result: str | None = None

        async def empty_dispatch(**kwargs):
            return EmptyResult()

        reply = await aggregate_responses(results, dispatch_fn=empty_dispatch)
        # Should fall back to simple concatenation
        assert "normal" in reply.lower()
        assert "calendar" in reply.lower()

    async def test_dispatch_fn_receives_trigger_source(self):
        """dispatch_fn should be called with trigger_source='tick'."""
        results = [
            ButlerResult(butler="a", response="R1", success=True),
            ButlerResult(butler="b", response="R2", success=True),
        ]

        captured_kwargs: list[dict] = []

        @dataclass
        class FakeResult:
            result: str = "Combined."

        async def capturing_dispatch(**kwargs):
            captured_kwargs.append(kwargs)
            return FakeResult()

        await aggregate_responses(results, dispatch_fn=capturing_dispatch)
        assert len(captured_kwargs) == 1
        assert captured_kwargs[0].get("trigger_source") == "tick"

    async def test_partial_success_includes_actionable_error_class(self):
        results = [
            {"butler": "health", "result": "Vitals logged", "error": None},
            {
                "butler": "email",
                "result": None,
                "error": "ConnectionError: downstream unavailable",
                "error_class": "target_unavailable",
            },
        ]

        reply = aggregate_responses(results)
        assert "Vitals logged" in reply
        assert "target_unavailable" in reply
        assert "downstream unavailable" in reply
