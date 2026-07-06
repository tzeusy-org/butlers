"""Tests for discretion IGNORE outcome discrimination (bu-n0336).

From bu-ofo3i's diagnosis (weight<0.5 senders silently fail-closed on a
never-provisioned Codex CLI auth token) and bu-cicgb (the WhatsApp 90%-drop
audit): ``connectors.filtered_events`` rows for discretion IGNORE outcomes all
carried the same bare ``"discretion:IGNORE"`` filter_reason, so a genuine
LLM-judged IGNORE was indistinguishable from a fail-closed default caused by
an auth failure, a timeout, an unparseable response, or same-tier failover
exhaustion — an audit could not tell "the LLM judged this noise" apart from
"the classifier silently dropped it" without re-sampling raw payloads.

Covers:
- ``classify_ignore_kind()`` maps every reason shape ``DiscretionEvaluator``
  produces to a stable, distinct kind string.
- End-to-end: ``DiscretionEvaluator.evaluate()`` under weight<0.5 (fail-closed)
  produces a reason that ``classify_ignore_kind()`` correctly attributes to
  auth_failure_default / failover_exhausted / timeout_default /
  parse_error_default / error_default, and a genuine LLM IGNORE verdict maps
  to llm_verdict.
- ``FilteredEventBuffer.reason_discretion_ignore()`` composes the persisted
  string as ``discretion:ignore:<kind>``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from butlers.connectors.discretion import (
    DiscretionEvaluator,
    DiscretionResult,
    _classify_default_error,
    classify_ignore_kind,
)
from butlers.connectors.filtered_event_buffer import FilteredEventBuffer

pytestmark = pytest.mark.unit


def _make_dispatcher(*, response: str | None = None, side_effect: Exception | None = None):
    dispatcher = AsyncMock()
    if side_effect is not None:
        dispatcher.call = AsyncMock(side_effect=side_effect)
    else:
        dispatcher.call = AsyncMock(return_value=response)
    return dispatcher


# ---------------------------------------------------------------------------
# classify_ignore_kind — direct reason-string mapping
# ---------------------------------------------------------------------------


def test_llm_verdict_ignore_has_empty_reason() -> None:
    """A genuine LLM-judged IGNORE carries an empty reason (per _parse_verdict)."""
    result = DiscretionResult(verdict="IGNORE", reason="", is_fail_open=False)
    assert classify_ignore_kind(result) == "llm_verdict"


def test_fail_closed_auth_failure_reason() -> None:
    result = DiscretionResult(
        verdict="IGNORE", reason="fail-closed: auth_failure", is_fail_open=False
    )
    assert classify_ignore_kind(result) == "auth_failure_default"


def test_fail_closed_provider_unavailable_reason() -> None:
    """bu-ujm9d: a provider/backend availability failure (e.g. connection
    refused, service unavailable) must classify to its own kind — not
    ``auth_failure_default`` — since it is not an identity/credential
    rejection."""
    result = DiscretionResult(
        verdict="IGNORE", reason="fail-closed: provider_unavailable", is_fail_open=False
    )
    assert classify_ignore_kind(result) == "provider_unavailable_default"


def test_fail_closed_failover_exhausted_reason() -> None:
    result = DiscretionResult(
        verdict="IGNORE", reason="fail-closed: failover_exhausted", is_fail_open=False
    )
    assert classify_ignore_kind(result) == "failover_exhausted"


def test_fail_closed_timeout_reason() -> None:
    result = DiscretionResult(verdict="IGNORE", reason="fail-closed: timeout", is_fail_open=False)
    assert classify_ignore_kind(result) == "timeout_default"


def test_fail_closed_parse_error_reason() -> None:
    result = DiscretionResult(
        verdict="IGNORE", reason="fail-closed: parse_error", is_fail_open=False
    )
    assert classify_ignore_kind(result) == "parse_error_default"


def test_fail_closed_other_exception_reason_is_error_default() -> None:
    result = DiscretionResult(
        verdict="IGNORE", reason="fail-closed: ValueError", is_fail_open=False
    )
    assert classify_ignore_kind(result) == "error_default"


# ---------------------------------------------------------------------------
# _classify_default_error — direct marker-split coverage (bu-ujm9d)
# ---------------------------------------------------------------------------


def test_classify_default_error_connection_refused_is_provider_unavailable() -> None:
    """Proven repro from PR #3004 review: a connection-refused RuntimeError
    used to classify as ``"auth_failure"`` before the marker split — it must
    now classify as ``"provider_unavailable"``."""
    exc = RuntimeError("Connection refused: could not reach provider")
    assert _classify_default_error(exc) == "provider_unavailable"


def test_classify_default_error_genuine_auth_is_still_auth_failure() -> None:
    """A genuine identity/credential failure is unaffected by the split."""
    exc = RuntimeError("Codex CLI exited with code 1: unexpected status 401 Unauthorized")
    assert _classify_default_error(exc) == "auth_failure"


# ---------------------------------------------------------------------------
# End-to-end through DiscretionEvaluator.evaluate()
# ---------------------------------------------------------------------------


async def test_evaluate_llm_verdict_ignore_classifies_as_llm_verdict() -> None:
    """The LLM actually ran and returned IGNORE — not a default."""
    dispatcher = _make_dispatcher(response="IGNORE")
    evaluator = DiscretionEvaluator(source_name="tg", dispatcher=dispatcher)

    result = await evaluator.evaluate(text="ambient chatter", weight=0.7)

    assert result.verdict == "IGNORE"
    assert classify_ignore_kind(result) == "llm_verdict"


async def test_evaluate_auth_failure_classifies_as_auth_failure_default() -> None:
    """A provider/auth-classified exception (e.g. a missing/revoked CLI token,
    the bu-ofo3i scenario) under weight<0.5 fail-closed must be distinguishable
    from a genuine LLM IGNORE."""
    dispatcher = _make_dispatcher(
        side_effect=RuntimeError("Codex CLI exited with code 1: unexpected status 401 Unauthorized")
    )
    evaluator = DiscretionEvaluator(source_name="tg", dispatcher=dispatcher)

    result = await evaluator.evaluate(text="spam", weight=0.1)

    assert result.verdict == "IGNORE"
    assert result.is_fail_open is False
    assert classify_ignore_kind(result) == "auth_failure_default"


async def test_evaluate_connection_refused_classifies_as_provider_unavailable_default() -> None:
    """bu-ujm9d: a connectivity failure under weight<0.5 fail-closed must be
    distinguishable from a genuine auth failure — proven repro from PR #3004
    review, previously misclassified as ``auth_failure_default``."""
    dispatcher = _make_dispatcher(
        side_effect=RuntimeError("Connection refused: could not reach provider")
    )
    evaluator = DiscretionEvaluator(source_name="tg", dispatcher=dispatcher)

    result = await evaluator.evaluate(text="spam", weight=0.1)

    assert result.verdict == "IGNORE"
    assert result.is_fail_open is False
    assert classify_ignore_kind(result) == "provider_unavailable_default"


async def test_evaluate_failover_exhausted_classifies_as_failover_exhausted() -> None:
    """DiscretionDispatcher's own same-tier failover exhaustion RuntimeError
    (bu-8fves) must not collapse into the generic error_default bucket."""
    dispatcher = _make_dispatcher(
        side_effect=RuntimeError(
            "same_tier_failover_exhausted: tier=specialty after 5 attempt(s); "
            "last error: TimeoutError: "
        )
    )
    evaluator = DiscretionEvaluator(source_name="tg", dispatcher=dispatcher)

    result = await evaluator.evaluate(text="spam", weight=0.1)

    assert result.verdict == "IGNORE"
    assert classify_ignore_kind(result) == "failover_exhausted"


async def test_evaluate_timeout_classifies_as_timeout_default() -> None:
    dispatcher = _make_dispatcher(side_effect=TimeoutError())
    evaluator = DiscretionEvaluator(source_name="tg", dispatcher=dispatcher)

    result = await evaluator.evaluate(text="spam", weight=0.1)

    assert result.verdict == "IGNORE"
    assert classify_ignore_kind(result) == "timeout_default"


async def test_evaluate_parse_error_classifies_as_parse_error_default() -> None:
    dispatcher = _make_dispatcher(response="not a recognized verdict")
    evaluator = DiscretionEvaluator(source_name="tg", dispatcher=dispatcher)

    result = await evaluator.evaluate(text="spam", weight=0.1)

    assert result.verdict == "IGNORE"
    assert classify_ignore_kind(result) == "parse_error_default"


async def test_evaluate_generic_error_classifies_as_error_default() -> None:
    """An exception that is neither a recognized auth/provider failure nor a
    same-tier-failover-exhaustion marker falls back to the generic bucket."""
    dispatcher = _make_dispatcher(side_effect=ValueError("unexpected business error"))
    evaluator = DiscretionEvaluator(source_name="tg", dispatcher=dispatcher)

    result = await evaluator.evaluate(text="spam", weight=0.1)

    assert result.verdict == "IGNORE"
    assert classify_ignore_kind(result) == "error_default"


# ---------------------------------------------------------------------------
# FilteredEventBuffer.reason_discretion_ignore — composition
# ---------------------------------------------------------------------------


def test_reason_discretion_ignore_format() -> None:
    assert (
        FilteredEventBuffer.reason_discretion_ignore("auth_failure_default")
        == "discretion:ignore:auth_failure_default"
    )
    assert (
        FilteredEventBuffer.reason_discretion_ignore("llm_verdict")
        == "discretion:ignore:llm_verdict"
    )
