"""Tests for butlers.core.qa.sources.infra_state.InfraStateSource.

Covers:
- DiscoverySource protocol compliance + health check runs before row processing
- connector-offline: offline connector trips a finding; paused/healthy do not;
  a freshly-registered never-heartbeated connector gets a grace window
- heartbeat-stale: a butler past its liveness_ttl_seconds trips a finding;
  within-ttl does not; quarantined always trips; freshly-registered is graced
- backup-stale: unconfigured is a legitimate absence (no finding); configured
  but unreachable/empty/stale trips a finding; fresh does not
- external-deadman-stale: unconfigured is a legitimate absence; stale/never
  trips a finding; recent does not
- a query error degrades (raises, caught by the QA patrol loop upstream),
  never a false all-clear
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest

from butlers.core.qa.sources.infra_state import (
    _DEADMAN_UNCONFIGURED_FINGERPRINT,
    SOURCE_NAME,
    InfraStateSource,
)
from butlers.core.qa.sources.protocol import DiscoverySource

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row(fields: dict) -> MagicMock:
    """Build a mock asyncpg Record from a plain dict."""
    record = MagicMock()
    record.__getitem__ = lambda self, key: fields[key]
    return record


class _FakePool:
    """Fake asyncpg pool: dispatches .fetch()/.fetchrow() by view/table name."""

    def __init__(
        self,
        *,
        connector_rows: list | None = None,
        heartbeat_rows: list | None = None,
        deadman_ts: datetime | None = None,
        health_check_error: Exception | None = None,
    ) -> None:
        self._connector_rows = connector_rows or []
        self._heartbeat_rows = heartbeat_rows or []
        self._deadman_ts = deadman_ts
        self._health_check_error = health_check_error

    async def execute(self, sql: str, *args):
        if self._health_check_error is not None:
            raise self._health_check_error
        return None

    async def fetch(self, sql: str, *args):
        if "v_qa_connector_state" in sql:
            return self._connector_rows
        if "v_qa_butler_heartbeat" in sql:
            return self._heartbeat_rows
        raise AssertionError(f"Unexpected fetch: {sql}")

    async def fetchrow(self, sql: str, *args):
        if "audit_log" in sql:
            return {"ts": self._deadman_ts} if self._deadman_ts is not None else None
        raise AssertionError(f"Unexpected fetchrow: {sql}")


def _connector_row(
    *,
    connector_type: str = "gmail",
    endpoint_identity: str = "owner@example.com",
    state: str = "healthy",
    error_message: str | None = None,
    last_heartbeat_at: datetime | None = None,
    first_seen_at: datetime | None = None,
) -> MagicMock:
    return _row(
        {
            "connector_type": connector_type,
            "endpoint_identity": endpoint_identity,
            "state": state,
            "error_message": error_message,
            "last_heartbeat_at": last_heartbeat_at,
            "first_seen_at": first_seen_at or (datetime.now(UTC) - timedelta(days=30)),
        }
    )


def _heartbeat_row(
    *,
    name: str = "finance",
    last_seen_at: datetime | None = None,
    registered_at: datetime | None = None,
    liveness_ttl_seconds: int = 300,
    quarantined_at: datetime | None = None,
) -> MagicMock:
    return _row(
        {
            "name": name,
            "last_seen_at": last_seen_at,
            "registered_at": registered_at or (datetime.now(UTC) - timedelta(days=30)),
            "liveness_ttl_seconds": liveness_ttl_seconds,
            "quarantined_at": quarantined_at,
        }
    )


# ---------------------------------------------------------------------------
# Protocol + health check
# ---------------------------------------------------------------------------


async def test_protocol_and_health_check(monkeypatch):
    import inspect

    monkeypatch.delenv("BUTLERS_BACKUP_DIR", raising=False)
    monkeypatch.delenv("EXTERNAL_DEADMAN_URL", raising=False)

    source = InfraStateSource(pool=_FakePool())
    assert isinstance(source, DiscoverySource)
    assert source.name == "infra_state"
    assert inspect.iscoroutinefunction(source.discover)

    findings = await source.discover(lookback_minutes=15)
    assert findings == []


async def test_health_check_failure_propagates(monkeypatch):
    monkeypatch.delenv("BUTLERS_BACKUP_DIR", raising=False)
    monkeypatch.delenv("EXTERNAL_DEADMAN_URL", raising=False)

    pool = _FakePool(health_check_error=asyncpg.PostgresError("permission denied"))
    with pytest.raises(asyncpg.PostgresError):
        await InfraStateSource(pool=pool).discover(lookback_minutes=15)


# ---------------------------------------------------------------------------
# connector-offline
# ---------------------------------------------------------------------------


async def test_offline_connector_trips_a_finding(monkeypatch):
    monkeypatch.delenv("BUTLERS_BACKUP_DIR", raising=False)
    monkeypatch.delenv("EXTERNAL_DEADMAN_URL", raising=False)

    row = _connector_row(
        connector_type="gmail",
        endpoint_identity="owner@example.com",
        state="error",
        last_heartbeat_at=datetime.now(UTC) - timedelta(minutes=20),
    )
    findings = await InfraStateSource(pool=_FakePool(connector_rows=[row])).discover(
        lookback_minutes=15
    )
    assert len(findings) == 1
    f = findings[0]
    assert f.exception_type == "ConnectorOffline"
    assert f.source_type == "infra_state"
    assert f.call_site == "connector:gmail/owner@example.com"
    assert len(f.fingerprint) == 64


async def test_online_connector_yields_nothing(monkeypatch):
    monkeypatch.delenv("BUTLERS_BACKUP_DIR", raising=False)
    monkeypatch.delenv("EXTERNAL_DEADMAN_URL", raising=False)

    row = _connector_row(last_heartbeat_at=datetime.now(UTC) - timedelta(minutes=1))
    findings = await InfraStateSource(pool=_FakePool(connector_rows=[row])).discover(
        lookback_minutes=15
    )
    assert findings == []


async def test_paused_connector_never_flagged_even_if_stale(monkeypatch):
    monkeypatch.delenv("BUTLERS_BACKUP_DIR", raising=False)
    monkeypatch.delenv("EXTERNAL_DEADMAN_URL", raising=False)

    row = _connector_row(
        state="paused",
        last_heartbeat_at=datetime.now(UTC) - timedelta(days=10),
    )
    findings = await InfraStateSource(pool=_FakePool(connector_rows=[row])).discover(
        lookback_minutes=15
    )
    assert findings == []


async def test_freshly_registered_never_heartbeated_connector_is_graced(monkeypatch):
    monkeypatch.delenv("BUTLERS_BACKUP_DIR", raising=False)
    monkeypatch.delenv("EXTERNAL_DEADMAN_URL", raising=False)

    row = _connector_row(
        last_heartbeat_at=None,
        first_seen_at=datetime.now(UTC) - timedelta(minutes=2),
    )
    findings = await InfraStateSource(pool=_FakePool(connector_rows=[row])).discover(
        lookback_minutes=15
    )
    assert findings == []


async def test_never_heartbeated_connector_past_grace_trips_a_finding(monkeypatch):
    monkeypatch.delenv("BUTLERS_BACKUP_DIR", raising=False)
    monkeypatch.delenv("EXTERNAL_DEADMAN_URL", raising=False)

    row = _connector_row(
        last_heartbeat_at=None,
        first_seen_at=datetime.now(UTC) - timedelta(hours=1),
    )
    findings = await InfraStateSource(pool=_FakePool(connector_rows=[row])).discover(
        lookback_minutes=15
    )
    assert len(findings) == 1
    assert findings[0].exception_type == "ConnectorOffline"


# ---------------------------------------------------------------------------
# heartbeat-stale
# ---------------------------------------------------------------------------


async def test_stale_butler_heartbeat_trips_a_finding(monkeypatch):
    monkeypatch.delenv("BUTLERS_BACKUP_DIR", raising=False)
    monkeypatch.delenv("EXTERNAL_DEADMAN_URL", raising=False)

    row = _heartbeat_row(
        name="finance",
        last_seen_at=datetime.now(UTC) - timedelta(minutes=20),
        liveness_ttl_seconds=300,  # 5 minutes
    )
    findings = await InfraStateSource(pool=_FakePool(heartbeat_rows=[row])).discover(
        lookback_minutes=15
    )
    assert len(findings) == 1
    f = findings[0]
    assert f.exception_type == "ButlerHeartbeatStale"
    assert f.source_butler == "finance"
    assert f.call_site == "butler_heartbeat:finance"


async def test_butler_within_ttl_yields_nothing(monkeypatch):
    monkeypatch.delenv("BUTLERS_BACKUP_DIR", raising=False)
    monkeypatch.delenv("EXTERNAL_DEADMAN_URL", raising=False)

    row = _heartbeat_row(
        last_seen_at=datetime.now(UTC) - timedelta(seconds=30),
        liveness_ttl_seconds=300,
    )
    findings = await InfraStateSource(pool=_FakePool(heartbeat_rows=[row])).discover(
        lookback_minutes=15
    )
    assert findings == []


async def test_future_heartbeat_within_tolerance_yields_nothing(monkeypatch):
    monkeypatch.delenv("BUTLERS_BACKUP_DIR", raising=False)
    monkeypatch.delenv("EXTERNAL_DEADMAN_URL", raising=False)

    # 1 min in the future — within the 5-min skew tolerance, still trusted.
    row = _heartbeat_row(
        last_seen_at=datetime.now(UTC) + timedelta(minutes=1),
        liveness_ttl_seconds=300,
    )
    findings = await InfraStateSource(pool=_FakePool(heartbeat_rows=[row])).discover(
        lookback_minutes=15
    )
    assert findings == []


async def test_future_heartbeat_beyond_tolerance_trips_a_finding(monkeypatch):
    monkeypatch.delenv("BUTLERS_BACKUP_DIR", raising=False)
    monkeypatch.delenv("EXTERNAL_DEADMAN_URL", raising=False)

    # 10 min in the future — beyond the 5-min tolerance. Without the guard the
    # unbounded TTL window (last_seen_at + ttl >= now) would evade the detector;
    # a future-dated heartbeat must be flagged stale.
    row = _heartbeat_row(
        name="finance",
        last_seen_at=datetime.now(UTC) + timedelta(minutes=10),
        liveness_ttl_seconds=300,
    )
    findings = await InfraStateSource(pool=_FakePool(heartbeat_rows=[row])).discover(
        lookback_minutes=15
    )
    assert len(findings) == 1
    assert findings[0].exception_type == "ButlerHeartbeatStale"
    assert findings[0].source_butler == "finance"


async def test_quarantined_butler_always_trips_regardless_of_ttl(monkeypatch):
    monkeypatch.delenv("BUTLERS_BACKUP_DIR", raising=False)
    monkeypatch.delenv("EXTERNAL_DEADMAN_URL", raising=False)

    row = _heartbeat_row(
        last_seen_at=datetime.now(UTC) - timedelta(seconds=10),
        liveness_ttl_seconds=300,
        quarantined_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    findings = await InfraStateSource(pool=_FakePool(heartbeat_rows=[row])).discover(
        lookback_minutes=15
    )
    assert len(findings) == 1
    assert findings[0].exception_type == "ButlerHeartbeatStale"


async def test_freshly_registered_butler_never_seen_is_graced(monkeypatch):
    monkeypatch.delenv("BUTLERS_BACKUP_DIR", raising=False)
    monkeypatch.delenv("EXTERNAL_DEADMAN_URL", raising=False)

    row = _heartbeat_row(
        last_seen_at=None,
        registered_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    findings = await InfraStateSource(pool=_FakePool(heartbeat_rows=[row])).discover(
        lookback_minutes=15
    )
    assert findings == []


async def test_never_seen_butler_past_grace_trips_a_finding(monkeypatch):
    monkeypatch.delenv("BUTLERS_BACKUP_DIR", raising=False)
    monkeypatch.delenv("EXTERNAL_DEADMAN_URL", raising=False)

    row = _heartbeat_row(
        last_seen_at=None,
        registered_at=datetime.now(UTC) - timedelta(hours=1),
    )
    findings = await InfraStateSource(pool=_FakePool(heartbeat_rows=[row])).discover(
        lookback_minutes=15
    )
    assert len(findings) == 1


# ---------------------------------------------------------------------------
# cross-consumer parity (bu-dvzya)
#
# registry.py's _derive_eligibility_state and InfraStateSource's
# heartbeat-stale check both now delegate to the single canonical
# butlers.core.liveness.is_liveness_stale() formula. These cases hold
# last_seen_at, liveness_ttl_seconds, and quarantined_at identical across
# both consumers and assert they always agree -- this would fail if either
# call site's ttl/clock-skew handling ever drifted from the shared formula.
# registered_at is fixed far enough in the past that InfraStateSource's own
# never-seen grace window (which registry.py has no equivalent of) never
# interferes.
# ---------------------------------------------------------------------------

_PARITY_REGISTERED_AT = datetime.now(UTC) - timedelta(days=30)


@pytest.mark.parametrize(
    "last_seen_at,liveness_ttl_seconds",
    [
        (datetime.now(UTC) - timedelta(seconds=100), 300),  # fresh
        (datetime.now(UTC) - timedelta(seconds=300), 300),  # exactly at TTL boundary
        (datetime.now(UTC) - timedelta(seconds=301), 300),  # just past TTL boundary
        (datetime.now(UTC) - timedelta(hours=1), 300),  # long stale
        (datetime.now(UTC) + timedelta(minutes=1), 300),  # future, within skew tolerance
        (datetime.now(UTC) + timedelta(minutes=4), 300),  # future, just within 5min skew tolerance
        (datetime.now(UTC) + timedelta(minutes=6), 300),  # future, just past 5min skew tolerance
        (datetime.now(UTC) + timedelta(minutes=10), 300),  # future, beyond skew tolerance
        (datetime.now(UTC) - timedelta(seconds=1000), 3600),  # custom ttl keeps it fresh
        (None, 300),  # never seen (registered long ago, past infra_state's grace window)
    ],
)
async def test_registry_and_infra_state_agree_on_staleness(
    monkeypatch, last_seen_at, liveness_ttl_seconds
):
    monkeypatch.delenv("BUTLERS_BACKUP_DIR", raising=False)
    monkeypatch.delenv("EXTERNAL_DEADMAN_URL", raising=False)

    from butlers.tools.switchboard.registry.registry import _derive_eligibility_state

    now = datetime.now(UTC)
    registry_row = {
        "eligibility_state": "active",
        "quarantined_at": None,
        "last_seen_at": last_seen_at,
        "liveness_ttl_seconds": liveness_ttl_seconds,
    }
    registry_stale = _derive_eligibility_state(registry_row, now=now) == "stale"

    heartbeat_row = _heartbeat_row(
        name="finance",
        last_seen_at=last_seen_at,
        registered_at=_PARITY_REGISTERED_AT,
        liveness_ttl_seconds=liveness_ttl_seconds,
    )
    findings = await InfraStateSource(pool=_FakePool(heartbeat_rows=[heartbeat_row])).discover(
        lookback_minutes=15
    )
    infra_stale = any(f.exception_type == "ButlerHeartbeatStale" for f in findings)

    assert registry_stale == infra_stale


async def test_registry_and_infra_state_agree_quarantine_always_stale(monkeypatch):
    monkeypatch.delenv("BUTLERS_BACKUP_DIR", raising=False)
    monkeypatch.delenv("EXTERNAL_DEADMAN_URL", raising=False)

    from butlers.tools.switchboard.registry.registry import _derive_eligibility_state

    now = datetime.now(UTC)
    last_seen_at = now - timedelta(seconds=10)  # well within TTL on its own
    quarantined_at = now - timedelta(minutes=5)

    registry_row = {
        "eligibility_state": "active",
        "quarantined_at": quarantined_at,
        "last_seen_at": last_seen_at,
        "liveness_ttl_seconds": 300,
    }
    assert _derive_eligibility_state(registry_row, now=now) == "quarantined"

    heartbeat_row = _heartbeat_row(
        name="finance",
        last_seen_at=last_seen_at,
        registered_at=_PARITY_REGISTERED_AT,
        liveness_ttl_seconds=300,
        quarantined_at=quarantined_at,
    )
    findings = await InfraStateSource(pool=_FakePool(heartbeat_rows=[heartbeat_row])).discover(
        lookback_minutes=15
    )
    assert any(f.exception_type == "ButlerHeartbeatStale" for f in findings)


# ---------------------------------------------------------------------------
# backup-stale
# ---------------------------------------------------------------------------


async def test_backup_unconfigured_is_a_legitimate_absence_not_a_finding(monkeypatch):
    monkeypatch.delenv("BUTLERS_BACKUP_DIR", raising=False)
    monkeypatch.delenv("EXTERNAL_DEADMAN_URL", raising=False)

    findings = await InfraStateSource(pool=_FakePool()).discover(lookback_minutes=15)
    assert findings == []


async def test_backup_dir_configured_but_missing_trips_unreachable(monkeypatch, tmp_path):
    monkeypatch.delenv("EXTERNAL_DEADMAN_URL", raising=False)
    missing = tmp_path / "does-not-exist"
    monkeypatch.setenv("BUTLERS_BACKUP_DIR", str(missing))

    findings = await InfraStateSource(pool=_FakePool()).discover(lookback_minutes=15)
    assert len(findings) == 1
    assert findings[0].exception_type == "BackupSourceUnreachable"


async def test_backup_dir_reachable_but_empty_trips_stale(monkeypatch, tmp_path):
    monkeypatch.delenv("EXTERNAL_DEADMAN_URL", raising=False)
    monkeypatch.setenv("BUTLERS_BACKUP_DIR", str(tmp_path))

    findings = await InfraStateSource(pool=_FakePool()).discover(lookback_minutes=15)
    assert len(findings) == 1
    assert findings[0].exception_type == "BackupStale"


async def test_fresh_backup_yields_nothing(monkeypatch, tmp_path):
    import os
    import time

    monkeypatch.delenv("EXTERNAL_DEADMAN_URL", raising=False)
    monkeypatch.setenv("BUTLERS_BACKUP_DIR", str(tmp_path))
    dump = tmp_path / "butlers_20260711.sql.gz"
    dump.write_bytes(b"x")
    now = time.time()
    os.utime(dump, (now, now))

    findings = await InfraStateSource(pool=_FakePool()).discover(lookback_minutes=15)
    assert findings == []


async def test_stale_backup_trips_a_finding(monkeypatch, tmp_path):
    import os
    import time

    monkeypatch.delenv("EXTERNAL_DEADMAN_URL", raising=False)
    monkeypatch.setenv("BUTLERS_BACKUP_DIR", str(tmp_path))
    dump = tmp_path / "butlers_old.sql.gz"
    dump.write_bytes(b"x")
    stale_ts = time.time() - timedelta(hours=48).total_seconds()
    os.utime(dump, (stale_ts, stale_ts))

    findings = await InfraStateSource(pool=_FakePool()).discover(lookback_minutes=15)
    assert len(findings) == 1
    assert findings[0].exception_type == "BackupStale"


# ---------------------------------------------------------------------------
# external-deadman-stale
# ---------------------------------------------------------------------------


async def test_deadman_unconfigured_is_a_legitimate_absence(monkeypatch):
    monkeypatch.delenv("BUTLERS_BACKUP_DIR", raising=False)
    monkeypatch.delenv("EXTERNAL_DEADMAN_URL", raising=False)

    findings = await InfraStateSource(pool=_FakePool()).discover(lookback_minutes=15)
    assert findings == []


async def test_deadman_recent_success_yields_nothing(monkeypatch):
    monkeypatch.delenv("BUTLERS_BACKUP_DIR", raising=False)
    monkeypatch.setenv("EXTERNAL_DEADMAN_URL", "https://example.com/ping/abc")

    pool = _FakePool(deadman_ts=datetime.now(UTC) - timedelta(minutes=1))
    findings = await InfraStateSource(pool=pool, deadman_ping_interval_s=600).discover(
        lookback_minutes=15
    )
    assert findings == []


async def test_deadman_stale_success_trips_a_finding(monkeypatch):
    monkeypatch.delenv("BUTLERS_BACKUP_DIR", raising=False)
    monkeypatch.setenv("EXTERNAL_DEADMAN_URL", "https://example.com/ping/abc")

    pool = _FakePool(deadman_ts=datetime.now(UTC) - timedelta(hours=2))
    findings = await InfraStateSource(pool=pool, deadman_ping_interval_s=600).discover(
        lookback_minutes=15
    )
    assert len(findings) == 1
    assert findings[0].exception_type == "ExternalDeadmanStale"


async def test_deadman_never_succeeded_trips_a_finding(monkeypatch):
    monkeypatch.delenv("BUTLERS_BACKUP_DIR", raising=False)
    monkeypatch.setenv("EXTERNAL_DEADMAN_URL", "https://example.com/ping/abc")

    pool = _FakePool(deadman_ts=None)
    findings = await InfraStateSource(pool=pool, deadman_ping_interval_s=600).discover(
        lookback_minutes=15
    )
    assert len(findings) == 1
    assert findings[0].exception_type == "ExternalDeadmanStale"


# ---------------------------------------------------------------------------
# Fingerprint stability across ticks
# ---------------------------------------------------------------------------


async def test_connector_offline_fingerprint_stable_across_ticks(monkeypatch):
    """The embedded timestamp/age changes every tick; the fingerprint must not."""
    monkeypatch.delenv("BUTLERS_BACKUP_DIR", raising=False)
    monkeypatch.delenv("EXTERNAL_DEADMAN_URL", raising=False)

    heartbeat = datetime.now(UTC) - timedelta(hours=1)
    row1 = _connector_row(last_heartbeat_at=heartbeat)
    row2 = _connector_row(last_heartbeat_at=heartbeat)

    findings1 = await InfraStateSource(pool=_FakePool(connector_rows=[row1])).discover(
        lookback_minutes=15
    )
    findings2 = await InfraStateSource(pool=_FakePool(connector_rows=[row2])).discover(
        lookback_minutes=15
    )
    assert findings1[0].fingerprint == findings2[0].fingerprint


# ---------------------------------------------------------------------------
# Condition-ledger reconciliation (bu-27dxl.6.4)
# ---------------------------------------------------------------------------


async def test_reconcile_snapshot_called_with_matching_observations(monkeypatch):
    """Every returned finding's fingerprint is reused, unchanged, as the ledger observation."""
    monkeypatch.delenv("BUTLERS_BACKUP_DIR", raising=False)
    monkeypatch.setenv("EXTERNAL_DEADMAN_URL", "https://example.com/ping/abc")

    row = _connector_row(
        state="error",
        last_heartbeat_at=datetime.now(UTC) - timedelta(minutes=20),
    )
    pool = _FakePool(connector_rows=[row], deadman_ts=datetime.now(UTC) - timedelta(minutes=1))

    with patch(
        "butlers.core.qa.sources.infra_state.reconcile_snapshot", new_callable=AsyncMock
    ) as mock_reconcile:
        findings = await InfraStateSource(pool=pool).discover(lookback_minutes=15)

    assert len(findings) == 1
    mock_reconcile.assert_awaited_once()
    call_kwargs = mock_reconcile.call_args.kwargs
    assert call_kwargs["source"] == SOURCE_NAME == "infra_state"
    assert call_kwargs["snapshot_complete"] is True
    observations = call_kwargs["observations"]
    assert [o.fingerprint for o in observations] == [findings[0].fingerprint]


async def test_reconcile_includes_deadman_unconfigured_observation_without_a_finding(monkeypatch):
    """Unconfigured deadman never becomes a QaFinding but IS folded into the ledger snapshot."""
    monkeypatch.delenv("BUTLERS_BACKUP_DIR", raising=False)
    monkeypatch.delenv("EXTERNAL_DEADMAN_URL", raising=False)

    with patch(
        "butlers.core.qa.sources.infra_state.reconcile_snapshot", new_callable=AsyncMock
    ) as mock_reconcile:
        findings = await InfraStateSource(pool=_FakePool()).discover(lookback_minutes=15)

    assert findings == []  # AC4: never a QA finding / never LLM execution
    observations = mock_reconcile.call_args.kwargs["observations"]
    assert [o.fingerprint for o in observations] == [_DEADMAN_UNCONFIGURED_FINGERPRINT]


async def test_reconcile_omits_deadman_unconfigured_observation_when_configured(monkeypatch):
    monkeypatch.delenv("BUTLERS_BACKUP_DIR", raising=False)
    monkeypatch.setenv("EXTERNAL_DEADMAN_URL", "https://example.com/ping/abc")

    pool = _FakePool(deadman_ts=datetime.now(UTC) - timedelta(minutes=1))
    with patch(
        "butlers.core.qa.sources.infra_state.reconcile_snapshot", new_callable=AsyncMock
    ) as mock_reconcile:
        await InfraStateSource(pool=pool).discover(lookback_minutes=15)

    observations = mock_reconcile.call_args.kwargs["observations"]
    assert _DEADMAN_UNCONFIGURED_FINGERPRINT not in {o.fingerprint for o in observations}


async def test_reconciliation_failure_never_breaks_findings_return(monkeypatch, caplog):
    """A degraded/unreachable ledger must never take down the primary findings contract.

    ``_FakePool`` has no ``.acquire()`` (it is not a real asyncpg.Pool), so
    ``reconcile_snapshot`` raises internally here -- exercising the same
    "reconciliation write failed" path a real transient DB error would hit.
    """
    monkeypatch.delenv("BUTLERS_BACKUP_DIR", raising=False)
    monkeypatch.delenv("EXTERNAL_DEADMAN_URL", raising=False)

    row = _connector_row(
        state="error",
        last_heartbeat_at=datetime.now(UTC) - timedelta(minutes=20),
    )
    with caplog.at_level("ERROR"):
        findings = await InfraStateSource(pool=_FakePool(connector_rows=[row])).discover(
            lookback_minutes=15
        )

    assert len(findings) == 1
    assert findings[0].exception_type == "ConnectorOffline"
    assert "condition-ledger reconciliation failed" in caplog.text


async def test_health_check_failure_skips_reconciliation_entirely(monkeypatch):
    """A total health-check failure must never reach reconcile_snapshot (skip, never resolve)."""
    monkeypatch.delenv("BUTLERS_BACKUP_DIR", raising=False)
    monkeypatch.delenv("EXTERNAL_DEADMAN_URL", raising=False)

    pool = _FakePool(health_check_error=asyncpg.PostgresError("permission denied"))
    with (
        patch(
            "butlers.core.qa.sources.infra_state.reconcile_snapshot", new_callable=AsyncMock
        ) as mock_reconcile,
        pytest.raises(asyncpg.PostgresError),
    ):
        await InfraStateSource(pool=pool).discover(lookback_minutes=15)

    mock_reconcile.assert_not_awaited()
