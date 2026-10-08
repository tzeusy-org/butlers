"""Existing ledger node's real-PG nightly lifecycle extension; no provider calls."""

from __future__ import annotations

import asyncio
import importlib
import os
import uuid
from datetime import UTC, datetime

import asyncpg

from butlers.db import register_jsonb_codec
from butlers.jobs import nightly_assurance as job
from butlers.nightly_assurance import REPOSITORY, WORKFLOW, atomic_json, digest


async def exercise_nightly_runtime_boundary(admin, db_url, tmp_path, monkeypatch):
    async def setup(connection):
        await connection.execute("SET ROLE butler_switchboard_rw")
        await connection.execute("SET search_path TO switchboard, public")

    runtime = await asyncpg.create_pool(
        db_url, min_size=1, max_size=3, init=register_jsonb_codec, setup=setup
    )
    path = tmp_path / "incidents.json"
    monkeypatch.setenv("BUTLERS_NIGHTLY_INCIDENT_EXPORT", str(path))
    calls = []

    async def recipient(pool):
        async with pool.acquire() as connection:
            assert await connection.fetchval("SELECT current_user") == "butler_switchboard_rw"
        return "synthetic-disposable-owner"

    async def transport(pool, **kwargs):
        assert kwargs["source_butler"] == "switchboard"
        calls.append(kwargs)
        return {"status": "sent", "notification_id": str(uuid.uuid4())}

    monkeypatch.setattr(job, "resolve_owner_telegram_recipient", recipient)
    # The package exports a function named deliver; patch the actual module
    # resolved by production's local function import, not that package alias.
    deliver = importlib.import_module("butlers.tools.switchboard.notification.deliver")
    monkeypatch.setattr(deliver, "deliver", transport)
    day = datetime(2026, 10, 4, 12, tzinfo=UTC)  # 20:00 Singapore, ordinary gate open
    quiet = datetime(2026, 10, 4, 16, tzinfo=UTC)  # 00:00 Singapore, seeded policy closes

    def export(name, now):
        item = {
            "incident_id": "bu-disposable",
            "episode_id": digest(name),
            "status": "open",
            "issue": 51,
            "run_id": 104,
            "attempt": 1,
            "head": "c" * 40,
            "night": "2026-10-04",
            "failure_digest": digest(["synthetic-evidence"]),
        }
        atomic_json(
            path,
            {
                "version": 1,
                "repository": REPOSITORY,
                "workflow": WORKFLOW,
                "as_of": now.isoformat(),
                "incidents": [item],
            },
        )
        return item, job._target(item)

    try:
        for _ in range(3):
            async with runtime.acquire() as connection:
                assert await connection.fetchval("SELECT current_user") == "butler_switchboard_rw"
                assert (await connection.fetchval("SELECT current_schemas(false)")) == [
                    "switchboard",
                    "public",
                ]
                assert await connection.fetchval("SELECT '{\"fixture\":true}'::jsonb") == {
                    "fixture": True
                }
        item, target = export("first", day)
        left, right = await asyncio.gather(
            job.run_nightly_assurance(runtime, now=day), job.run_nightly_assurance(runtime, now=day)
        )
        assert left["available"] and right["available"]
        assert len(calls) == 1
        again = await job.run_nightly_assurance(runtime, now=day)
        assert again["incidents"][0]["state"] == "ledgered" and len(calls) == 1
        rows = await admin.fetch(
            "SELECT source,outcome,metadata FROM public.attention_ledger WHERE dedup_key=$1", target
        )
        assert (
            len(rows) == 1 and rows[0]["source"] == "notify" and rows[0]["outcome"] == "delivered"
        )
        # Actor strings do not bypass current-principal admission.
        async with runtime.acquire() as connection, connection.transaction():
            await connection.execute("SET LOCAL ROLE butler_health_rw")
            try:
                await job._lock(connection, "nightly:wrong-role")
            except job.EvidenceUnavailable as exception:
                assert str(exception) == "wrong-runtime-role"
            else:
                raise AssertionError("peer runtime role admitted the owning marker")

        item, target = export("quiet", quiet)
        suppressed = await job.run_nightly_assurance(runtime, now=quiet)
        assert suppressed["incidents"][0]["state"] == "ledgered" and len(calls) == 1
        row = await admin.fetchrow(
            "SELECT outcome,reason FROM public.attention_ledger WHERE dedup_key=$1", target
        )
        assert dict(row) == {"outcome": "suppressed", "reason": "quiet_hours"}

        # Real table-owner fixture changes one disposable object's ACL only.
        # No role/database/schema privilege is added. Restore even on failure.
        item, target = export("ledger-loss", day)
        await admin.execute("REVOKE INSERT ON public.attention_ledger FROM butler_switchboard_rw")
        try:
            async with runtime.acquire() as connection:
                assert not await connection.fetchval(
                    "SELECT has_table_privilege(current_user,'public.attention_ledger','INSERT')"
                )
            pending = await job.run_nightly_assurance(runtime, now=day)
            assert pending["incidents"][0]["state"] == "pending" and len(calls) == 2
            async with runtime.acquire() as connection:
                marker = await job._latest(connection, target)
                assert marker["state"] == "known" and marker["outcome"] == "delivered"
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM public.attention_ledger WHERE dedup_key=$1", target
                )
                == 0
            )
        finally:
            await admin.execute("GRANT INSERT ON public.attention_ledger TO butler_switchboard_rw")
        restored = await job.run_nightly_assurance(runtime, now=day)
        assert restored["incidents"][0]["state"] == "ledgered" and len(calls) == 2
        assert (
            await admin.fetchval(
                "SELECT count(*) FROM public.attention_ledger WHERE dedup_key=$1", target
            )
            == 1
        )

        if os.environ.get("BUTLERS_NIGHTLY_CLOCK_VARIANT") == "folded-hour":
            from zoneinfo import ZoneInfo

            wall = datetime.now(UTC)
            assert wall.astimezone(ZoneInfo("Asia/Singapore")).hour in {23, 0, 1, 2, 3, 4, 5, 6, 7}
            item, target = export("actual-folded-hour", wall)
            before_calls = len(calls)
            observed = await job.run_nightly_assurance(
                runtime
            )  # actual process clock, no now injection
            assert observed["incidents"][0]["state"] == "ledgered" and len(calls) == before_calls
            row = await admin.fetchrow(
                "SELECT outcome,reason FROM public.attention_ledger WHERE dedup_key=$1", target
            )
            assert dict(row) == {"outcome": "suppressed", "reason": "quiet_hours"}

        async def lose_ack(pool, **kwargs):
            calls.append(kwargs)
            raise TimeoutError("synthetic-unknown-ack")

        monkeypatch.setattr(deliver, "deliver", lose_ack)
        item, target = export("unknown-ack", day)
        uncertain = await job.run_nightly_assurance(runtime, now=day)
        assert uncertain["incidents"][0]["state"] == "uncertain" and len(calls) == 3
        replay = await job.run_nightly_assurance(runtime, now=day)
        assert replay["incidents"][0]["state"] == "pending" and len(calls) == 3
        assert (
            await admin.fetchval(
                "SELECT count(*) FROM public.attention_ledger WHERE dedup_key=$1", target
            )
            == 0
        )
        async with runtime.acquire() as connection:
            assert (await job._latest(connection, target))["state"] == "uncertain"
        path.unlink()
        absent = await job.run_nightly_assurance(runtime, now=day)
        assert absent == {"available": False, "reason": "nightly_export_unavailable"}
    finally:
        await runtime.close()
