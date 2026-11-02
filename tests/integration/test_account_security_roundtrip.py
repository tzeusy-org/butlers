"""Real-Postgres regression: account-security answer door (bu-q7vx1q.10).

Exercises ``record_security_answer`` against a migrated database: a recorded
``switchboard.security_event`` row, a ``no`` answer creating exactly one
``fleet_cases`` row with the provider recovery door as evidence, and a repeated
``no`` collapsing onto the same case. Sibling of
``test_fleet_case_contribution_roundtrip.py`` (same fixtures and cleanup rules).
"""

from __future__ import annotations

import shutil
import uuid
from collections.abc import AsyncIterator
from urllib.parse import urlparse

import asyncpg
import pytest

from butlers.account_security import classify_account_security
from butlers.core.account_security_events import (
    SECURITY_EVENT_TYPE,
    publish_security_event,
    record_security_answer,
)
from butlers.core.domain_events import record_event
from butlers.testing.db import open_pool
from butlers.testing.migration import migration_bootstrap_db_url

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]


@pytest.fixture
def db_url(fresh_db) -> str:
    """One clone shared by the owner pool and the Switchboard-role pool of a test."""
    return fresh_db("core")


@pytest.fixture
def bootstrap_url(postgres_container, db_url: str) -> str:
    return migration_bootstrap_db_url(postgres_container, urlparse(db_url).path[1:]).replace(
        "postgresql+psycopg2://", "postgresql://", 1
    )


@pytest.fixture
async def owner_pool(db_url: str) -> AsyncIterator[asyncpg.Pool]:
    p = await open_pool(db_url)
    yield p
    await p.close()


@pytest.fixture
async def switchboard_pool(db_url: str) -> AsyncIterator[asyncpg.Pool]:
    # fleet_cases INSERT is RLS-restricted to butler_switchboard_rw (core_217); the
    # answer door runs on Switchboard's own daemon pool, so mirror that identity.
    # Production pools register the jsonb codec, which is why fleet_cases writers
    # pass dict payloads; open_pool mirrors it, then the role is taken.
    async def _as_switchboard(conn: asyncpg.Connection) -> None:
        await conn.execute("SET ROLE butler_switchboard_rw")

    p = await open_pool(db_url, init=_as_switchboard)
    yield p
    await p.close()


async def test_no_answer_opens_one_case_with_recovery_door(
    owner_pool, switchboard_pool, bootstrap_url
):
    pool = owner_pool
    event_id = await record_event(
        pool,
        event_type=SECURITY_EVENT_TYPE,
        source_butler="switchboard",
        payload={
            "kind": "new_sign_in",
            "provider": "google",
            "sender_verification": "authenticated",
            "source_request_id": "req-synthetic-1",
        },
    )
    first = await record_security_answer(switchboard_pool, event_id=event_id, answer="no")
    second = await record_security_answer(switchboard_pool, event_id=event_id, answer="no")
    assert first["status"] == "ok" and first["case_id"] == second["case_id"]

    key = f"account_security:{event_id}"
    try:
        assert (
            await pool.fetchval(
                "SELECT count(*) FROM public.fleet_cases WHERE correlation_key = $1", key
            )
            == 1
        )
        evidence = await pool.fetch(
            "SELECT kind, ref, payload FROM public.fleet_case_evidence WHERE case_id = $1::uuid",
            first["case_id"],
        )
        assert [(r["kind"], r["ref"]) for r in evidence] == [("account_security_event", event_id)]

        yes = await record_security_answer(switchboard_pool, event_id=event_id, answer="yes")
        assert yes["case_id"] is None
        assert (
            await pool.fetchval(
                "SELECT state FROM public.fleet_cases WHERE correlation_key = $1", key
            )
            == "open"
        )
    finally:
        conn = await asyncpg.connect(bootstrap_url)
        try:
            await conn.execute("DELETE FROM public.fleet_cases WHERE correlation_key = $1", key)
        finally:
            await conn.close()


async def test_publishing_the_same_alert_twice_stores_one_event(switchboard_pool):
    # Pool carries the production jsonb codec, so record_event's pre-dumped payload is
    # stored as a JSON string scalar; the dedup query must still see through it.
    verdict = classify_account_security("no-reply@accounts.google.com", "Security alert")
    external_id = f"msg-synthetic-{uuid.uuid4()}"
    kwargs = dict(external_event_id=external_id, observed_at=None)
    first = await publish_security_event(
        switchboard_pool, verdict, source_request_id="req-a", **kwargs
    )
    second = await publish_security_event(
        switchboard_pool, verdict, source_request_id="req-b", **kwargs
    )
    assert first is not None and second is None
    stored = await switchboard_pool.fetchval(
        "SELECT count(*) FROM public.domain_events WHERE event_type = $1 "
        "AND payload::text LIKE '%' || $2 || '%'",
        SECURITY_EVENT_TYPE,
        external_id,
    )
    assert stored == 1
