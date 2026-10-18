"""DiscretionEvaluator logs carry identifiers, never message content (bu-1wwjrb).

A sentinel string is pushed through the message text, the LLM response and the exception
message on every evaluator path; no log record at any level may contain it, and no record
may carry traceback text (``exc_info``).
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock

import pytest

import butlers.connectors.discretion as discretion_mod
from butlers.connectors.discretion import ContactWeightResolver, DiscretionEvaluator

pytestmark = pytest.mark.unit

SENTINEL = "SENTINEL-4f9c-private-message-body"
FAILOVER = (
    "same_tier_failover_exhausted: tier=specialty after 5 attempt(s); "
    f"last error: RuntimeError: {SENTINEL}"
)


def _evaluator(*, response: str | None = None, error: BaseException | None = None, **kwargs):
    dispatcher = AsyncMock()
    dispatcher.pool = None
    if error is not None:
        dispatcher.call = AsyncMock(side_effect=error)
    else:
        dispatcher.call = AsyncMock(return_value=response)
    return DiscretionEvaluator(source_name="tg:42", dispatcher=dispatcher, **kwargs)


def _assert_clean(caplog: pytest.LogCaptureFixture) -> None:
    assert caplog.records, "the path under test must log something"
    for record in caplog.records:
        assert SENTINEL not in record.getMessage()
        assert SENTINEL not in str(record.args)
        assert record.exc_info is None
    assert SENTINEL not in caplog.text


@pytest.mark.parametrize(
    "response",
    [
        f'{{"verdict": "FORWARD", "reason": "{SENTINEL}"}}',
        f"IGNORE {SENTINEL}",
        f"no verdict here {SENTINEL}",
    ],
    ids=["json-verdict", "text-verdict", "unparseable"],
)
async def test_input_and_result_logs_carry_no_content(caplog, response: str) -> None:
    with caplog.at_level(logging.DEBUG):
        await _evaluator(response=response).evaluate(text=SENTINEL, weight=0.3)
    _assert_clean(caplog)
    assert "prompt_sha=" in caplog.text


async def test_result_log_has_length_hash_and_latency(caplog) -> None:
    with caplog.at_level(logging.INFO):
        await _evaluator(response="FORWARD").evaluate(text=SENTINEL, weight=0.3)
    assert "response_chars=7" in caplog.text
    assert "response_sha=" in caplog.text
    assert "latency_ms=" in caplog.text


async def test_timeout_log_carries_no_content(caplog) -> None:
    with caplog.at_level(logging.DEBUG):
        result = await _evaluator(error=TimeoutError(SENTINEL)).evaluate(text=SENTINEL, weight=0.3)
    assert result.verdict == "IGNORE"
    _assert_clean(caplog)
    assert "timed out" in caplog.text


async def test_error_log_has_class_and_failover_token_only(caplog) -> None:
    with caplog.at_level(logging.DEBUG):
        result = await _evaluator(error=RuntimeError(FAILOVER)).evaluate(text=SENTINEL, weight=0.3)
    assert result.reason == "fail-closed: failover_exhausted"
    _assert_clean(caplog)
    assert "error_class=RuntimeError error_code=same_tier_failover_exhausted" in caplog.text
    assert "tier=specialty" not in caplog.text
    assert "attempt(s)" not in caplog.text


async def test_unclassified_error_logs_class_name_without_code(caplog) -> None:
    with caplog.at_level(logging.DEBUG):
        await _evaluator(error=ValueError(SENTINEL)).evaluate(text=SENTINEL, weight=0.3)
    _assert_clean(caplog)
    assert "error_class=ValueError" in caplog.text
    assert "error_code=" not in caplog.text


async def test_error_log_has_no_content_even_when_exception_wraps_the_prompt(caplog) -> None:
    async def boom(prompt, **_kwargs):
        raise RuntimeError(f"provider echoed: {prompt}")

    evaluator = _evaluator(response="FORWARD")
    evaluator._dispatcher.call = boom
    with caplog.at_level(logging.DEBUG):
        await evaluator.evaluate(text=SENTINEL, weight=0.3)
    _assert_clean(caplog)


async def test_ledger_write_failure_log_carries_no_exception_text(monkeypatch, caplog) -> None:
    monkeypatch.setattr(
        discretion_mod,
        "record_attention_event",
        AsyncMock(side_effect=RuntimeError(SENTINEL)),
    )
    evaluator = _evaluator(error=RuntimeError(FAILOVER), ledger_pool=object())
    with caplog.at_level(logging.DEBUG):
        result = await evaluator.evaluate(text=SENTINEL, weight=0.1, channel="telegram")
    assert result.verdict == "IGNORE"
    _assert_clean(caplog)
    assert "ledger write failed" in caplog.text


async def test_contact_resolver_debug_log_hides_the_channel_value(caplog, monkeypatch) -> None:
    monkeypatch.setattr(
        discretion_mod,
        "resolve_contact_by_channel",
        AsyncMock(side_effect=RuntimeError("db down")),
    )
    resolver = ContactWeightResolver(object())
    with caplog.at_level(logging.DEBUG):
        await resolver.resolve("telegram", SENTINEL)
    _assert_clean(caplog)
    assert "channel_type=telegram value_sha=" in caplog.text
