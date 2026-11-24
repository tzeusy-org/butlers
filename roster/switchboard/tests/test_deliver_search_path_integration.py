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

import asyncio
import shutil
import uuid
from contextlib import AsyncExitStack

import pytest

from butlers.testing.db import open_pool

docker_available = shutil.which("docker") is not None

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]


@pytest.fixture
async def dual_scoped_pools(fresh_db):
    """Administrative pools on one migrated clone with distinct query search paths.

    These seed and exercise cross-schema delivery SQL, not runtime-role
    isolation. The clone carries the real core + switchboard chains.
    """
    db_url = await asyncio.to_thread(
        fresh_db, "core", "switchboard", schemas={"switchboard": "switchboard"}
    )
    # Register each successful pool immediately. The first remains owned if
    # opening the second fails; stack unwinding attempts both on cancellation.
    async with AsyncExitStack() as stack:
        switchboard_pool = await open_pool(db_url, schema="switchboard")
        stack.push_async_callback(switchboard_pool.close)
        public_pool = await open_pool(db_url, schema="public")
        stack.push_async_callback(public_pool.close)
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
