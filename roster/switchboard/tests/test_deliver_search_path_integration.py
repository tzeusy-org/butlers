"""Integration test for bu-tdd4k.2: deliver()'s cross-schema delivery path
must resolve correctly under a caller pool whose search_path does NOT
include the switchboard schema.

Reproduces the actual production topology: ``secrets_lifecycle.py`` runs
inside the dashboard-api process against the *shared credential pool*, which
in one-db/multi-schema topology is deliberately scoped to
``schema="public"`` (see ``deps.py``'s ``shared_db_schema = "public"``) --
never the switchboard butler's own schema-scoped pool. Before this fix,
every bare ``butler_registry``/``notifications``/``routing_log`` reference
reachable from ``deliver()``'s call graph (``deliver.py``, ``registry.py``'s
``resolve_routing_target``, ``route.py``) silently resolved to nothing (or
raised ``UndefinedTableError`` after an otherwise-successful dispatch) under
that pool -- so the secrets-lifecycle owner-push had never delivered
(attention ledger: 120 suppressed / 0 delivered).

This test seeds a real ``switchboard`` schema (mirroring the production
Alembic migration + one-db topology), then drives ``deliver()`` through a
*separate* pool connected to the same database with search_path scoped to
``public`` only -- exactly the caller shape that was broken. It asserts the
whole channel-based dispatch succeeds end to end (registry lookup, routed
call, and both the ``notifications``/``routing_log`` writes), which only
holds once every hop in the path is schema-qualified.
"""

from __future__ import annotations

import shutil
import uuid
from typing import Any

import pytest

from butlers.db import Database

docker_available = shutil.which("docker") is not None

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]


def _unique_db_name() -> str:
    return f"test_deliver_sp_{uuid.uuid4().hex[:12]}"


@pytest.fixture
async def dual_scoped_pools(postgres_container):
    """Two pools against the *same* database: one scoped to ``switchboard``
    (used to seed the schema/data, mirroring the switchboard butler's own
    connections) and one scoped to ``public`` only (used to invoke deliver(),
    mirroring secrets_lifecycle's shared credential pool).
    """
    db_name = _unique_db_name()
    conn_kwargs: dict[str, Any] = {
        "host": postgres_container.get_container_host_ip(),
        "port": int(postgres_container.get_exposed_port(5432)),
        "user": postgres_container.username,
        "password": postgres_container.password,
    }

    switchboard_db = Database(db_name=db_name, schema="switchboard", **conn_kwargs)
    await switchboard_db.provision()
    switchboard_pool = await switchboard_db.connect()

    public_db = Database(db_name=db_name, schema="public", **conn_kwargs)
    await public_db.provision()  # idempotent -- db already exists
    public_pool = await public_db.connect()

    try:
        await switchboard_pool.execute("CREATE SCHEMA IF NOT EXISTS switchboard")
        await switchboard_pool.execute("""
            CREATE TABLE IF NOT EXISTS switchboard.butler_registry (
                name TEXT PRIMARY KEY,
                endpoint_url TEXT NOT NULL,
                description TEXT,
                modules JSONB NOT NULL DEFAULT '[]',
                last_seen_at TIMESTAMPTZ,
                eligibility_state TEXT NOT NULL DEFAULT 'active',
                liveness_ttl_seconds INTEGER NOT NULL DEFAULT 300,
                quarantined_at TIMESTAMPTZ,
                quarantine_reason TEXT,
                route_contract_min INTEGER NOT NULL DEFAULT 1,
                route_contract_max INTEGER NOT NULL DEFAULT 1,
                capabilities JSONB NOT NULL DEFAULT '[]',
                eligibility_updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                registered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                agent_type TEXT NOT NULL DEFAULT 'butler'
            )
        """)
        await switchboard_pool.execute("""
            CREATE TABLE IF NOT EXISTS switchboard.butler_registry_eligibility_log (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                butler_name TEXT NOT NULL,
                previous_state TEXT NOT NULL,
                new_state TEXT NOT NULL,
                reason TEXT NOT NULL,
                previous_last_seen_at TIMESTAMPTZ,
                new_last_seen_at TIMESTAMPTZ,
                observed_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        await switchboard_pool.execute("""
            CREATE TABLE IF NOT EXISTS switchboard.routing_log (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                source_butler TEXT NOT NULL,
                target_butler TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                success BOOLEAN NOT NULL,
                duration_ms INTEGER,
                error TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                thread_id TEXT,
                source_channel TEXT,
                contact_id UUID,
                entity_id UUID,
                sender_roles TEXT[]
            )
        """)
        await switchboard_pool.execute("""
            CREATE TABLE IF NOT EXISTS switchboard.notifications (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                source_butler TEXT NOT NULL,
                channel TEXT NOT NULL,
                recipient TEXT NOT NULL,
                message TEXT NOT NULL,
                metadata JSONB NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'sent',
                error TEXT,
                session_id UUID,
                trace_id TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        await switchboard_pool.execute(
            """
            INSERT INTO switchboard.butler_registry
                (name, endpoint_url, description, modules, last_seen_at, capabilities)
            VALUES ($1, $2, $3, $4::jsonb, now(), $5::jsonb)
            """,
            "messenger",
            "http://localhost:41100/sse",
            "Messenger",
            ["telegram"],
            ["trigger", "telegram"],
        )

        yield switchboard_pool, public_pool
    finally:
        await switchboard_db.close()
        await public_db.close()


@pytest.mark.pg_clock
async def test_deliver_succeeds_under_public_only_search_path(dual_scoped_pools) -> None:
    """deliver() must find the registry row and complete dispatch even when
    the caller's pool search_path is scoped to ``public`` only -- the exact
    shape of secrets_lifecycle's shared credential pool. Pre-fix, the bare
    ``butler_registry`` SELECT in deliver.py resolved to zero rows under this
    pool and the call failed with "No butler with 'telegram' module found in
    registry" without ever reaching route().
    """
    from butlers.tools.switchboard.notification.deliver import deliver

    switchboard_pool, public_pool = dual_scoped_pools

    # Sanity: the calling pool genuinely cannot see the switchboard schema
    # unqualified -- this is what makes the test meaningful.
    with pytest.raises(Exception, match="does not exist"):
        await public_pool.fetch("SELECT name FROM butler_registry")

    async def mock_call(endpoint_url: str, tool_name: str, args: dict) -> dict:
        return {"ok": True, "message_id": "abc-123"}

    result = await deliver(
        public_pool,
        channel="telegram",
        message="Credential 'GOOGLE_OAUTH' has expired.",
        recipient="999888",
        source_butler="switchboard",
        call_fn=mock_call,
    )

    assert result["status"] == "sent", result
    assert result["notification_id"]

    # The notification write (log_notification) landed in the real
    # switchboard.notifications table, schema-qualified from the public-only
    # caller pool.
    notif_row = await switchboard_pool.fetchrow(
        "SELECT channel, recipient, status FROM switchboard.notifications WHERE id = $1",
        uuid.UUID(result["notification_id"]),
    )
    assert notif_row is not None
    assert notif_row["channel"] == "telegram"
    assert notif_row["recipient"] == "999888"
    assert notif_row["status"] == "sent"

    # route()'s post-success bookkeeping (last_seen_at UPDATE + routing_log
    # INSERT) also completed without raising and masking the success.
    routing_row = await switchboard_pool.fetchrow(
        "SELECT success FROM switchboard.routing_log WHERE target_butler = 'messenger'"
    )
    assert routing_row is not None
    assert routing_row["success"] is True

    registry_row = await switchboard_pool.fetchrow(
        "SELECT last_seen_at FROM switchboard.butler_registry WHERE name = 'messenger'"
    )
    assert registry_row["last_seen_at"] is not None


async def test_deliver_reports_failure_when_no_registered_butler_under_public_only_pool(
    dual_scoped_pools,
) -> None:
    """No eligible butler for the module still reports a clean failure (not a
    raised exception) under a public-only pool -- covers the "no candidate"
    branch through the same schema-qualified lookup."""
    from butlers.tools.switchboard.notification.deliver import deliver

    switchboard_pool, public_pool = dual_scoped_pools
    await switchboard_pool.execute("DELETE FROM switchboard.butler_registry")

    result = await deliver(
        public_pool,
        channel="telegram",
        message="hello",
        recipient="1",
        source_butler="switchboard",
    )

    assert result["status"] == "failed"
    assert "telegram" in result["error"]
