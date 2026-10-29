"""Contract tests for the one model-call kernel (bu-m0vjeb.1).

``call_model`` owns resolution, the spend gate, failover with RFC 0038 D14's relaunch rule,
and attempt/usage accounting. These tests drive it with a fake catalog and a recording sink so
each rule is pinned at the kernel seam rather than through a particular caller.
"""

from __future__ import annotations

import uuid
from contextlib import ExitStack
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.core import model_call
from butlers.core.failover_classifier import FailoverDecision
from butlers.core.model_call import (
    AttemptOutcome,
    AttemptSink,
    CallSpec,
    Candidate,
    NoModelConfigured,
    SpendCeilingReached,
    call_model,
    registry_adapters,
)
from butlers.core.model_routing import (
    CEILING_DENIAL_REASON_PREFIX,
    CeilingStatus,
    Complexity,
    QuotaStatus,
    SpendRoutingResult,
)

pytestmark = pytest.mark.unit

_K = "butlers.core.model_call"


def _row(name: str) -> tuple[str, str, list[str], uuid.UUID, int]:
    return ("opencode", f"model-{name}", [], uuid.uuid5(uuid.NAMESPACE_DNS, name), 30)


A, B, C = _row("a"), _row("b"), _row("c")
_ALLOWED = QuotaStatus(allowed=True, usage_24h=0, limit_24h=None, usage_30d=0, limit_30d=None)
_EXHAUSTED = QuotaStatus(allowed=False, usage_24h=10, limit_24h=5, usage_30d=0, limit_30d=None)
_UNDER = CeilingStatus(allowed=True, mtd_usd=1.0, ceiling_usd=10.0)
_OVER = CeilingStatus(allowed=False, mtd_usd=12.0, ceiling_usd=10.0)


class _Sink(AttemptSink):
    def __init__(self) -> None:
        super().__init__(MagicMock(), metrics=MagicMock())
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.starts: list[Candidate] = []
        self.failures: list[FailoverDecision] = []

    async def attempt(self, **fields: Any) -> None:
        self.events.append(("attempt", fields))

    async def usage(self, **fields: Any) -> None:
        self.events.append(("usage", fields))

    def on_attempt_start(self, candidate: Candidate) -> None:
        self.starts.append(candidate)

    def on_runtime_failure(self, candidate: Candidate, decision: FailoverDecision) -> None:
        self.failures.append(decision)

    @property
    def outcomes(self) -> list[str]:
        return [f["outcome"] for kind, f in self.events if kind == "attempt"]


class _Catalog:
    """Patches the kernel's catalog, gate and classifier collaborators."""

    def __init__(
        self,
        *,
        first: tuple | None = A,
        fallbacks: list[tuple] | None = None,
        quotas: list[QuotaStatus] | None = None,
        ceilings: list[CeilingStatus] | None = None,
        routed: tuple | None = None,
    ) -> None:
        async def resolve(pool, butler, tier, *, receipt_intent, receipt_sink):
            receipt_sink.append(SimpleNamespace(describe=lambda: {"candidates": []}))
            return None if first is None else (*first, "workhorse")

        self.resolve = AsyncMock(side_effect=resolve)
        self.next = AsyncMock(side_effect=[*(fallbacks or []), None])
        self.quota = AsyncMock(side_effect=quotas) if quotas else AsyncMock(return_value=_ALLOWED)
        self.ceiling = (
            AsyncMock(side_effect=ceilings) if ceilings else AsyncMock(return_value=_UNDER)
        )
        self.rules = AsyncMock(
            side_effect=lambda pool, butler, tier, resolved, **kw: SpendRoutingResult(
                resolved=routed or resolved
            )
        )
        self._stack = ExitStack()

    def __enter__(self) -> _Catalog:
        for name, mock in (
            ("resolve_model_with_effective_tier", self.resolve),
            ("next_same_tier_candidate", self.next),
            ("check_token_quota", self.quota),
            ("check_monthly_ceiling", self.ceiling),
            ("apply_spend_routing_rules", self.rules),
        ):
            self._stack.enter_context(patch(f"{_K}.{name}", mock))
        return self

    def __exit__(self, *exc: object) -> None:
        self._stack.close()


def _adapter_for(adapter: Any = None):
    adapter = adapter or SimpleNamespace(last_process_info=None)

    async def adapter_for(candidate: Candidate) -> Any:
        return adapter

    return adapter_for


def _attempts(*results: Any):
    """An attempt function returning or raising each result in turn."""
    seen: list[Candidate] = []
    queue = list(results)

    async def attempt(adapter: Any, candidate: Candidate) -> AttemptOutcome:
        seen.append(candidate)
        result = queue.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    attempt.seen = seen  # type: ignore[attr-defined]
    return attempt


def _spec(**overrides: Any) -> CallSpec:
    return CallSpec(butler_name="b", tier=Complexity.WORKHORSE, **overrides)


async def _call(spec: CallSpec, attempt, sink: _Sink, adapter: Any = None):
    return await call_model(
        MagicMock(), spec, attempt, adapter_for=_adapter_for(adapter), sink=sink
    )


def _retryable() -> Exception:
    return RuntimeError("Connection error.")


class _ToolCallFailure(RuntimeError):
    tool_calls = [{"name": "notify"}]


# --- resolution -------------------------------------------------------------------------


async def test_success_returns_the_text_and_records_usage_before_the_success_row() -> None:
    sink = _Sink()
    attempt = _attempts(AttemptOutcome("ok", {"input_tokens": 7, "output_tokens": 3}))
    with _Catalog():
        result = await _call(
            _spec(usage_butler_name="tg:1", usage_purpose="discretion"), attempt, sink
        )

    assert (result.text, result.candidate.model_id, result.attempts) == ("ok", "model-a", 1)
    assert [kind for kind, _ in sink.events] == ["usage", "attempt"]
    usage = sink.events[0][1]
    assert (usage["butler_name"], usage["purpose"], usage["input_tokens"]) == (
        "tg:1",
        "discretion",
        7,
    )
    assert usage["session_id"] is None
    assert sink.outcomes == ["success"]


@pytest.mark.parametrize("usage", [None, {}, {"output_tokens": 3}])
async def test_success_without_measurable_usage_writes_no_usage_row(usage) -> None:
    sink = _Sink()
    with _Catalog():
        await _call(_spec(), _attempts(AttemptOutcome("ok", usage)), sink)

    assert [kind for kind, _ in sink.events] == ["attempt"]


async def test_empty_tier_raises_no_model_configured_without_attempting() -> None:
    sink = _Sink()
    attempt = _attempts()
    with _Catalog(first=None), pytest.raises(NoModelConfigured, match="No .* model configured"):
        await _call(_spec(), attempt, sink)

    assert attempt.seen == [] and sink.events == []


async def test_spend_rule_override_dispatches_the_routed_model_with_its_receipt_reason() -> None:
    sink = _Sink()
    attempt = _attempts(AttemptOutcome("ok"))
    with _Catalog(routed=B) as catalog:
        await _call(_spec(spend_rule_trigger="discretion"), attempt, sink)

    assert catalog.rules.await_args.kwargs == {"trigger_source": "discretion"}
    assert attempt.seen[0].model_id == "model-b"
    assert sink.events[-1][1]["resolution_receipt"]["winner"]["reason"] == "spend_rule_override"


async def test_spend_rule_errors_keep_the_tier_resolved_model() -> None:
    sink = _Sink()
    attempt = _attempts(AttemptOutcome("ok"))
    with _Catalog() as catalog:
        catalog.rules.side_effect = RuntimeError("rules table missing")
        await _call(_spec(), attempt, sink)

    assert attempt.seen[0].model_id == "model-a"


# --- spend gate -------------------------------------------------------------------------


async def test_ceiling_refusal_writes_a_fleet_halt_skip_and_never_invokes() -> None:
    sink = _Sink()
    attempt = _attempts()
    with _Catalog(ceilings=[_OVER]), pytest.raises(SpendCeilingReached) as raised:
        await _call(_spec(), attempt, sink)

    assert str(raised.value).startswith(CEILING_DENIAL_REASON_PREFIX)
    assert attempt.seen == []
    ((_, row),) = sink.events
    assert (row["outcome"], row["produce_fleet_halt"], row["attempt_index"]) == (
        "quota_skip",
        True,
        0,
    )


async def test_ceiling_is_not_consulted_when_the_caller_opts_out() -> None:
    sink = _Sink()
    with _Catalog(ceilings=[_OVER]) as catalog:
        await _call(_spec(enforce_ceiling=False), _attempts(AttemptOutcome("ok")), sink)

    catalog.ceiling.assert_not_awaited()


async def test_quota_exhausted_candidate_is_skipped_for_the_next_same_tier_entry() -> None:
    sink = _Sink()
    attempt = _attempts(AttemptOutcome("ok"))
    with _Catalog(fallbacks=[B], quotas=[_EXHAUSTED, _ALLOWED]) as catalog:
        result = await _call(_spec(), attempt, sink)

    assert [c.model_id for c in attempt.seen] == ["model-b"]
    assert result.attempts == 2
    assert sink.outcomes == ["quota_skip", "success"]
    assert "24h (used=10, limit=5)" in sink.events[0][1]["failure_reason"]
    assert catalog.next.await_args.args[3] == [A[3]]
    sink.metrics.record_failover_attempt.assert_called_once_with(
        from_model="model-a", to_model="model-b", reason="quota_exhausted"
    )


async def test_quota_exhausted_tier_raises_failover_exhausted_without_invoking() -> None:
    sink = _Sink()
    attempt = _attempts()
    with _Catalog(quotas=[_EXHAUSTED]), pytest.raises(RuntimeError) as raised:
        await _call(_spec(), attempt, sink)

    assert str(raised.value).startswith("same_tier_failover_exhausted: tier=workhorse")
    assert "last quota skip: Token quota exhausted" in str(raised.value)
    assert attempt.seen == []


async def test_gate_errors_fail_open_as_catalog_token_limits_requires() -> None:
    """A database error inside either gate check lets the call proceed."""
    pool = MagicMock()
    pool.fetchrow = AsyncMock(side_effect=OSError("connection refused"))
    pool.fetch = AsyncMock(side_effect=OSError("connection refused"))
    sink = _Sink()
    with (
        patch(f"{_K}.resolve_model_with_effective_tier", AsyncMock(return_value=(*A, "workhorse"))),
        patch(f"{_K}.apply_spend_routing_rules", AsyncMock(side_effect=OSError("down"))),
    ):
        result = await call_model(
            pool, _spec(), _attempts(AttemptOutcome("ok")), adapter_for=_adapter_for(), sink=sink
        )

    assert result.text == "ok"
    assert sink.outcomes == ["success"]


# --- failover and D14 -------------------------------------------------------------------


async def test_eligible_failure_fails_over_to_the_next_same_tier_candidate() -> None:
    sink = _Sink()
    attempt = _attempts(_retryable(), AttemptOutcome("ok"))
    with _Catalog(fallbacks=[B]):
        result = await _call(_spec(), attempt, sink)

    assert [c.model_id for c in attempt.seen] == ["model-a", "model-b"]
    assert result.candidate.model_id == "model-b"
    assert sink.outcomes == ["runtime_failure", "success"]
    failure_row = sink.events[0][1]
    assert failure_row["error_code"] == "RuntimeError"
    receipt = sink.events[1][1]["resolution_receipt"]
    assert receipt["attempt_index"] == 1
    assert receipt["winner"] == {**receipt["winner"], "model_id": "model-b"}
    assert receipt["failover"]["failure_class"] == failure_row["failure_reason"]
    assert [d.eligible for d in sink.failures] == [True]


async def test_ineligible_failure_re_raises_the_original_exception() -> None:
    sink = _Sink()
    failure = ValueError("business rule")
    with _Catalog(fallbacks=[B]) as catalog, pytest.raises(ValueError) as raised:
        await _call(_spec(), _attempts(failure), sink)

    assert raised.value is failure
    catalog.next.assert_not_awaited()
    sink.metrics.record_failover_suppressed.assert_called_once()


async def test_a_capability_call_forbids_the_relaunch() -> None:
    """D14: relaunch only when no capability call was made with the failed token."""
    sink = _Sink()
    failure = _ToolCallFailure("Connection error.")
    with _Catalog(fallbacks=[B]) as catalog, pytest.raises(_ToolCallFailure):
        await _call(_spec(), _attempts(failure), sink)

    catalog.next.assert_not_awaited()
    assert [d.eligible for d in sink.failures] == [False]


async def test_one_relaunch_rule_stops_after_a_single_retry() -> None:
    sink = _Sink()
    attempt = _attempts(_retryable(), _retryable())
    with _Catalog(fallbacks=[B, C]), pytest.raises(RuntimeError) as raised:
        await _call(_spec(max_relaunches=1), attempt, sink)

    assert len(attempt.seen) == 2
    assert "(relaunch limit)" in str(raised.value)
    assert str(raised.value).startswith("same_tier_failover_exhausted")
    assert isinstance(raised.value.__cause__, RuntimeError)
    sink.metrics.record_failover_exhausted.assert_called_once_with(tier="workhorse")


async def test_every_relaunch_passes_the_ceiling_again() -> None:
    """D14: a relaunch counts against the budget, so the ceiling can refuse it."""
    sink = _Sink()
    attempt = _attempts(_retryable())
    with (
        _Catalog(fallbacks=[B], ceilings=[_UNDER, _OVER]) as catalog,
        pytest.raises(SpendCeilingReached),
    ):
        await _call(_spec(max_relaunches=1), attempt, sink)

    assert len(attempt.seen) == 1
    assert catalog.ceiling.await_count == 2
    assert sink.outcomes == ["runtime_failure", "quota_skip"]
    assert sink.events[-1][1]["attempt_index"] == 1


async def test_the_attempt_cap_bounds_an_unlimited_relaunch_budget() -> None:
    sink = _Sink()
    failures = [_retryable() for _ in range(model_call.MAX_ATTEMPTS)]
    attempt = _attempts(*failures)
    fallbacks = [_row(str(i)) for i in range(model_call.MAX_ATTEMPTS + 2)]
    with _Catalog(fallbacks=fallbacks), pytest.raises(RuntimeError, match=r"\(safety cap\)"):
        await _call(_spec(), attempt, sink)

    assert len(attempt.seen) == model_call.MAX_ATTEMPTS


async def test_an_exhausted_tier_raises_with_the_last_error() -> None:
    sink = _Sink()
    with _Catalog(), pytest.raises(RuntimeError) as raised:
        await _call(_spec(), _attempts(_retryable()), sink)

    assert str(raised.value) == (
        "same_tier_failover_exhausted: tier=workhorse after 1 attempt(s); "
        "last error: RuntimeError: Connection error."
    )


async def test_a_timeout_is_classified_like_any_other_failure() -> None:
    sink = _Sink()
    attempt = _attempts(TimeoutError(), AttemptOutcome("ok"))
    with _Catalog(fallbacks=[B]):
        result = await _call(_spec(), attempt, sink)

    assert result.candidate.model_id == "model-b"


# --- accounting -------------------------------------------------------------------------


async def test_redacted_calls_record_failures_without_provider_text() -> None:
    sink = _Sink()
    with _Catalog(fallbacks=[B]):
        await _call(
            _spec(redact_failures=True), _attempts(_retryable(), AttemptOutcome("ok")), sink
        )

    row = sink.events[0][1]
    assert row["failure_reason"] == model_call.REDACTED_RUNTIME_FAILURE
    assert row["error_code"] is None and row["error_message"] is None
    assert sink.failures[0].reason != model_call.REDACTED_RUNTIME_FAILURE


async def test_attempt_rows_carry_the_purpose_lane_and_butler() -> None:
    sink = _Sink()
    with _Catalog():
        await _call(_spec(purpose_lane="private_content"), _attempts(AttemptOutcome("ok")), sink)

    row = sink.events[-1][1]
    assert (row["butler"], row["purpose_lane"]) == ("b", "private_content")
    assert [c.model_id for c in sink.starts] == ["model-a"]


async def test_default_sink_writes_through_the_ledger_functions() -> None:
    pool = MagicMock()
    with (
        patch(f"{_K}.record_dispatch_attempt", AsyncMock()) as attempt_row,
        patch(f"{_K}.record_token_usage", AsyncMock()) as usage_row,
    ):
        sink = AttemptSink(pool)
        await sink.attempt(outcome="success")
        await sink.usage(input_tokens=1)

    attempt_row.assert_awaited_once_with(pool, outcome="success")
    usage_row.assert_awaited_once_with(pool, input_tokens=1)


async def test_registry_adapters_configure_each_candidate_provider() -> None:
    registry = MagicMock()
    pool = MagicMock()
    with patch(f"{_K}.resolve_provider_config", AsyncMock(return_value={"p": {}})) as resolve:
        adapter = await registry_adapters(pool, registry)(Candidate(*A))

    resolve.assert_awaited_once_with(pool, "model-a")
    registry.for_runtime.assert_called_once_with("opencode", {"p": {}})
    assert adapter is registry.for_runtime.return_value
