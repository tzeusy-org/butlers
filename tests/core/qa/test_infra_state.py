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
from unittest.mock import MagicMock

import asyncpg
import pytest

from butlers.core.qa.sources.infra_state import InfraStateSource
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
