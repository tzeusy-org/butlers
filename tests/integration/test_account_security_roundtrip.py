"""Real-Postgres regression: account-security answer door (bu-q7vx1q.10).

Exercises ``record_security_answer`` against a migrated database: a recorded
``switchboard.security_event`` row, a ``no`` answer creating exactly one
``fleet_cases`` row with the provider recovery door as evidence, and a repeated
``no`` collapsing onto the same case. Sibling of
``test_fleet_case_contribution_roundtrip.py`` (same fixtures and cleanup rules).
"""

from __future__ import annotations

import shutil

import asyncpg
import pytest

from butlers.core.account_security_events import SECURITY_EVENT_TYPE, record_security_answer
from butlers.core.domain_events import record_event
from butlers.testing.migration import (
    create_migrated_test_db,
    migration_bootstrap_db_url,
    migration_db_name,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]


@pytest.fixture(scope="module")
def _db_name() -> str:
    return migration_db_name()


@pytest.fixture(scope="module")
def db_url(postgres_container, _db_name: str) -> str:
    return create_migrated_test_db(postgres_container, _db_name, chains=["core"])


@pytest.fixture(scope="module")
def bootstrap_url(postgres_container, _db_name: str, db_url: str) -> str:
    return migration_bootstrap_db_url(postgres_container, _db_name).replace(
        "postgresql+psycopg2://", "postgresql://", 1
    )


@pytest.fixture
async def pool(db_url: str) -> asyncpg.Pool:
    p = await asyncpg.create_pool(db_url, min_size=1, max_size=3)
    yield p
    await p.close()


@pytest.fixture
async def switchboard_pool(db_url: str) -> asyncpg.Pool:
    # fleet_cases INSERT is RLS-restricted to butler_switchboard_rw (core_217); the
    # answer door runs on Switchboard's own daemon pool, so mirror that identity.
    async def _as_switchboard(conn: asyncpg.Connection) -> None:
        await conn.execute("SET ROLE butler_switchboard_rw")

    p = await asyncpg.create_pool(db_url, min_size=1, max_size=3, init=_as_switchboard)
    yield p
    await p.close()


async def test_no_answer_opens_one_case_with_recovery_door(pool, switchboard_pool, bootstrap_url):
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
