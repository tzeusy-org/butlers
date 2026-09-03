"""Executable planning contract for Finance recurrence producer authority.

Spec: REQ-finance-supporting-tables-001, REQ-butler-finance-001,
REQ-finance-alerts-001, and REQ-expected-signals-001.

The specification matrix remains executable alongside the endpoint-bound RFC 0029 runtime
implementation delivered by continued bu-8cdl1.3.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from butlers.core.expected_signals import ExpectedSignalState, evaluate_expected_signal
from butlers.core.liveness import is_liveness_stale

pytestmark = pytest.mark.contract

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CHANGE = "openspec/changes/archive/2026-09-03-finance-recurrence-producer-mapping"
_DESIGN = _REPO_ROOT / _CHANGE / "design.md"
_START = "<!-- finance-recurrence-producer-map:start -->"
_END = "<!-- finance-recurrence-producer-map:end -->"
_NOW = datetime(2026, 9, 3, 12, tzinfo=UTC)
_DEAD_ENDPOINT = "gmail:user:dead@example.com"
_HEALTHY_ENDPOINT = "gmail:user:healthy@example.com"
_ENDPOINT_LIVENESS_SQL = """
    SELECT state, last_heartbeat_at
    FROM public.v_qa_connector_state
    WHERE connector_type = $1
      AND endpoint_identity = $2
"""


def _source_rows() -> list[dict[str, Any]]:
    text = _DESIGN.read_text(encoding="utf-8")
    block = text.split(_START, 1)[1].split(_END, 1)[0]
    match = re.search(r"```json\s*(\[.*\])\s*```", block, flags=re.DOTALL)
    assert match is not None, "design must carry the executable producer-map JSON block"
    rows = json.loads(match.group(1))
    assert isinstance(rows, list)
    return rows


def _absence_candidate(state: ExpectedSignalState) -> str | None:
    """No approved Finance policy currently consumes recurrence absence."""
    assert state in {
        ExpectedSignalState.PRESENT,
        ExpectedSignalState.ABSENT,
        ExpectedSignalState.UNMEASURABLE,
    }
    return None


@dataclass(frozen=True)
class _EndpointEvaluation:
    state: ExpectedSignalState
    reason: str | None


async def _evaluate_gmail_endpoint(
    pool: Any,
    *,
    producer_endpoint_identity: str | None,
    last_observed_at: datetime,
    expected_cadence: timedelta,
) -> _EndpointEvaluation:
    """Execute the target exact-endpoint contract without changing landed runtime code."""
    if not producer_endpoint_identity:
        return _EndpointEvaluation(ExpectedSignalState.UNMEASURABLE, "producer_endpoint_missing")
    try:
        rows = await pool.fetch(_ENDPOINT_LIVENESS_SQL, "gmail", producer_endpoint_identity)
    except Exception:  # noqa: BLE001 - unavailable evidence must fail closed
        return _EndpointEvaluation(ExpectedSignalState.UNMEASURABLE, "liveness_unavailable")
    if not rows:
        return _EndpointEvaluation(ExpectedSignalState.UNMEASURABLE, "producer_unregistered")
    if any(
        row["state"] == "healthy"
        and not is_liveness_stale(row["last_heartbeat_at"], ttl_seconds=300, now=_NOW)
        for row in rows
    ):
        state = (
            ExpectedSignalState.ABSENT
            if last_observed_at + expected_cadence <= _NOW
            else ExpectedSignalState.PRESENT
        )
        return _EndpointEvaluation(state, None)
    if any(row["state"] in {"error", "degraded", "paused"} for row in rows):
        return _EndpointEvaluation(ExpectedSignalState.UNMEASURABLE, "producer_not_healthy")
    return _EndpointEvaluation(ExpectedSignalState.UNMEASURABLE, "producer_stale_or_offline")


def _endpoint_pool(rows: list[dict[str, Any]]) -> AsyncMock:
    """Build a liveness view double that enforces exact type/endpoint filtering."""
    pool = AsyncMock()

    async def _fetch(sql: str, connector_type: str, endpoint_identity: str):
        assert "connector_type = $1" in sql
        assert "endpoint_identity = $2" in sql
        return [
            row
            for row in rows
            if row["connector_type"] == connector_type
            and row["endpoint_identity"] == endpoint_identity
        ]

    pool.fetch.side_effect = _fetch
    return pool


def _resolve_contract_producer(rows: list[dict[str, Any]]) -> str:
    """Resolve exactly one mapped producer; every other source set fails closed."""
    if any(row["mapping"] != "mapped" for row in rows):
        return "unknown"
    producers = {row["producer"] for row in rows}
    if len(producers) != 1:
        return "unknown"
    producer = next(iter(producers))
    return producer if isinstance(producer, str) else "unknown"


@pytest.mark.parametrize(
    ("state", "heartbeat_age", "expected_reason"),
    [
        ("healthy", timedelta(minutes=6), "producer_stale_or_offline"),
        ("offline", timedelta(), "producer_stale_or_offline"),
        ("error", timedelta(), "producer_not_healthy"),
        ("degraded", timedelta(), "producer_not_healthy"),
        ("paused", timedelta(), "producer_not_healthy"),
    ],
)
async def test_killed_gmail_past_expected_date_is_unmeasurable_without_candidate(
    state: str,
    heartbeat_age: timedelta,
    expected_reason: str,
) -> None:
    """The exact mapped endpoint fails closed after its recurrence date elapses."""
    pool = _endpoint_pool(
        [
            {
                "connector_type": "gmail",
                "endpoint_identity": _DEAD_ENDPOINT,
                "state": state,
                "last_heartbeat_at": _NOW - heartbeat_age,
            }
        ]
    )

    result = await _evaluate_gmail_endpoint(
        pool,
        producer_endpoint_identity=_DEAD_ENDPOINT,
        expected_cadence=timedelta(days=30),
        last_observed_at=_NOW - timedelta(days=31),
    )

    assert result.state is ExpectedSignalState.UNMEASURABLE
    assert result.reason == expected_reason
    assert _absence_candidate(result.state) is None


async def test_missing_gmail_endpoint_is_unmeasurable_without_candidate() -> None:
    """No row for the exact endpoint remains missing even if its connector type exists."""
    pool = _endpoint_pool([])

    result = await _evaluate_gmail_endpoint(
        pool,
        producer_endpoint_identity=_DEAD_ENDPOINT,
        expected_cadence=timedelta(days=30),
        last_observed_at=_NOW - timedelta(days=31),
    )

    assert result == _EndpointEvaluation(ExpectedSignalState.UNMEASURABLE, "producer_unregistered")
    assert _absence_candidate(result.state) is None


async def test_unreadable_gmail_liveness_is_unmeasurable_without_candidate() -> None:
    """An unavailable liveness projection cannot become a missed-renewal claim."""
    pool = AsyncMock()
    pool.fetch.side_effect = RuntimeError("liveness unavailable")

    result = await _evaluate_gmail_endpoint(
        pool,
        producer_endpoint_identity=_DEAD_ENDPOINT,
        expected_cadence=timedelta(days=365),
        last_observed_at=_NOW - timedelta(days=366),
    )

    assert result.state is ExpectedSignalState.UNMEASURABLE
    assert result.reason == "liveness_unavailable"
    assert _absence_candidate(result.state) is None


async def test_healthy_elapsed_gmail_is_absent_without_inventing_alert_policy() -> None:
    """Healthy absence is state evidence only until an alert consumer is approved."""
    pool = _endpoint_pool(
        [
            {
                "connector_type": "gmail",
                "endpoint_identity": _HEALTHY_ENDPOINT,
                "state": "healthy",
                "last_heartbeat_at": _NOW - timedelta(seconds=30),
            }
        ]
    )

    result = await _evaluate_gmail_endpoint(
        pool,
        producer_endpoint_identity=_HEALTHY_ENDPOINT,
        expected_cadence=timedelta(days=30),
        last_observed_at=_NOW - timedelta(days=30),
    )

    assert result.state is ExpectedSignalState.ABSENT
    assert _absence_candidate(result.state) is None


@pytest.mark.parametrize("reverse_rows", [False, True])
async def test_healthy_gmail_sibling_cannot_authorize_dead_endpoint_in_either_row_order(
    reverse_rows: bool,
) -> None:
    """The endpoint-bound query prevents account B from authorizing dead account A."""
    rows = [
        {
            "connector_type": "gmail",
            "endpoint_identity": _DEAD_ENDPOINT,
            "state": "offline",
            "last_heartbeat_at": _NOW,
        },
        {
            "connector_type": "gmail",
            "endpoint_identity": _HEALTHY_ENDPOINT,
            "state": "healthy",
            "last_heartbeat_at": _NOW - timedelta(seconds=30),
        },
    ]
    pool = _endpoint_pool(list(reversed(rows)) if reverse_rows else rows)

    result = await _evaluate_gmail_endpoint(
        pool,
        producer_endpoint_identity=_DEAD_ENDPOINT,
        expected_cadence=timedelta(days=30),
        last_observed_at=_NOW - timedelta(days=31),
    )

    assert result == _EndpointEvaluation(
        ExpectedSignalState.UNMEASURABLE, "producer_stale_or_offline"
    )
    pool.fetch.assert_awaited_once_with(_ENDPOINT_LIVENESS_SQL, "gmail", _DEAD_ENDPOINT)


async def test_missing_required_gmail_endpoint_fails_closed_without_liveness_query() -> None:
    """Connector type alone is never sufficient authority."""
    pool = AsyncMock()

    result = await _evaluate_gmail_endpoint(
        pool,
        producer_endpoint_identity=None,
        expected_cadence=timedelta(days=30),
        last_observed_at=_NOW - timedelta(days=31),
    )

    assert result == _EndpointEvaluation(
        ExpectedSignalState.UNMEASURABLE, "producer_endpoint_missing"
    )
    pool.fetch.assert_not_awaited()


async def test_attested_owner_elapsed_is_absent_but_has_no_payment_wording() -> None:
    """Owner measurability stays bounded to the absence of an owner-recorded row."""
    pool = AsyncMock()

    result = await evaluate_expected_signal(
        pool,
        signal_key="finance:subscription-renewal:sub-1",
        producer="owner",
        expected_cadence=timedelta(days=365),
        last_observed_at=_NOW - timedelta(days=365),
        now=_NOW,
    )

    assert result.state is ExpectedSignalState.ABSENT
    assert _absence_candidate(result.state) is None
    pool.fetch.assert_not_awaited()


@pytest.mark.parametrize(
    "source_ids",
    [
        ["gmail_transaction_attested", "owner_transaction_attested"],
        ["simplefin_aggregator"],
        ["current_email_message_id"],
        ["mixed_recurring_group"],
        ["declared_renewal_date_only"],
    ],
)
async def test_mixed_or_unprovable_elapsed_source_is_unmeasurable_without_candidate(
    source_ids: list[str],
) -> None:
    """Source resolution feeds unknown to RFC 0029 instead of selecting a healthy peer."""
    all_rows = _source_rows()
    selected = [row for row in all_rows if row["source_id"] in source_ids]
    assert len(selected) == len(source_ids)
    pool = AsyncMock()

    result = await evaluate_expected_signal(
        pool,
        signal_key="finance:recurrence:group-1",
        producer=_resolve_contract_producer(selected),
        expected_cadence=timedelta(days=30),
        last_observed_at=_NOW - timedelta(days=31),
        now=_NOW,
    )

    assert result.state is ExpectedSignalState.UNMEASURABLE
    assert result.unmeasurable_reason == "producer_unknown"
    assert _absence_candidate(result.state) is None
    pool.fetch.assert_not_awaited()


def test_every_current_untrusted_or_unsupported_source_is_unmeasurable() -> None:
    """Current rows, SimpleFIN, mixed groups, and generic sources never guess a producer."""
    rows = _source_rows()
    unmeasurable = {row["source_id"] for row in rows if row["mapping"] == "unmeasurable"}

    assert unmeasurable == {
        "current_email_message_id",
        "current_manual_or_bulk",
        "simplefin_aggregator",
        "api_or_bank_sync",
        "legacy_backfill_or_split",
        "current_recurring_group",
        "mixed_recurring_group",
    }
    assert all(row["producer"] is None for row in rows if row["mapping"] != "mapped")


def test_mapping_has_only_supported_rfc_0029_producers() -> None:
    """The contract does not invent a SimpleFIN schedule producer or generic Finance connector."""
    mapped = {row["producer"] for row in _source_rows() if row["mapping"] == "mapped"}
    assert mapped == {"connector:gmail", "owner"}

    for row in _source_rows():
        endpoint = row["producer_endpoint_identity"]
        if row["producer"] == "connector:gmail":
            assert endpoint == "required:source_endpoint_identity"
        else:
            assert endpoint is None


class _FakeMCP:
    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def tool(self):
        def _decorator(fn):
            self.tools[fn.__name__] = fn
            return fn

        return _decorator


class _FakeModule:
    def __init__(self, pool: Any) -> None:
        self._pool = pool

    def _get_pool(self) -> Any:
        return self._pool


async def test_registered_subscription_fact_writer_is_inventoried_outside_current_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The actual MCP surface cannot drift beyond the reviewed source inventory."""
    from butlers.modules._roster_finance.tools import register_tools
    from butlers.tools.finance import facts

    fact_writer = AsyncMock(return_value={"id": "fact-1"})
    monkeypatch.setattr(facts, "track_subscription_fact", fact_writer)
    mcp = _FakeMCP()
    pool = AsyncMock()
    register_tools(mcp, _FakeModule(pool), SimpleNamespace(groups=["facts"]))

    assert "track_subscription_fact" in mcp.tools
    row = next(row for row in _source_rows() if row["source_id"] == "subscription_fact_writer")
    assert row["mapping"] == "outside_current_inputs"
    assert row["producer"] is None

    await mcp.tools["track_subscription_fact"](
        service="Example",
        amount=10.0,
        currency="USD",
        frequency="monthly",
        next_renewal="2026-10-01",
        source_message_id="caller-controlled",
        metadata='{"source":"caller-controlled"}',
    )

    fact_writer.assert_awaited_once()
    assert fact_writer.await_args.kwargs["source_message_id"] == "caller-controlled"
    assert fact_writer.await_args.kwargs["metadata"] == {"source": "caller-controlled"}


def test_tracked_renewal_and_inferred_recurrence_policies_remain_bounded() -> None:
    """The packet preserves forward-looking policies and names no missed-renewal consumer."""
    design = " ".join(_DESIGN.read_text(encoding="utf-8").split())
    assert "active yearly tracked subscription within 14 days" in design
    assert "untracked regular payment predicted inside the next 30 days" in design
    assert "produces no new candidate or dashboard verdict" in design
    assert '"missed renewal"' in design


def test_endpoint_contract_names_implemented_runtime_owner() -> None:
    """Docs bind endpoint enforcement and migration evidence to continued adoption."""
    paths = [
        "about/legends-and-lore/rfcs/0029-expected-signals-and-honest-absence.md",
        "docs/concepts/expected-signals.md",
        "docs/butlers/finance.md",
        f"{_CHANGE}/design.md",
        f"{_CHANGE}/tasks.md",
    ]
    normalized = {
        path: " ".join((_REPO_ROOT / path).read_text(encoding="utf-8").split()) for path in paths
    }

    assert "core_211` adds the endpoint column" in normalized[paths[0]]
    assert "core_211` migrates legacy connector rows" in normalized[paths[1]]
    assert "state-only expected-signals surface" in normalized[paths[2]]
    assert "Continued `bu-8cdl1.3` implements" in normalized[paths[3]]
    assert "existing Health call sites and rows" in normalized[paths[4]]
    assert "migrated-PostgreSQL" in normalized[paths[4]]
