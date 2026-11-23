"""Dark QA inbox custody on the real migrated schema, without service activation.

These are owner-side storage tests. They do not establish relay authentication.
"""

import asyncio
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

from butlers.core.qa.dashboard_inbox import (
    DashboardIdentity,
    DashboardInbox,
    DashboardReport,
    InboxRefused,
)
from butlers.core.qa.findings import insert_finding
from butlers.core.qa.sources.butler_reports import ButlerReportsSource
from tests.polling import poll_until

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


def report(**changes):
    return DashboardReport(
        exception_type="ValueError",
        call_site="qa.test:report",
        severity=2,
        event_summary="synthetic failure",
        context="synthetic context",
        **changes,
    )


@pytest.fixture
async def inbox(db):
    pool = await db("core")
    enabled = [True]
    return pool, DashboardInbox(pool, enabled=lambda: enabled[0], repo_root=Path.cwd()), enabled


async def test_duplicate_acceptance_changed_content_and_disabled_lookup(inbox):
    pool, store, enabled = inbox
    identity = DashboardIdentity(uuid4(), uuid4(), "same-key")
    for bad in (
        (None, identity.terminal_effect_id, "same-key"),
        (identity.terminal_action_id, None, "same-key"),
        (identity.terminal_action_id, identity.terminal_effect_id, ""),
        (identity.terminal_action_id, identity.terminal_effect_id, "\x00"),
    ):
        with pytest.raises(InboxRefused, match="dashboard_identity_invalid"):
            DashboardIdentity(*bad)
    for bad_report in ({"severity": True}, {"event_summary": "\x00"}, {"call_site": ""}):
        with pytest.raises(InboxRefused, match="dashboard_report_invalid"):
            replace(report(), **bad_report)
    assert await pool.fetchval("SELECT count(*) FROM public.qa_dashboard_reports") == 0
    receipts = await asyncio.gather(*(store.accept(identity, report()) for _ in range(8)))
    assert all(value == receipts[0] for value in receipts)
    receipt = receipts[0]
    assert receipt["accepted"] and receipt["delivery"] == "dashboard_durable"
    assert "finding_id" not in receipt["receipt"]
    assert await pool.fetchval("SELECT count(*) FROM public.qa_dashboard_reports") == 1
    for changed_identity, changed_report in (
        (replace(identity, terminal_effect_idempotency_key="other"), report()),
        (identity, replace(report(), context="changed")),
    ):
        with pytest.raises(InboxRefused, match="dashboard_identity_conflict"):
            await store.accept(changed_identity, changed_report)
    enabled[0] = False
    with pytest.raises(InboxRefused, match="butler_reports_disabled"):
        await store.accept(identity, report())
    assert await store.claim() == []
    found = await store.receipt(identity.terminal_action_id, identity.terminal_effect_id)
    assert found == {"status": "found", "receipt": receipt["receipt"]}
    assert await store.receipt(uuid4(), uuid4()) == {"status": "not_found"}
    enabled[0] = True
    assert len(await store.claim()) == 1


async def test_crash_reclaim_fences_atomic_finding_and_retained_receipt(inbox):
    pool, store, enabled = inbox
    identity = DashboardIdentity(uuid4(), uuid4(), "reclaim")
    await store.accept(identity, report())
    old = (await store.claim(lease_seconds=1))[0]
    assert await store.claim() == []
    claimed_receipt = await store.receipt(identity.terminal_action_id, identity.terminal_effect_id)
    assert claimed_receipt["receipt"]["inbox_state"] == "claimed"
    assert "finding_id" not in claimed_receipt["receipt"]

    async def expired():
        return await pool.fetchval(
            "SELECT lease_until<=clock_timestamp() FROM public.qa_dashboard_reports WHERE id=$1",
            old.dashboard_claim.inbox_id,
        )

    async with asyncio.timeout(5):
        await poll_until(expired, interval_s=0.02)
    new = (await store.claim())[0]
    assert new.dashboard_claim.generation == old.dashboard_claim.generation + 1
    patrol = await pool.fetchval(
        "INSERT INTO public.qa_patrols(status,log_lookback_minutes) VALUES('running',15) RETURNING id"
    )
    enabled[0] = False
    with pytest.raises(InboxRefused, match="butler_reports_disabled"):
        await insert_finding(pool, patrol, new, None)
    assert await pool.fetchval("SELECT count(*) FROM public.qa_findings") == 0
    enabled[0] = True
    with pytest.raises(InboxRefused, match="dashboard_claim_stale"):
        await insert_finding(pool, patrol, old, None)
    assert await pool.fetchval("SELECT count(*) FROM public.qa_findings") == 0
    # A crash after INSERT but before acknowledgement rolls back both effects.
    async with pool.acquire() as conn:
        with pytest.raises(RuntimeError, match="synthetic crash"):
            async with conn.transaction():
                await store.persist_finding(conn, patrol, new, None, None)
                raise RuntimeError("synthetic crash")
    assert await pool.fetchval("SELECT count(*) FROM public.qa_findings") == 0
    winners = await asyncio.gather(*(insert_finding(pool, patrol, new, None) for _ in range(4)))
    finding_id = winners[0]
    assert all(value == finding_id for value in winners)
    assert await insert_finding(pool, patrol, new, None) == finding_id
    assert await pool.fetchval("SELECT count(*) FROM public.qa_findings") == 1
    enabled[0] = False
    receipt = await store.receipt(identity.terminal_action_id, identity.terminal_effect_id)
    assert receipt["receipt"]["inbox_state"] == "acknowledged"
    assert receipt["receipt"]["finding_id"] == str(finding_id)
    assert "synthetic" not in str(receipt)
    assert await store.claim() == []


async def test_custody_immutability_role_isolation_and_schema_rollback(postgres_container):
    from alembic import command
    from butlers.migrations import _build_alembic_config
    from butlers.testing.db import migrated_url, open_pool

    url = await asyncio.to_thread(migrated_url, postgres_container, "core")
    pool = await open_pool(url)
    try:
        store = DashboardInbox(pool, enabled=lambda: True, repo_root=Path.cwd())
        identity = DashboardIdentity(uuid4(), uuid4(), "retained")
        accepted = await store.accept(identity, report())
        async with pool.acquire() as conn:
            for sql in (
                "UPDATE public.qa_dashboard_reports SET payload='{}'::jsonb",
                "DELETE FROM public.qa_dashboard_reports",
                "TRUNCATE public.qa_dashboard_reports",
            ):
                with pytest.raises(asyncpg.CheckViolationError):
                    await conn.execute(sql)
            await conn.execute("SET ROLE butler_health_rw")
            try:
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await conn.fetch("SELECT * FROM public.qa_dashboard_reports")
            finally:
                await conn.execute("RESET ROLE")

        def rollback_and_upgrade():
            config = _build_alembic_config(url, ["core"])
            command.downgrade(config, "core_362")
            command.upgrade(config, "core@head")

        await asyncio.to_thread(rollback_and_upgrade)
        assert await store.receipt(identity.terminal_action_id, identity.terminal_effect_id) == {
            "status": "found",
            "receipt": accepted["receipt"],
        }
        async with pool.acquire() as conn:
            with pytest.raises(asyncpg.CheckViolationError):
                await conn.execute("DELETE FROM public.qa_dashboard_reports")
    finally:
        await pool.close()


async def test_discovery_preserves_ordinary_buffer_and_canonical_payload(inbox):
    pool, store, enabled = inbox
    identity = DashboardIdentity(uuid4(), uuid4(), "canonical")
    await store.accept(identity, report())
    source = ButlerReportsSource(dashboard_inbox=store)
    await source.accept("ordinary", "ValueError", "ordinary:call", 3, "ordinary", "health")
    findings = await source.discover(15)
    assert len(findings) == 2 and source.buffer_size == 0
    durable = next(value for value in findings if value.dashboard_claim)
    assert durable.source_type == "butler_reports" and durable.source_butler == "switchboard"
    assert durable.fingerprint != "ordinary"
    assert await source.discover(15) == []
    # Store failure is unknown, never proof that a receipt is missing.
    await pool.close()
    assert await store.receipt(identity.terminal_action_id, identity.terminal_effect_id) == {
        "status": "unavailable"
    }
