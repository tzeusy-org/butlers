"""Finance suppresses its large-transaction flag only on a positive owner-watch answer.

The Switchboard is the watch trust root: Finance trusts a true answer and fails open on
anything else (bu-4c7nvp.1.5 S7). Synthetic merchants and amounts only.
"""

from __future__ import annotations

import asyncio
import shutil
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from butlers.roster_imports import roster_module

alerts = roster_module("finance", "tools/alerts")

_docker = pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available")


class Executor:
    """A pool-like fake that answers one cover read."""

    def __init__(self, answer=None, *, error=None, delay=0.0):
        self.answer, self.error, self.delay, self.calls = answer, error, delay, []

    async def fetchval(self, sql, *args):
        self.calls.append(args)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return self.answer


@pytest.mark.parametrize(
    ("answer", "expected"),
    [(True, True), (False, False), (None, False), ("t", False), (1, False)],
    ids=["true", "false", "null", "string", "int"],
)
async def test_only_a_boolean_true_suppresses(answer, expected):
    assert await alerts.owner_watch_covers(Executor(answer), "USD", Decimal("900")) is expected


@pytest.mark.parametrize(
    "error",
    [RuntimeError("boom"), PermissionError("permission denied for function"), OSError("gone")],
)
async def test_any_error_fails_open_and_never_raises(error):
    assert await alerts.owner_watch_covers(Executor(error=error), "USD", 900) is False


async def test_a_slow_answer_times_out_and_fails_open(monkeypatch):
    monkeypatch.setattr(alerts, "_COVER_TIMEOUT_S", 0.05)
    assert await alerts.owner_watch_covers(Executor(True, delay=1.0), "USD", 900) is False


async def test_a_bad_amount_fails_open():
    assert await alerts.owner_watch_covers(Executor(True), "USD", "not a number") is False


async def test_the_absolute_amount_is_asked_about():
    executor = Executor(True)
    await alerts.owner_watch_covers(executor, "usd", Decimal("-900.50"))
    assert executor.calls == [("usd", Decimal("900.50"))]


async def test_a_failure_logs_one_line_and_counts_once(monkeypatch, caplog):
    counted = []
    monkeypatch.setattr(alerts, "_count_cover_failure", lambda: counted.append(1))
    with caplog.at_level("WARNING"):
        await alerts.owner_watch_covers(Executor(error=RuntimeError("boom")), "USD", 900)
    assert counted == [1]
    assert [r.getMessage() for r in caplog.records].count(
        "owner_watch_cover_unavailable error=RuntimeError; legacy alert fires"
    ) == 1


async def test_a_false_answer_is_not_a_failure(monkeypatch):
    counted = []
    monkeypatch.setattr(alerts, "_count_cover_failure", lambda: counted.append(1))
    await alerts.owner_watch_covers(Executor(False), "USD", 900)
    assert counted == []


def _alert(kind, threshold=None, currency="USD", enabled=True, fact_id="f1"):
    return {
        "type": kind,
        "threshold": threshold,
        "currency": currency,
        "enabled": enabled,
        "fact_id": fact_id,
    }


async def _proposals(monkeypatch, *configured):
    async def listing(pool):
        return {"alerts": list(configured), "total": len(configured)}

    monkeypatch.setattr(alerts, "alert_list", listing)
    return (await alerts.alert_watch_proposals(None))["proposals"]


async def test_an_enabled_large_alert_proposes_one_matching_read_check(monkeypatch):
    [proposal] = await _proposals(monkeypatch, _alert("large_transaction", 500.0, "eur"))
    assert proposal["status"] == "proposed"
    assert proposal["watch"] == {
        "tag": "read_check",
        "capability": "finance.transaction_amount.eur",
        "field": "amount",
        "op": "gt",
        "value": 500.0,
    }
    assert len(proposal["digest"]) == 64


async def test_the_digest_is_stable_so_parking_twice_is_idempotent(monkeypatch):
    first = await _proposals(monkeypatch, _alert("large_transaction", 500, "USD"))
    again = await _proposals(monkeypatch, _alert("large_transaction", 500, "usd", fact_id="f2"))
    assert first[0]["digest"] == again[0]["digest"]
    other = await _proposals(monkeypatch, _alert("large_transaction", 501, "USD"))
    assert other[0]["digest"] != first[0]["digest"]


@pytest.mark.parametrize(
    "config",
    [
        _alert("large_transaction", 500, enabled=False),
        _alert("large_transaction", None),
        _alert("large_transaction", True),
        _alert("large_transaction", 500, currency="US"),
        _alert("large_transaction", 500, currency="12$"),
    ],
    ids=["disabled", "no threshold", "bool threshold", "short currency", "bad currency"],
)
async def test_a_disabled_or_unusable_alert_proposes_nothing(monkeypatch, config):
    [proposal] = await _proposals(monkeypatch, config)
    assert proposal["status"] == "not_proposed" and proposal["watch"] is None


@pytest.mark.parametrize("kind", ["price_change", "budget_exceeded", "new_merchant"])
async def test_the_other_alert_types_are_superseded_with_no_watch(monkeypatch, kind):
    [proposal] = await _proposals(monkeypatch, _alert(kind, None))
    assert proposal["status"] == "superseded" and proposal["watch"] is None
    assert proposal["digest"] is None


async def test_the_proposal_is_a_pure_read(monkeypatch):
    class Pool:
        async def fetch(self, sql, *args):
            assert sql.lstrip().upper().startswith("SELECT")
            return []

    assert await alerts.alert_watch_proposals(Pool()) == {"proposals": [], "total": 0}


# ---------------------------------------------------------------------------
# The real record paths, with a stub answer function standing in for core_380.
# ---------------------------------------------------------------------------

_DROP = "DROP FUNCTION IF EXISTS public.owner_watch_covers_large_transaction(text, numeric)"
_STUB = """
CREATE FUNCTION public.owner_watch_covers_large_transaction(p_currency text, p_amount numeric)
RETURNS boolean LANGUAGE plpgsql AS $$ BEGIN {body} END $$
"""


@pytest.fixture
async def finance_pool(db):
    return await db("core", "memory", "finance")


async def _answer(pool, body):
    await pool.execute(_DROP)
    await pool.execute(_STUB.format(body=body))


async def _record(pool, merchant, amount, **kw):
    from butlers.tools.finance.transactions import record_transaction

    return await record_transaction(
        pool,
        posted_at=datetime.now(UTC),
        merchant=merchant,
        amount=amount,
        currency="USD",
        category="misc",
        **kw,
    )


@_docker
@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
class TestSuppressionOnTheRecordPaths:
    async def _configure(self, pool):
        await alerts.alert_configure(pool, alert_type="large_transaction", threshold=500.0)

    async def test_a_true_answer_suppresses_the_flag_but_still_records(self, finance_pool):
        await self._configure(finance_pool)
        await _answer(finance_pool, "RETURN true;")
        result = await _record(finance_pool, "Big Shop", -900.0)
        assert "large_transaction_alert" not in result and result["id"]

    @pytest.mark.parametrize(
        "body",
        [
            "RETURN false;",
            "RETURN NULL;",
            "RAISE EXCEPTION 'boom';",
            "RAISE EXCEPTION 'denied' USING ERRCODE = '42501';",
            "PERFORM pg_sleep(30); RETURN true;",
        ],
        ids=["false", "null", "raises", "denied", "slow"],
    )
    async def test_anything_but_true_keeps_the_flag(self, finance_pool, body, monkeypatch):
        monkeypatch.setattr(alerts, "_COVER_TIMEOUT_S", 0.5)
        await self._configure(finance_pool)
        await _answer(finance_pool, body)
        result = await _record(finance_pool, "Big Shop", -900.0)
        assert result["large_transaction_alert"]["amount"] == 900.0

    async def test_a_missing_function_keeps_the_flag(self, finance_pool):
        await self._configure(finance_pool)
        await finance_pool.execute(_DROP)
        result = await _record(finance_pool, "Big Shop", -900.0)
        assert "large_transaction_alert" in result

    async def test_a_failed_read_inside_a_transaction_does_not_poison_it(self, finance_pool):
        await self._configure(finance_pool)
        await _answer(finance_pool, "RAISE EXCEPTION 'boom';")
        async with finance_pool.acquire() as connection:
            async with connection.transaction():
                from butlers.tools.finance.transactions import _record_transaction

                result = await _record_transaction(
                    finance_pool,
                    datetime.now(UTC),
                    "Big Shop",
                    -900.0,
                    "USD",
                    "misc",
                    connection=connection,
                )
                assert "large_transaction_alert" in result
                assert await connection.fetchval("SELECT 1") == 1  # still usable

    async def test_it_is_asked_per_transaction_so_only_the_uncovered_one_flags(self, finance_pool):
        from butlers.tools.finance.transactions import bulk_record_transactions

        await self._configure(finance_pool)
        await _answer(finance_pool, "RETURN p_amount >= 800;")
        now = datetime.now(UTC).isoformat()
        result = await bulk_record_transactions(
            finance_pool,
            transactions=[
                {"posted_at": now, "merchant": "Covered", "amount": "-900.00", "category": "misc"},
                {"posted_at": now, "merchant": "Open", "amount": "-600.00", "category": "misc"},
            ],
        )
        flagged = result["large_transaction_alerts"]
        assert [(f["index"], f["merchant"]) for f in flagged] == [(1, "Open")]

    async def test_a_below_threshold_amount_never_asks(self, finance_pool):
        await self._configure(finance_pool)
        await _answer(finance_pool, "RAISE EXCEPTION 'must not be asked';")
        result = await _record(finance_pool, "Cafe", -12.0)
        assert "large_transaction_alert" not in result
