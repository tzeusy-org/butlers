"""Real-Postgres: Finance contributes the exact held window to the custody case (S5e).

bu-s11n0s.1 S5e, REQ-endpoint-custody-holds-009. The trigger is the custody engine's own hold
evidence on an open ``endpoint_custody:<episode>`` case (as core_347 writes it); the job runs on
Finance's own pool and appends one idempotent ``finance``/``held_window`` row per hold. Docker.
"""

from __future__ import annotations

import asyncio
import importlib
import shutil
import sys
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest

from butlers.testing.db import open_pool

pytestmark = [pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available")]

HELD_FROM = datetime(2026, 11, 19, 9, 0, tzinfo=UTC)
NOW = HELD_FROM + timedelta(hours=6)


def _fj():
    importlib.import_module("butlers.jobs._roster.finance_jobs")
    return sys.modules["butlers.jobs._roster.finance_jobs"]


@pytest.fixture
def finance_clone(fresh_db):
    @asynccontextmanager
    async def _pool():
        url = await asyncio.to_thread(fresh_db, "core", "finance", schemas={"finance": "finance"})
        pool = await open_pool(url)
        try:
            yield pool
        finally:
            await pool.close()

    return _pool


async def _txn(pool, merchant: str, posted_at: datetime, direction: str = "debit") -> None:
    await pool.execute(
        "INSERT INTO finance.categories (name, display_name) VALUES ('general', 'general') "
        "ON CONFLICT (name) DO NOTHING"
    )
    await pool.execute(
        "INSERT INTO finance.transactions "
        "(merchant, amount, currency, direction, category, posted_at) "
        "VALUES ($1, 10, 'USD', $2, 'general', $3)",
        merchant,
        direction,
        posted_at,
    )


async def _hold_case(pool, *, state: str = "open") -> tuple[str, str]:
    """An episode case carrying the engine's hold evidence; returns (case id, hold id)."""
    case_id = await pool.fetchval(
        "INSERT INTO public.fleet_cases (correlation_key) VALUES ($1) RETURNING id::text",
        f"endpoint_custody:{uuid.uuid4()}",
    )
    hold_id = str(uuid.uuid4())
    await pool.execute(
        "INSERT INTO public.fleet_case_evidence "
        "(case_id, contributor, kind, ref, payload, contributed_at) "
        "VALUES ($1::uuid, 'endpoint_custody', 'hold', $2, $3, $4)",
        case_id,
        hold_id,
        {"target_kind": "has-email", "generation": 1, "command_id": str(uuid.uuid4())},
        HELD_FROM,
    )
    if state == "closed":
        await pool.execute(
            "UPDATE public.fleet_cases SET state = 'closed', outcome = 'custody_resolved', "
            "closed_at = now() WHERE id = $1::uuid",
            case_id,
        )
    return case_id, hold_id


async def _evidence(pool, case_id: str) -> list:
    return await pool.fetch(
        "SELECT contributor, kind, ref, payload FROM public.fleet_case_evidence "
        "WHERE case_id = $1::uuid AND contributor = 'finance'",
        case_id,
    )


async def test_only_the_exact_window_contributes_once_and_replay_is_the_same(finance_clone) -> None:
    async with finance_clone() as pool:
        await _txn(pool, "OLD SHOP", HELD_FROM - timedelta(days=2))
        await _txn(pool, "OLD SHOP", HELD_FROM + timedelta(hours=1))  # known merchant
        await _txn(pool, "NEW SHOP", HELD_FROM + timedelta(hours=2))  # first seen in window
        await _txn(pool, "REFUND", HELD_FROM + timedelta(hours=3), direction="credit")
        await _txn(pool, "LATER", NOW + timedelta(hours=1))  # after the window
        case_id, hold_id = await _hold_case(pool)

        first = await _fj().run_custody_held_window_evidence(pool, now=NOW)
        assert first == {"holds": 1, "contributed": 1, "errors": 0}
        (row,) = await _evidence(pool, case_id)
        assert (row["kind"], row["ref"]) == ("held_window", hold_id)
        payload = row["payload"]
        assert (payload["transactions"], payload["debits"], payload["credits"]) == (3, 2, 1)
        assert payload["first_seen_merchants"] == 2  # NEW SHOP and REFUND; OLD SHOP seen before
        assert payload["anomalies"] == "unknown"  # no baseline data in this clone
        assert payload["target_kind"] == "has-email" and payload["generation"] == 1

        again = await _fj().run_custody_held_window_evidence(pool, now=NOW + timedelta(hours=2))
        assert again == {"holds": 0, "contributed": 0, "errors": 0}
        assert [r["payload"] for r in await _evidence(pool, case_id)] == [payload]


async def test_a_released_hold_is_bounded_by_its_release_evidence(finance_clone) -> None:
    async with finance_clone() as pool:
        await _txn(pool, "BEFORE RELEASE", HELD_FROM + timedelta(hours=1))
        await _txn(pool, "AFTER RELEASE", HELD_FROM + timedelta(hours=4))
        case_id, hold_id = await _hold_case(pool)
        await pool.execute(
            "INSERT INTO public.fleet_case_evidence "
            "(case_id, contributor, kind, ref, payload, contributed_at) "
            "VALUES ($1::uuid, 'endpoint_custody', 'release', $2, '{}', $3)",
            case_id,
            f"{hold_id}:1",
            HELD_FROM + timedelta(hours=2),
        )
        await _fj().run_custody_held_window_evidence(pool, now=NOW)
        (row,) = await _evidence(pool, case_id)
        assert row["payload"]["transactions"] == 1
        assert row["payload"]["window_end"] == (HELD_FROM + timedelta(hours=2)).isoformat()


async def test_a_closed_case_and_an_unrelated_case_get_nothing(finance_clone) -> None:
    async with finance_clone() as pool:
        closed_id, _ = await _hold_case(pool, state="closed")
        other = await pool.fetchval(
            "INSERT INTO public.fleet_cases (correlation_key) VALUES ('something-else') "
            "RETURNING id::text"
        )
        await pool.execute(
            "INSERT INTO public.fleet_case_evidence (case_id, contributor, kind, ref, payload) "
            "VALUES ($1::uuid, 'endpoint_custody', 'hold', 'x', '{}')",
            other,
        )
        result = await _fj().run_custody_held_window_evidence(pool, now=NOW)
        assert result["holds"] == 0
        assert await _evidence(pool, closed_id) == [] and await _evidence(pool, other) == []
