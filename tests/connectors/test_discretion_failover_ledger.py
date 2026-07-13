"""Discretion failover-exhausted suppression → attention-ledger recording (bu-5go3y).

When the shared connector discretion layer's same-tier model failover exhausts,
``DiscretionEvaluator`` falls back to the weight-default IGNORE verdict — a
degraded, fabricated suppression that silently drops a message the owner would
otherwise have seen. Before this change that gap was observable only via an
ERROR log + the ``discretion_evaluations_total`` metric; now it is durably
recorded to ``public.attention_ledger`` with ``source="discretion"`` /
``outcome="suppressed"``.

Classify-before-flagging: ONLY the failover-exhausted weight-default IGNORE is
recorded. Genuine model-judged IGNORE verdicts, weight-default FORWARD
(fail-open, which still reaches the pipeline), and every other failure class
(auth failure, provider unavailable, timeout, parse error, opaque exception)
MUST NOT be recorded.

The ledger write is best-effort/fail-open: a ledger failure MUST NOT alter the
discretion verdict the evaluator returns.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import butlers.connectors.discretion as discretion_mod
from butlers.connectors.discretion import DiscretionEvaluator

pytestmark = pytest.mark.unit

_FAILOVER_EXC = RuntimeError(
    "same_tier_failover_exhausted: tier=specialty after 5 attempt(s); last error: TimeoutError: "
)


def _dispatcher(*, side_effect: Exception | None = None, response: str | None = None) -> AsyncMock:
    d = AsyncMock()
    if side_effect is not None:
        d.call = AsyncMock(side_effect=side_effect)
    else:
        d.call = AsyncMock(return_value=response)
    # Prevent the AsyncMock from auto-vivifying a truthy ``.pool``; the tests
    # inject ``ledger_pool`` explicitly so the write target is deterministic.
    d.pool = None
    return d


async def test_failover_exhausted_fail_closed_records_ledger(monkeypatch) -> None:
    rec = AsyncMock(return_value="row-id")
    monkeypatch.setattr(discretion_mod, "record_attention_event", rec)
    sentinel_pool = object()
    ev = DiscretionEvaluator(
        source_name="tg:123",
        dispatcher=_dispatcher(side_effect=_FAILOVER_EXC),
        ledger_pool=sentinel_pool,
    )

    result = await ev.evaluate(text="spam", weight=0.1, channel="telegram")

    assert result.verdict == "IGNORE"
    assert result.is_fail_open is False
    rec.assert_awaited_once()
    assert rec.await_args.args[0] is sentinel_pool
    kwargs = rec.await_args.kwargs
    assert kwargs["source"] == "discretion"
    assert kwargs["outcome"] == "suppressed"
    assert kwargs["reason"] == "failover_exhausted"
    assert kwargs["origin_butler"] == "__discretion__"
    assert kwargs["channel"] == "telegram"
    assert kwargs["intent"] == "discretion"
    meta = kwargs["metadata"]
    assert meta["weight_default"] is True
    assert meta["verdict"] == "IGNORE"
    assert meta["source_identity"] == "tg:123"
    assert meta["weight"] == 0.1
    assert "tier=specialty" in meta["detail"]


async def test_failover_exhausted_fail_open_does_not_record(monkeypatch) -> None:
    """weight >= weight_fail_open → FORWARD (fail-open): the message still
    reaches the pipeline, so it is not an honesty gap and is not recorded."""
    rec = AsyncMock()
    monkeypatch.setattr(discretion_mod, "record_attention_event", rec)
    ev = DiscretionEvaluator(
        source_name="tg",
        dispatcher=_dispatcher(side_effect=_FAILOVER_EXC),
        ledger_pool=object(),
    )

    result = await ev.evaluate(text="spam", weight=0.7)

    assert result.verdict == "FORWARD"
    assert result.is_fail_open is True
    rec.assert_not_awaited()


async def test_genuine_llm_ignore_does_not_record(monkeypatch) -> None:
    rec = AsyncMock()
    monkeypatch.setattr(discretion_mod, "record_attention_event", rec)
    ev = DiscretionEvaluator(
        source_name="tg",
        dispatcher=_dispatcher(response="IGNORE"),
        ledger_pool=object(),
    )

    result = await ev.evaluate(text="ambient chatter", weight=0.1)

    assert result.verdict == "IGNORE"
    rec.assert_not_awaited()


@pytest.mark.parametrize(
    "exc",
    [
        RuntimeError("Codex CLI exited with code 1: unexpected status 401 Unauthorized"),
        RuntimeError("Connection refused: could not reach provider"),
        TimeoutError(),
        ValueError("unexpected business error"),
    ],
    ids=["auth_failure", "provider_unavailable", "timeout", "opaque_error"],
)
async def test_non_failover_fail_closed_does_not_record(monkeypatch, exc: Exception) -> None:
    """Every non-failover-exhaustion failure class is out of scope — only the
    failover-exhausted fabricated suppression is an honesty gap this bead
    records (classify-before-flagging)."""
    rec = AsyncMock()
    monkeypatch.setattr(discretion_mod, "record_attention_event", rec)
    ev = DiscretionEvaluator(
        source_name="tg",
        dispatcher=_dispatcher(side_effect=exc),
        ledger_pool=object(),
    )

    result = await ev.evaluate(text="spam", weight=0.1)

    assert result.verdict == "IGNORE"
    rec.assert_not_awaited()


async def test_ledger_write_failure_does_not_alter_verdict(monkeypatch) -> None:
    """Fail-open: a ledger-write exception is swallowed and the discretion
    verdict is returned unchanged."""
    rec = AsyncMock(side_effect=RuntimeError("ledger boom"))
    monkeypatch.setattr(discretion_mod, "record_attention_event", rec)
    ev = DiscretionEvaluator(
        source_name="tg",
        dispatcher=_dispatcher(side_effect=_FAILOVER_EXC),
        ledger_pool=object(),
    )

    result = await ev.evaluate(text="spam", weight=0.1)

    assert result.verdict == "IGNORE"
    assert result.is_fail_open is False
    rec.assert_awaited_once()


async def test_pool_auto_resolved_from_dispatcher(monkeypatch) -> None:
    """The six existing connector construction sites inject only a dispatcher;
    the evaluator resolves the ledger pool from ``dispatcher.pool`` so those
    sites are wired automatically without a construction-site change."""
    rec = AsyncMock()
    monkeypatch.setattr(discretion_mod, "record_attention_event", rec)
    sentinel_pool = object()

    class _Disp:
        pool = sentinel_pool
        call = AsyncMock(side_effect=_FAILOVER_EXC)

    ev = DiscretionEvaluator(source_name="tg", dispatcher=_Disp())

    result = await ev.evaluate(text="spam", weight=0.1)

    assert result.verdict == "IGNORE"
    rec.assert_awaited_once()
    assert rec.await_args.args[0] is sentinel_pool


async def test_missing_dispatcher_pool_no_ops(monkeypatch) -> None:
    """A dispatcher that exposes no ``pool`` (e.g. a bare mock caller) resolves
    to ``ledger_pool=None`` and the write no-ops without touching the verdict."""
    rec = AsyncMock()
    monkeypatch.setattr(discretion_mod, "record_attention_event", rec)
    ev = DiscretionEvaluator(
        source_name="tg",
        dispatcher=_dispatcher(side_effect=_FAILOVER_EXC),  # .pool is None
    )

    result = await ev.evaluate(text="spam", weight=0.1)

    assert result.verdict == "IGNORE"
    # record_attention_event is still invoked (it is the no-op boundary), with a
    # None pool — it returns None without a DB round-trip.
    rec.assert_awaited_once()
    assert rec.await_args.args[0] is None


def test_dispatcher_exposes_pool() -> None:
    from butlers.connectors.discretion_dispatcher import DiscretionDispatcher

    sentinel = object()
    disp = DiscretionDispatcher(pool=sentinel)  # type: ignore[arg-type]
    assert disp.pool is sentinel
