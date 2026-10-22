"""Finance asks the owner once about an open loan claim against a memorial person (bu-q7vx1q.45).

Unit tests drive the producer with the claim read, the ledger and the broker mocked. The
DB-backed class runs the real read and the sweep against a migrated database (CI-only).
All people and amounts are synthetic.
"""

from __future__ import annotations

import json
import shutil
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest

import butlers.jobs._roster.finance_jobs as finance_jobs
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from butlers.tools.finance.memorial_claims import list_memorial_open_claims

_SOURCE = "finance:memorial-claim"
_CLAIM = {
    "id": uuid.UUID("00000000-0000-0000-0000-0000000000a1"),
    "asserted_by": "relationship",
    "claim_key": "loan:sample",
    "direction": "inbound",
    "counterparty_name": "Sample Person",
}


def _transition(kind: str, claim: dict = _CLAIM):
    fingerprint = finance_jobs.owner_condition_fingerprint(
        _SOURCE, 1, {"asserted_by": claim["asserted_by"], "claim_key": claim["claim_key"]}
    )
    return SimpleNamespace(
        condition_id=uuid.UUID("00000000-0000-0000-0000-0000000000c1"),
        fingerprint=fingerprint,
        transition=kind,
    )


class TestMemorialClaimQuestionProducer:
    async def _run(self, *, claims, transitions=(), read_error=None):
        read = AsyncMock(side_effect=read_error) if read_error else AsyncMock(return_value=claims)
        reconcile = AsyncMock(return_value=list(transitions))
        propose = AsyncMock(return_value="accepted")
        with (
            patch.object(finance_jobs, "list_memorial_open_claims", read),
            patch.object(finance_jobs, "reconcile_owner_condition", reconcile),
            patch.object(finance_jobs, "_propose", propose),
        ):
            result = await finance_jobs._ask_about_memorial_claims(MagicMock())
        return result, reconcile, propose

    async def test_an_opened_condition_asks_exactly_once_with_a_content_free_notice(self):
        result, reconcile, propose = await self._run(
            claims=[_CLAIM], transitions=[_transition("opened")]
        )
        assert result == {"status": "ok", "opened": 1}
        propose.assert_awaited_once()
        kwargs = propose.await_args.kwargs
        assert kwargs["category"] == "memorial-claim"
        assert kwargs["dedup_key"] == "finance:memorial-claim:00000000-0000-0000-0000-0000000000c1"
        assert "Sample Person owes you" in kwargs["message"]
        assert "Retract it or keep it?" in kwargs["message"]
        assert not any(ch.isdigit() for ch in kwargs["message"])
        assert kwargs["premise"]["source"] == _SOURCE
        observed = reconcile.await_args.kwargs
        assert observed["snapshot_complete"] is True
        assert [o.metadata for o in observed["observations"]] == [{"claim_id": str(_CLAIM["id"])}]

    @pytest.mark.parametrize("kind", ["confirmed", "no_change", "escalation_due", "resolved"])
    async def test_only_the_opened_transition_asks(self, kind):
        result, _, propose = await self._run(claims=[_CLAIM], transitions=[_transition(kind)])
        assert result["opened"] == 0
        propose.assert_not_awaited()

    async def test_the_payable_direction_is_named_from_the_owners_side(self):
        payable = {**_CLAIM, "direction": "outbound"}
        _, _, propose = await self._run(
            claims=[payable], transitions=[_transition("opened", payable)]
        )
        assert "you owe Sample Person" in propose.await_args.kwargs["message"]

    async def test_no_memorial_claims_reconciles_an_empty_snapshot_and_asks_nothing(self):
        result, reconcile, propose = await self._run(claims=[])
        assert result["opened"] == 0
        assert reconcile.await_args.kwargs["observations"] == []
        propose.assert_not_awaited()

    async def test_a_superseded_claim_keeps_its_identity_and_is_not_asked_again(self):
        amended = {**_CLAIM, "id": uuid.UUID("00000000-0000-0000-0000-0000000000a2")}
        _, first, _ = await self._run(claims=[_CLAIM], transitions=[_transition("opened")])
        _, second, _ = await self._run(claims=[amended], transitions=[_transition("confirmed")])
        before = first.await_args.kwargs["observations"]
        after = second.await_args.kwargs["observations"]
        assert [o.fingerprint for o in before] == [o.fingerprint for o in after]
        assert after[0].metadata == {"claim_id": str(amended["id"])}

    async def test_an_unknown_read_changes_nothing(self):
        result, reconcile, propose = await self._run(claims=[], read_error=RuntimeError("down"))
        assert result["status"] == "degraded"
        reconcile.assert_not_awaited()
        propose.assert_not_awaited()


class TestSweepGainsTheQuestionStep:
    async def _sweep(self, reconciled):
        ask = AsyncMock(return_value={"status": "ok", "opened": 0})
        scoped = MagicMock()
        scoped.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        scoped.return_value.__aexit__ = AsyncMock(return_value=False)
        with (
            patch.object(finance_jobs, "_finance_scoped_connection", scoped),
            patch.object(finance_jobs, "reconcile_cost_claims", AsyncMock(return_value=reconciled)),
            patch.object(finance_jobs, "_ask_about_memorial_claims", ask),
        ):
            result = await finance_jobs.run_cost_claim_reconciliation_sweep(MagicMock())
        return result, ask

    async def test_a_completed_sweep_runs_the_step_and_keeps_its_own_result(self):
        result, ask = await self._sweep({"acquired": True, "processed": 2, "outcomes": {}})
        ask.assert_awaited_once()
        assert result["processed"] == 2
        assert result["memorial_claims"] == {"status": "ok", "opened": 0}

    async def test_a_sweep_that_lost_the_lock_skips_the_step(self):
        result, ask = await self._sweep({"acquired": False, "processed": 0, "outcomes": {}})
        ask.assert_not_awaited()
        assert "memorial_claims" not in result


pytestmark_db = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]


@pytest.fixture
def db_url(postgres_container) -> str:
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "finance"],
        schemas={"finance": "finance"},
    )


async def _entity(conn: asyncpg.Connection, name: str, posture: str) -> uuid.UUID:
    entity_id = await conn.fetchval(
        "INSERT INTO public.entities (canonical_name, entity_type) VALUES ($1, 'person') "
        "RETURNING id",
        name,
    )
    await conn.execute("UPDATE public.entities SET posture = $2 WHERE id = $1", entity_id, posture)
    return entity_id


async def _finance_pool_init(conn: asyncpg.Connection) -> None:
    """Run as the finance role, as the daemon does, and accept Python objects and JSON text.

    The owner-condition insert passes a dict, while ``cost_claims.record_resolution`` hands
    asyncpg a ``json.dumps`` string; the shared binary codec would encode that a second time
    and trip ``match_refs`` being an array.
    """
    await conn.execute("SET ROLE butler_finance_rw")
    await conn.set_type_codec(
        "jsonb",
        encoder=lambda value: value if isinstance(value, str) else json.dumps(value),
        decoder=json.loads,
        schema="pg_catalog",
        format="text",
    )


async def _update_claim(db_url: str, sql: str, *args: object) -> None:
    """Run an UPDATE on cost_claims as the asserting role; row-level security hides it otherwise."""
    conn = await asyncpg.connect(db_url)
    try:
        await conn.execute("SET ROLE butler_relationship_rw")
        await conn.execute(sql, *args)
    finally:
        await conn.close()


async def _claim_again(db_url: str, entity_id: uuid.UUID, *, key: str, amount: int) -> uuid.UUID:
    return await _claim(db_url, entity_id, key=key, amount=amount)


async def _claim(
    db_url: str,
    entity_id: uuid.UUID,
    *,
    key: str,
    kind: str = "receivable",
    amount: int = 25,
) -> uuid.UUID:
    conn = await asyncpg.connect(db_url)
    try:
        await conn.execute("SET ROLE butler_relationship_rw")
        return await conn.fetchval(
            """
            INSERT INTO public.cost_claims
                (claim_key, asserted_by, kind, direction, amount, currency,
                 counterparty_entity_id, description)
            VALUES ($1, 'relationship', $2, $3, $5, 'SGD', $4, 'Synthetic loan')
            RETURNING id
            """,
            key,
            kind,
            "inbound" if kind == "receivable" else "outbound",
            entity_id,
            amount,
        )
    finally:
        await conn.close()


class TestMemorialClaimReadAndSweep:
    pytestmark = pytestmark_db

    async def test_only_live_unsettled_loan_claims_to_a_memorial_person_are_listed(self, db_url):
        conn = await asyncpg.connect(db_url)
        try:
            memorial = await _entity(conn, "Memorial Sample", "memorial")
            others = {p: await _entity(conn, f"{p} Sample", p) for p in ("active", "quiet")}
            listed = await _claim(db_url, memorial, key="listed")
            payable = await _claim(db_url, memorial, key="payable", kind="payable")
            await _claim(db_url, memorial, key="shared", kind="shared_expense")
            await _claim(db_url, others["active"], key="active-person")
            await _claim(db_url, others["quiet"], key="quiet-person")
            retracted = await _claim(db_url, memorial, key="retracted")
            settled = await _claim(db_url, memorial, key="settled")
            await _update_claim(
                db_url,
                "UPDATE public.cost_claims SET retracted_at = now(), retraction_reason = 'x' "
                "WHERE id = $1",
                retracted,
            )
            await conn.execute(
                "INSERT INTO public.cost_claim_resolutions (claim_id, state) "
                "VALUES ($1, 'settled')",
                settled,
            )
            ids = {row["id"] for row in await list_memorial_open_claims(conn)}
        finally:
            await conn.close()
        assert ids == {listed, payable}

    async def test_an_amended_claim_between_sweeps_is_not_asked_again(self, db_url):
        admin = await asyncpg.connect(db_url)
        pool = await asyncpg.create_pool(db_url, min_size=1, max_size=2, init=_finance_pool_init)
        try:
            await admin.execute(
                "INSERT INTO insight_settings (id, verbosity) VALUES (1, 'normal') "
                "ON CONFLICT (id) DO NOTHING"
            )
            person = await _entity(admin, "Memorial Sample", "memorial")
            old_id = await _claim(db_url, person, key="loan")
            await finance_jobs.run_cost_claim_reconciliation_sweep(pool)

            # An amendment supersedes the row and re-asserts it under a new id, same claim_key.
            await _update_claim(
                db_url, "UPDATE public.cost_claims SET superseded_at = now() WHERE id = $1", old_id
            )
            await _claim_again(db_url, person, key="loan", amount=10)
            await finance_jobs.run_cost_claim_reconciliation_sweep(pool)

            conditions = await admin.fetch(
                "SELECT state, episode FROM public.owner_conditions WHERE source = $1", _SOURCE
            )
            assert len(conditions) == 1
            assert conditions[0]["state"] != "resolved" and conditions[0]["episode"] == 1
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM insight_candidates WHERE category = 'memorial-claim'"
                )
                == 1
            )
        finally:
            await pool.close()
            await admin.close()

    async def test_the_sweep_asks_once_leaves_the_claim_alone_and_resolves_on_active(self, db_url):
        admin = await asyncpg.connect(db_url)
        pool = await asyncpg.create_pool(db_url, min_size=1, max_size=2, init=_finance_pool_init)
        try:
            await admin.execute(
                "INSERT INTO insight_settings (id, verbosity) VALUES (1, 'normal') "
                "ON CONFLICT (id) DO NOTHING"
            )
            person = await _entity(admin, "Memorial Sample", "memorial")
            claim_id = await _claim(db_url, person, key="loan")
            before = await admin.fetch("SELECT * FROM public.cost_claims ORDER BY id")

            first = await finance_jobs.run_cost_claim_reconciliation_sweep(pool)
            second = await finance_jobs.run_cost_claim_reconciliation_sweep(pool)

            assert first["memorial_claims"]["opened"] == 1
            assert second["memorial_claims"]["opened"] == 0
            candidates = await admin.fetch(
                "SELECT message FROM insight_candidates WHERE category = 'memorial-claim'"
            )
            assert len(candidates) == 1
            assert "25" not in candidates[0]["message"]
            assert await admin.fetch("SELECT * FROM public.cost_claims ORDER BY id") == before
            conditions = await admin.fetch(
                "SELECT state FROM public.owner_conditions WHERE source = $1", _SOURCE
            )
            assert len(conditions) == 1 and conditions[0]["state"] != "resolved"

            await admin.execute(
                "UPDATE public.entities SET posture = 'active' WHERE id = $1", person
            )
            await finance_jobs.run_cost_claim_reconciliation_sweep(pool)
            state = await admin.fetchval(
                "SELECT state FROM public.owner_conditions WHERE source = $1", _SOURCE
            )
            assert state == "resolved"
            assert await admin.fetchval(
                "SELECT retracted_at IS NULL FROM public.cost_claims WHERE id = $1", claim_id
            )
        finally:
            await pool.close()
            await admin.close()
