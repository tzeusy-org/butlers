"""Tests for spawner guardrail emission: degenerate_tool_loop, tool_call_budget_exceeded,
and token_budget_exceeded.

Covers bu-7y6cs:

1. Helper-unit tests for ``_check_degenerate_tool_loop``, ``_check_tool_call_budget``,
   and ``_check_token_budget``.
2. Integration tests proving that each guardrail, when triggered inside
   ``Spawner._run()``, raises a ``RuntimeError`` whose message contains the
   canonical guardrail marker string.
3. End-to-end classifier gate: the ``RuntimeError`` from the spawner is classified
   as a guardrail termination (``eligible=False``) by
   ``classify_failover_eligibility``.
4. Same-tier failover is NOT attempted when a guardrail fires.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from butlers.config import ButlerConfig, RuntimeSeedConfig
from butlers.core.failover_classifier import FailoverContext, classify_failover_eligibility
from butlers.core.runtimes.base import RuntimeAdapter
from butlers.core.spawner import (
    _DEGENERATE_TOOL_LOOP_CONSECUTIVE_THRESHOLD,
    Spawner,
    _check_degenerate_tool_loop,
    _check_token_budget,
    _check_tool_call_budget,
)
from butlers.core.spawner_guardrails import _check_undelivered_interactive_reply
from butlers.core.tool_call_capture import fingerprint_tool_call_payload
from butlers.routing_guidance import _INTERACTIVE_ROUTE_CHANNELS

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SESSION_ID = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001")
_CATALOG_ID = uuid.UUID("bbbbbbbb-0000-0000-0000-000000000002")


def _make_config(name: str = "test-butler") -> ButlerConfig:
    return ButlerConfig(
        name=name,
        port=9100,
        runtime_seed=RuntimeSeedConfig(max_concurrent_sessions=1),
        modules={},
        env_required=[],
        env_optional=[],
    )


_CALL_COUNTER = 0


def _tool_call(name: str = "get_data", input_payload: dict | None = None) -> dict[str, Any]:
    global _CALL_COUNTER
    _CALL_COUNTER += 1
    return {"id": f"call-{_CALL_COUNTER}", "name": name, "input": input_payload or {}}


def _repeated_calls(name: str, count: int) -> list[dict[str, Any]]:
    """Return ``count`` identical tool calls with the same name and input (but unique IDs)."""
    return [_tool_call(name) for _ in range(count)]


# ---------------------------------------------------------------------------
# _check_degenerate_tool_loop — unit tests
# ---------------------------------------------------------------------------


class TestCheckDegenerateToolLoop:
    """Unit tests for _check_degenerate_tool_loop."""

    def test_no_calls_returns_none(self) -> None:
        assert _check_degenerate_tool_loop([]) is None

    def test_single_call_returns_none(self) -> None:
        assert _check_degenerate_tool_loop([_tool_call()]) is None

    def test_below_threshold_returns_none(self) -> None:
        calls = _repeated_calls("fetch", _DEGENERATE_TOOL_LOOP_CONSECUTIVE_THRESHOLD - 1)
        assert _check_degenerate_tool_loop(calls) is None

    def test_at_threshold_triggers(self) -> None:
        calls = _repeated_calls("fetch", _DEGENERATE_TOOL_LOOP_CONSECUTIVE_THRESHOLD)
        result = _check_degenerate_tool_loop(calls)
        assert result is not None
        assert "degenerate_tool_loop" in result
        assert "fetch" in result

    def test_above_threshold_triggers(self) -> None:
        calls = _repeated_calls("read_file", _DEGENERATE_TOOL_LOOP_CONSECUTIVE_THRESHOLD + 3)
        result = _check_degenerate_tool_loop(calls)
        assert result is not None
        assert "degenerate_tool_loop" in result

    def test_non_consecutive_identical_does_not_trigger(self) -> None:
        """Alternating calls never build a consecutive streak."""
        threshold = _DEGENERATE_TOOL_LOOP_CONSECUTIVE_THRESHOLD
        calls = []
        for _ in range(threshold):
            calls.append(_tool_call("alpha"))
            calls.append(_tool_call("beta"))
        assert _check_degenerate_tool_loop(calls) is None

    def test_streak_reset_by_different_call(self) -> None:
        """A streak that is broken and never reaches threshold."""
        threshold = _DEGENERATE_TOOL_LOOP_CONSECUTIVE_THRESHOLD
        # threshold-1 identical, then a different call, then threshold-1 more identical
        calls = (
            _repeated_calls("alpha", threshold - 1)
            + [_tool_call("different")]
            + _repeated_calls("alpha", threshold - 1)
        )
        assert _check_degenerate_tool_loop(calls) is None

    def test_custom_threshold_respected(self) -> None:
        calls = _repeated_calls("foo", 3)
        assert _check_degenerate_tool_loop(calls, consecutive_threshold=3) is not None
        assert _check_degenerate_tool_loop(calls, consecutive_threshold=4) is None

    def test_zero_threshold_disables(self) -> None:
        """consecutive_threshold=0 disables the check."""
        calls = _repeated_calls("loop", 100)
        assert _check_degenerate_tool_loop(calls, consecutive_threshold=0) is None

    def test_different_inputs_are_distinct(self) -> None:
        """Same name but different inputs should not count as a loop."""
        calls = [
            {"id": "1", "name": "search", "input": {"query": "apples"}},
            {"id": "2", "name": "search", "input": {"query": "oranges"}},
            {"id": "3", "name": "search", "input": {"query": "bananas"}},
            {"id": "4", "name": "search", "input": {"query": "grapes"}},
            {"id": "5", "name": "search", "input": {"query": "pears"}},
            {"id": "6", "name": "search", "input": {"query": "mangoes"}},
        ]
        assert _check_degenerate_tool_loop(calls) is None

    def test_same_name_same_input_triggers(self) -> None:
        """Same name AND same input across the threshold fires the guardrail."""
        calls = [
            {"id": str(i), "name": "search", "input": {"query": "cats"}}
            for i in range(_DEGENERATE_TOOL_LOOP_CONSECUTIVE_THRESHOLD)
        ]
        result = _check_degenerate_tool_loop(calls)
        assert result is not None
        assert "degenerate_tool_loop" in result

    def test_input_fingerprint_distinguishes_hidden_inputs(self) -> None:
        """Captured calls with redacted visible input use fingerprints for loop detection."""
        calls = [
            {
                "id": str(i),
                "name": "contact_resolve",
                "input": {},
                "input_fingerprint": fingerprint_tool_call_payload({"name": f"Person {i}"}),
            }
            for i in range(_DEGENERATE_TOOL_LOOP_CONSECUTIVE_THRESHOLD)
        ]

        assert _check_degenerate_tool_loop(calls) is None

    def test_same_input_fingerprint_triggers(self) -> None:
        fingerprint = fingerprint_tool_call_payload({"name": "Repeated Person"})
        calls = [
            {
                "id": str(i),
                "name": "contact_resolve",
                "input": {},
                "input_fingerprint": fingerprint,
            }
            for i in range(_DEGENERATE_TOOL_LOOP_CONSECUTIVE_THRESHOLD)
        ]

        assert _check_degenerate_tool_loop(calls) is not None

    def test_loop_mid_session_reports_looping_tool_name(self) -> None:
        """When the loop starts mid-session, the reported tool name is the looping tool,
        not the first tool in the session (regression test for tool_calls[0] bug)."""
        threshold = _DEGENERATE_TOOL_LOOP_CONSECUTIVE_THRESHOLD
        # First call is a different tool — should not appear in the error message.
        first_call = {"id": "first", "name": "initial_tool", "input": {}}
        looping_calls = _repeated_calls("looping_tool", threshold)
        calls = [first_call] + looping_calls
        result = _check_degenerate_tool_loop(calls)
        assert result is not None
        assert "degenerate_tool_loop" in result
        assert "looping_tool" in result, (
            f"Expected 'looping_tool' (the looping call) in message, got: {result!r}"
        )
        assert "initial_tool" not in result, (
            f"Expected 'initial_tool' (the first call) NOT in message, got: {result!r}"
        )


# ---------------------------------------------------------------------------
# _check_tool_call_budget — unit tests
# ---------------------------------------------------------------------------


class TestCheckToolCallBudget:
    """Unit tests for _check_tool_call_budget."""

    def test_disabled_when_zero(self) -> None:
        assert _check_tool_call_budget(_repeated_calls("x", 1000), max_tool_calls=0) is None

    def test_not_exceeded_returns_none(self) -> None:
        calls = _repeated_calls("x", 5)
        assert _check_tool_call_budget(calls, max_tool_calls=10) is None

    def test_at_limit_returns_none(self) -> None:
        """Exactly at the limit is allowed."""
        calls = _repeated_calls("x", 5)
        assert _check_tool_call_budget(calls, max_tool_calls=5) is None

    def test_exceeded_returns_reason(self) -> None:
        calls = _repeated_calls("x", 11)
        result = _check_tool_call_budget(calls, max_tool_calls=10)
        assert result is not None
        assert "tool_call_budget_exceeded" in result
        assert "11" in result
        assert "10" in result

    def test_single_call_exceeds_budget_of_zero(self) -> None:
        """max_tool_calls=0 means disabled, not budget of zero."""
        calls = [_tool_call()]
        assert _check_tool_call_budget(calls, max_tool_calls=0) is None

    def test_negative_max_treated_as_disabled(self) -> None:
        calls = _repeated_calls("x", 100)
        assert _check_tool_call_budget(calls, max_tool_calls=-1) is None

    def test_empty_calls_not_exceeded(self) -> None:
        assert _check_tool_call_budget([], max_tool_calls=5) is None


# ---------------------------------------------------------------------------
# _check_token_budget — unit tests
# ---------------------------------------------------------------------------


class TestCheckTokenBudget:
    """Unit tests for _check_token_budget."""

    def test_none_budget_returns_none(self) -> None:
        assert _check_token_budget(50_000, max_token_budget=None) is None

    def test_none_tokens_returns_none(self) -> None:
        assert _check_token_budget(None, max_token_budget=100_000) is None

    def test_within_budget_returns_none(self) -> None:
        assert _check_token_budget(80_000, max_token_budget=100_000) is None

    def test_at_budget_returns_none(self) -> None:
        """Exactly at the budget is allowed."""
        assert _check_token_budget(100_000, max_token_budget=100_000) is None

    def test_exceeded_returns_reason(self) -> None:
        result = _check_token_budget(120_000, max_token_budget=100_000)
        assert result is not None
        assert "token_budget_exceeded" in result
        assert "120,000" in result
        assert "100,000" in result

    def test_both_none_returns_none(self) -> None:
        assert _check_token_budget(None, max_token_budget=None) is None

    def test_zero_tokens_within_budget(self) -> None:
        assert _check_token_budget(0, max_token_budget=1000) is None


# ---------------------------------------------------------------------------
# Classifier gate: guardrail markers in RuntimeError suppress failover
# ---------------------------------------------------------------------------


class TestGuardrailClassifierGate:
    """Verify the failover classifier treats guardrail markers as suppressed."""

    @pytest.mark.parametrize(
        "message",
        [
            "degenerate_tool_loop: 6 consecutive identical calls to 'fetch' detected",
            "tool_call_budget_exceeded: session made 51 tool calls, exceeding budget of 50",
            "token_budget_exceeded: session consumed 200,000 input tokens, exceeding budget of 100,000",
        ],
    )
    def test_guardrail_message_suppresses_failover(self, message: str) -> None:
        """Each guardrail marker causes the classifier to suppress failover."""
        exc = RuntimeError(message)
        ctx = FailoverContext(exception=exc, tool_calls=[])
        decision = classify_failover_eligibility(ctx)
        assert decision.eligible is False, (
            f"Expected guardrail to suppress failover for message={message!r}, "
            f"but got eligible=True, reason={decision.reason!r}"
        )
        assert "guardrail" in decision.reason

    def test_guardrail_with_tool_calls_also_suppressed(self) -> None:
        """Tool calls present AND guardrail marker — both independently suppress."""
        exc = RuntimeError("degenerate_tool_loop: repeated calls")
        tool_calls = [{"name": "fetch", "input": {}}]
        ctx = FailoverContext(exception=exc, tool_calls=tool_calls)
        decision = classify_failover_eligibility(ctx)
        assert decision.eligible is False


# ---------------------------------------------------------------------------
# Integration: spawner emits guardrails during a session
# ---------------------------------------------------------------------------


class _SuccessAdapter(RuntimeAdapter):
    """Adapter that returns a pre-configured result."""

    def __init__(
        self,
        result_text: str = "done",
        tool_calls: list[dict[str, Any]] | None = None,
        usage: dict[str, Any] | None = None,
    ) -> None:
        self._result_text = result_text
        self._tool_calls = tool_calls or []
        self._usage = usage

    @property
    def binary_name(self) -> str:
        return "mock"

    async def invoke(
        self,
        prompt: str,
        system_prompt: str,
        mcp_servers: dict[str, Any],
        env: dict[str, str],
        max_turns: int = 20,
        model: str | None = None,
        runtime_args: list[str] | None = None,
        cwd: Path | None = None,
        timeout: int | None = None,
    ) -> tuple[str | None, list[dict[str, Any]], dict[str, Any] | None]:
        return self._result_text, self._tool_calls, self._usage

    def build_config_file(self, mcp_servers: dict[str, Any], tmp_dir: Path) -> Path:
        p = tmp_dir / "config.json"
        p.write_text("{}")
        return p

    def parse_system_prompt_file(self, config_dir: Path) -> str:
        return ""


def _catalog_result(
    model: str = "test-model",
) -> tuple[str, str, list, uuid.UUID, int, str]:
    return ("codex", model, [], _CATALOG_ID, 300, "workhorse")


def _make_spawner(
    adapter: RuntimeAdapter,
    tmp_path: Path,
) -> tuple[Spawner, AsyncMock]:
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    mock_pool = AsyncMock()
    spawner = Spawner(
        config=_make_config(),
        config_dir=config_dir,
        pool=mock_pool,
        runtime=adapter,
    )
    return spawner, mock_pool


class TestSpawnerGuardrailEmission:
    """Integration: spawner emits the correct RuntimeError for each guardrail condition."""

    async def test_degenerate_tool_loop_raises(self, tmp_path: Path) -> None:
        """Spawner raises RuntimeError with 'degenerate_tool_loop' when loop detected."""
        threshold = _DEGENERATE_TOOL_LOOP_CONSECUTIVE_THRESHOLD
        looping_calls = _repeated_calls("fetch_entity", threshold)
        adapter = _SuccessAdapter(tool_calls=looping_calls)
        spawner, _mock_pool = _make_spawner(adapter, tmp_path)

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_sc,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=_catalog_result(),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=type(
                    "Q",
                    (),
                    {
                        "allowed": True,
                        "usage_24h": 0,
                        "limit_24h": None,
                        "usage_30d": 0,
                        "limit_30d": None,
                    },
                )(),
            ),
            patch(
                "butlers.core.spawner.next_same_tier_candidate",
                new_callable=AsyncMock,
            ) as mock_next,
        ):
            mock_sc.return_value = _SESSION_ID
            result = await spawner.trigger("do a thing", "tick")

        assert result.success is False
        assert "degenerate_tool_loop" in (result.error or ""), (
            f"Expected 'degenerate_tool_loop' in error, got: {result.error!r}"
        )
        # Failover must NOT have been attempted.
        mock_next.assert_not_called()

    async def test_tool_call_budget_exceeded_raises(self, tmp_path: Path) -> None:
        """Spawner raises RuntimeError with 'tool_call_budget_exceeded' when budget exceeded."""
        budget = 3
        over_budget_calls = _repeated_calls("different_tool", budget + 1)
        # Make each call have a unique input so it doesn't trigger the loop guardrail.
        for i, call in enumerate(over_budget_calls):
            call["input"] = {"seq": i}
        adapter = _SuccessAdapter(tool_calls=over_budget_calls)
        spawner, _mock_pool = _make_spawner(adapter, tmp_path)

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_sc,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=_catalog_result(),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=type(
                    "Q",
                    (),
                    {
                        "allowed": True,
                        "usage_24h": 0,
                        "limit_24h": None,
                        "usage_30d": 0,
                        "limit_30d": None,
                    },
                )(),
            ),
            patch(
                "butlers.core.spawner.next_same_tier_candidate",
                new_callable=AsyncMock,
            ) as mock_next,
        ):
            mock_sc.return_value = _SESSION_ID
            result = await spawner.trigger("do a thing", "tick", max_tool_calls=budget)

        assert result.success is False
        assert "tool_call_budget_exceeded" in (result.error or ""), (
            f"Expected 'tool_call_budget_exceeded' in error, got: {result.error!r}"
        )
        mock_next.assert_not_called()

    async def test_token_budget_exceeded_raises(self, tmp_path: Path) -> None:
        """Spawner raises RuntimeError with 'token_budget_exceeded' when token budget exceeded."""
        budget = 50_000
        adapter = _SuccessAdapter(
            tool_calls=[],
            usage={"input_tokens": budget + 10_000, "output_tokens": 1000},
        )
        spawner, _mock_pool = _make_spawner(adapter, tmp_path)

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_sc,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=_catalog_result(),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=type(
                    "Q",
                    (),
                    {
                        "allowed": True,
                        "usage_24h": 0,
                        "limit_24h": None,
                        "usage_30d": 0,
                        "limit_30d": None,
                    },
                )(),
            ),
            patch(
                "butlers.core.spawner.next_same_tier_candidate",
                new_callable=AsyncMock,
            ) as mock_next,
        ):
            mock_sc.return_value = _SESSION_ID
            result = await spawner.trigger("do a thing", "tick", max_token_budget=budget)

        assert result.success is False
        assert "token_budget_exceeded" in (result.error or ""), (
            f"Expected 'token_budget_exceeded' in error, got: {result.error!r}"
        )
        mock_next.assert_not_called()

    async def test_no_guardrail_below_thresholds(self, tmp_path: Path) -> None:
        """Session with tool calls below all budgets completes successfully."""
        budget = 10
        calls = [{"id": str(i), "name": "safe_tool", "input": {"n": i}} for i in range(5)]
        adapter = _SuccessAdapter(
            tool_calls=calls,
            usage={"input_tokens": 1000, "output_tokens": 100},
        )
        spawner, _mock_pool = _make_spawner(adapter, tmp_path)

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_sc,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=_catalog_result(),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=type(
                    "Q",
                    (),
                    {
                        "allowed": True,
                        "usage_24h": 0,
                        "limit_24h": None,
                        "usage_30d": 0,
                        "limit_30d": None,
                    },
                )(),
            ),
        ):
            mock_sc.return_value = _SESSION_ID
            result = await spawner.trigger(
                "do a thing",
                "tick",
                max_tool_calls=budget,
                max_token_budget=100_000,
            )

        assert result.success is True
        assert result.error is None

    async def test_guardrail_suppresses_failover_end_to_end(self, tmp_path: Path) -> None:
        """End-to-end: degenerate loop guardrail fires → classifier suppresses failover."""
        from butlers.core.model_routing import QuotaStatus

        threshold = _DEGENERATE_TOOL_LOOP_CONSECUTIVE_THRESHOLD
        looping_calls = _repeated_calls("loop_tool", threshold)
        adapter = _SuccessAdapter(tool_calls=looping_calls)
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        mock_pool = AsyncMock()
        spawner = Spawner(
            config=_make_config(),
            config_dir=config_dir,
            pool=mock_pool,
            runtime=adapter,
        )

        _quota_ok = QuotaStatus(
            allowed=True, usage_24h=0, limit_24h=None, usage_30d=0, limit_30d=None
        )

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_sc,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=_catalog_result(),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_quota_ok,
            ),
            patch(
                "butlers.core.spawner.next_same_tier_candidate",
                new_callable=AsyncMock,
            ) as mock_next,
        ):
            mock_sc.return_value = _SESSION_ID
            with patch.object(spawner._metrics, "record_failover_suppressed") as mock_suppressed:
                result = await spawner.trigger("do a thing", "tick")

        assert result.success is False
        assert "degenerate_tool_loop" in (result.error or "")
        # Failover NOT attempted.
        mock_next.assert_not_called()
        # Suppressed metric emitted — the classifier correctly suppressed failover.
        # For guardrail conditions where tool calls were executed, GATE 1 (captured tool
        # calls suppress failover) fires before GATE 2 (guardrail marker check). Either
        # gate is acceptable: the invariant is that failover is NOT attempted.
        mock_suppressed.assert_called_once()
        reason = mock_suppressed.call_args[1].get("reason", "")
        assert reason, f"Expected a non-empty suppressed reason, got: {reason!r}"


# ---------------------------------------------------------------------------
# _check_undelivered_interactive_reply (helper-unit)
# ---------------------------------------------------------------------------


def _telegram_routing_ctx() -> dict[str, Any]:
    return {
        "request_context": {
            "request_id": "019ef580-de73-7ae4-8c14-c3ccc8a9bf21",
            "source_channel": "telegram_bot",
            "source_endpoint_identity": "switchboard",
            "source_sender_identity": "206570151",
            "source_thread_identity": "206570151:1311",
        }
    }


def _undelivered(tool_calls: list[dict[str, Any]], **over: Any) -> str | None:
    kwargs: dict[str, Any] = {
        "routing_context": _telegram_routing_ctx(),
        "trigger_source": "route",
        "interactive_channels": _INTERACTIVE_ROUTE_CHANNELS,
    }
    kwargs.update(over)
    return _check_undelivered_interactive_reply(tool_calls, **kwargs)


class TestUndeliveredInteractiveReply:
    """Helper-unit coverage for the undelivered-interactive-reply guardrail."""

    def test_attempted_but_all_failed_is_flagged(self) -> None:
        # The incident shape: notify attempted, validation rejected at the
        # boundary so the captured result is null/absent.
        calls = [
            {"name": "calendar_list_events", "result": {"events": []}},
            {"name": "notify", "result": None},
            {"name": "notify", "result": None},
        ]
        reason = _undelivered(calls)
        assert reason is not None
        assert "undelivered_interactive_reply" in reason
        assert "2 notify attempt" in reason

    def test_notify_status_error_is_flagged(self) -> None:
        calls = [{"name": "notify", "result": {"status": "error", "error": "boom"}}]
        assert _undelivered(calls) is not None

    def test_notify_outcome_error_is_flagged(self) -> None:
        calls = [{"name": "notify", "outcome": "error", "result": None}]
        assert _undelivered(calls) is not None

    def test_delivered_ok_is_not_flagged(self) -> None:
        calls = [{"name": "notify", "result": {"status": "ok"}}]
        assert _undelivered(calls) is None

    def test_deferred_counts_as_delivered(self) -> None:
        # Quiet-hours hold is a successful delivery decision, not a failure.
        calls = [{"name": "notify", "result": {"status": "deferred", "notification_id": "n1"}}]
        assert _undelivered(calls) is None

    def test_mixed_attempts_with_one_success_is_not_flagged(self) -> None:
        calls = [
            {"name": "notify", "result": {"status": "error"}},
            {"name": "notify", "result": {"status": "ok"}},
        ]
        assert _undelivered(calls) is None

    def test_no_notify_attempt_is_not_flagged(self) -> None:
        # Conservative: the runtime may have legitimately decided not to reply.
        calls = [{"name": "calendar_list_events", "result": {"events": []}}]
        assert _undelivered(calls) is None

    def test_prefixed_notify_name_is_detected(self) -> None:
        # Unmerged parser-side records may carry a prefixed tool name.
        calls = [{"name": "mcp__health.notify", "result": None}]
        assert _undelivered(calls) is not None

    def test_non_route_trigger_is_ignored(self) -> None:
        calls = [{"name": "notify", "result": None}]
        assert _undelivered(calls, trigger_source="tick") is None

    def test_non_interactive_channel_is_ignored(self) -> None:
        calls = [{"name": "notify", "result": None}]
        ctx = {"request_context": {"source_channel": "email"}}
        assert _undelivered(calls, routing_context=ctx) is None

    def test_missing_routing_context_is_ignored(self) -> None:
        calls = [{"name": "notify", "result": None}]
        assert _undelivered(calls, routing_context=None) is None

    def test_source_channel_from_source_metadata_fallback(self) -> None:
        calls = [{"name": "notify", "result": None}]
        ctx = {"source_metadata": {"channel": "telegram_bot"}}
        assert _undelivered(calls, routing_context=ctx) is not None


class TestSpawnerUndeliveredReplyAccounting:
    """Integration: an undelivered interactive reply is recorded as a failed session.

    The runtime returns cleanly (no exception), so failover/self-healing are not
    triggered, but the session row is persisted with success=False and a reason.
    """

    async def test_route_reply_with_failed_notify_marks_session_failed(
        self, tmp_path: Path
    ) -> None:
        # One notify attempt whose result is null (boundary rejection shape).
        adapter = _SuccessAdapter(
            result_text="I replied (allegedly).",
            tool_calls=[{"name": "notify", "input": {"intent": "reply"}, "result": None}],
        )
        spawner, _mock_pool = _make_spawner(adapter, tmp_path)

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_sc,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock) as mock_complete,
            patch(
                "butlers.core.spawner._capture_pipeline_routing_context",
                return_value=_telegram_routing_ctx(),
            ),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=_catalog_result(),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=type(
                    "Q",
                    (),
                    {
                        "allowed": True,
                        "usage_24h": 0,
                        "limit_24h": None,
                        "usage_30d": 0,
                        "limit_30d": None,
                    },
                )(),
            ),
        ):
            mock_sc.return_value = _SESSION_ID
            result = await spawner.trigger("Did I miss the Dr Ng followup?", "route")

        # The runtime invocation itself succeeded (memory/route flow unaffected).
        assert result.success is True
        # But the session row is recorded as a failed delivery.
        mock_complete.assert_awaited_once()
        kwargs = mock_complete.await_args.kwargs
        assert kwargs["success"] is False
        assert "undelivered_interactive_reply" in (kwargs["error"] or "")

    async def test_route_reply_with_delivered_notify_marks_session_success(
        self, tmp_path: Path
    ) -> None:
        adapter = _SuccessAdapter(
            result_text="Replied.",
            tool_calls=[
                {"name": "notify", "input": {"intent": "reply"}, "result": {"status": "ok"}}
            ],
        )
        spawner, _mock_pool = _make_spawner(adapter, tmp_path)

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_sc,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock) as mock_complete,
            patch(
                "butlers.core.spawner._capture_pipeline_routing_context",
                return_value=_telegram_routing_ctx(),
            ),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=_catalog_result(),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=type(
                    "Q",
                    (),
                    {
                        "allowed": True,
                        "usage_24h": 0,
                        "limit_24h": None,
                        "usage_30d": 0,
                        "limit_30d": None,
                    },
                )(),
            ),
        ):
            mock_sc.return_value = _SESSION_ID
            result = await spawner.trigger("Did I miss the Dr Ng followup?", "route")

        assert result.success is True
        mock_complete.assert_awaited_once()
        kwargs = mock_complete.await_args.kwargs
        assert kwargs["success"] is True
        assert kwargs["error"] is None
