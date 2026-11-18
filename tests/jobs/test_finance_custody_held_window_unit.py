"""Finance's held-window evidence job (bu-s11n0s.1 S5e, REQ-endpoint-custody-holds-009).

The custody engine's own hold evidence on an open ``endpoint_custody:<episode>`` case is the
durable trigger; Finance computes the window once on its own pool and contributes one
idempotent attributed row per hold. Pools, the window figures and the case store are faked here;
the real SQL is covered by ``roster/finance/tests/test_custody_held_window.py`` (Docker).
"""

from __future__ import annotations

import importlib
import sys
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import pytest

pytestmark = pytest.mark.unit

HELD_FROM = datetime(2026, 11, 19, 9, 0, tzinfo=UTC)
NOW = HELD_FROM + timedelta(hours=3)


def _fj():
    importlib.import_module("butlers.jobs._roster.finance_jobs")
    return sys.modules["butlers.jobs._roster.finance_jobs"]


class _Pool:
    def __init__(self, holds: list[dict[str, Any]]) -> None:
        self.holds = holds
        self.sql: list[str] = []

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        self.sql.append(sql)
        return self.holds


def _hold(**overrides: Any) -> dict[str, Any]:
    return {
        "case_id": str(uuid.uuid4()),
        "hold_ref": str(uuid.uuid4()),
        "held_from": HELD_FROM,
        "target_kind": "has-email",
        "generation": 2,
        "resolved_at": None,
    } | overrides


@pytest.fixture
def store(monkeypatch):
    """A case-evidence store with the idempotent unique key, and recorded figure windows."""
    fj = _fj()
    rows: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    windows: list[tuple[datetime, datetime]] = []

    @asynccontextmanager
    async def scoped(pool):
        yield object()

    async def figures(conn, *, held_from, window_end):
        windows.append((held_from, window_end))
        return {
            "transactions": 3,
            "debits": 2,
            "credits": 1,
            "first_seen_merchants": 1,
            "anomalies": "unknown",
        }

    async def contribute(pool, *, case_id, contributor, kind, ref, payload=None):
        key = (case_id, contributor, kind, ref)
        if key in rows:
            return rows[key], False
        rows[key] = {"contributor": contributor, "kind": kind, "ref": ref, "payload": payload}
        return rows[key], True

    monkeypatch.setattr(fj, "_finance_scoped_connection", scoped)
    monkeypatch.setattr(fj, "_held_window_figures", figures)
    monkeypatch.setattr("butlers.core.fleet_cases.contribute_evidence", contribute)
    return rows, windows


async def test_a_new_hold_gets_one_attributed_evidence_row_for_its_exact_window(store) -> None:
    rows, windows = store
    hold = _hold()
    result = await _fj().run_custody_held_window_evidence(_Pool([hold]), now=NOW)
    assert result == {"holds": 1, "contributed": 1, "errors": 0}
    assert windows == [(HELD_FROM, NOW)]
    ((case_id, contributor, kind, ref), row) = next(iter(rows.items()))
    assert (case_id, contributor, kind, ref) == (
        hold["case_id"],
        "finance",
        "held_window",
        hold["hold_ref"],
    )
    payload = row["payload"]
    assert payload["target_kind"] == "has-email" and payload["generation"] == 2
    assert payload["window_start"] == HELD_FROM.isoformat()
    assert payload["window_end"] == NOW.isoformat()
    assert payload["anomalies"] == "unknown"  # insufficient baselines report unknown
    # Counts only: no merchant name, amount or identifier is written.
    assert set(payload) == {
        "target_kind",
        "generation",
        "window_start",
        "window_end",
        "observed_at",
        "transactions",
        "debits",
        "credits",
        "first_seen_merchants",
        "anomalies",
    }


async def test_replay_observes_the_same_evidence(store) -> None:
    rows, _ = store
    hold = _hold()
    first = await _fj().run_custody_held_window_evidence(_Pool([hold]), now=NOW)
    again = await _fj().run_custody_held_window_evidence(
        _Pool([hold]), now=NOW + timedelta(hours=1)
    )
    assert first["contributed"] == 1 and again["contributed"] == 0
    assert len(rows) == 1
    only = next(iter(rows.values()))
    assert only["payload"]["observed_at"] == NOW.isoformat()  # computed once, never rewritten


async def test_a_resolved_hold_is_bounded_by_its_release_time(store) -> None:
    _, windows = store
    released = HELD_FROM + timedelta(hours=1)
    await _fj().run_custody_held_window_evidence(_Pool([_hold(resolved_at=released)]), now=NOW)
    assert windows == [(HELD_FROM, released)]


async def test_each_hold_of_an_episode_is_its_own_row(store) -> None:
    rows, _ = store
    case_id = str(uuid.uuid4())
    holds = [_hold(case_id=case_id), _hold(case_id=case_id, target_kind="has-handle")]
    result = await _fj().run_custody_held_window_evidence(_Pool(holds), now=NOW)
    assert result["contributed"] == 2
    assert {key[3] for key in rows} == {h["hold_ref"] for h in holds}


async def test_a_failing_window_is_counted_and_does_not_stop_the_others(store, monkeypatch) -> None:
    rows, _ = store
    fj = _fj()
    good, bad = _hold(), _hold()
    real = fj._held_window_figures

    async def flaky(conn, *, held_from, window_end):
        if flaky.calls == 0:
            flaky.calls += 1
            raise asyncpg.PostgresError("synthetic")
        return await real(conn, held_from=held_from, window_end=window_end)

    flaky.calls = 0  # type: ignore[attr-defined]
    monkeypatch.setattr(fj, "_held_window_figures", flaky)
    result = await fj.run_custody_held_window_evidence(_Pool([bad, good]), now=NOW)
    assert result == {"holds": 2, "contributed": 1, "errors": 1}
    assert {key[3] for key in rows} == {good["hold_ref"]}


async def test_the_job_reads_only_open_custody_cases_without_finance_evidence() -> None:
    pool = _Pool([])
    assert await _fj().run_custody_held_window_evidence(pool, now=NOW) == {
        "holds": 0,
        "contributed": 0,
        "errors": 0,
    }
    sql = pool.sql[0]
    assert "c.state <> 'closed'" in sql
    assert "NOT EXISTS" in sql and "f.contributor = 'finance'" in sql
    assert "endpoint\\_custody:%" in sql
