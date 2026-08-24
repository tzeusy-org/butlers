"""Real-Postgres regression: a cursor save must record an ownership decision.

bu-ogs8x. ``cursor_store.save_cursor`` stamped every row it *created* with
``operational_role = 'checkpoint'`` and left ``parent_endpoint_identity`` NULL
unless the caller opted in to supplying one. Only
``connectors/google_health.py`` ever did.

Every other connector keys its cursor by its own runtime-instance identity, so
the row it creates is not storage state belonging to a parent at all — it is
the runtime instance's own row, waiting for its first heartbeat to claim it.
Stamping it ``checkpoint`` with a NULL parent put it straight into the
dashboard's ``unparented_checkpoints`` bucket, which sw_031's one-shot backfill
had just emptied. The bucket therefore refilled continuously after a migration
that appeared to have fixed it, reading as a dashboard regression rather than
as an unfinished rollout.

The fix makes the ownership declaration a **required** argument, so a call site
cannot inherit the NULL by omission, and derives ``operational_role`` from it:

* ``parent_endpoint_identity=NO_PARENT`` — the cursor key is the connector's own
  runtime identity. The row is stamped ``unknown``: unclaimed until a heartbeat
  proves a process owns it, which is exactly what ``unknown`` means. It is never
  storage state, so it can never be an unparented checkpoint.
* ``parent_endpoint_identity="<runtime identity>"`` — the cursor key carries
  extra dimensions (Google Health: account *and* resource). The row is stamped
  ``checkpoint`` and nests under the named parent.

Together those give the invariant this file's last test pins: a ``checkpoint``
row written by ``save_cursor`` always names a parent, so a NULL parent on a
freshly-written row can no longer mean "nobody set one".

All identities here are synthetic (``.invalid`` hosts, ``example.invalid``
addresses, literal ``synthetic`` tokens) and generated in-test.
"""

from __future__ import annotations

import asyncio
import shutil
from contextlib import suppress
from datetime import UTC, datetime

import asyncpg
import pytest

from butlers.api.routers.ingestion_connectors import (
    _group_checkpoints,
    _partition_by_operational_role,
)
from butlers.connectors.cursor_store import NO_PARENT, save_cursor
from butlers.connectors.google_drive import (
    AccountHealthStatus,
    GDriveConnectorManager,
    MultiAccountHealthStatus,
)
from butlers.connectors.home_assistant_checkpoint import save_ha_checkpoint
from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]

_REGISTRY_COLUMNS = """
    connector_type,
    endpoint_identity,
    last_heartbeat_at,
    archived_at,
    operational_role,
    parent_endpoint_identity,
    checkpoint_cursor,
    checkpoint_updated_at
"""


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    """Provision core + switchboard chains — switchboard.connector_registry.

    ``core`` is required alongside ``switchboard`` as of migration ``sw_019``
    (bu-aga08): ``switchboard.routing_verdict_log`` FKs to
    ``public.ingestion_events``, so the switchboard chain no longer migrates
    standalone.
    """
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "switchboard"],
        schemas={"switchboard": "switchboard"},
    )


@pytest.fixture
async def pool(migrated_db_url: str):
    p = await asyncpg.create_pool(
        migrated_db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
    )
    await p.execute("TRUNCATE TABLE switchboard.connector_registry")
    yield p
    await p.close()


async def _dashboard_buckets(pool: asyncpg.Pool) -> tuple[list[str], list[str]]:
    """Return ``(roster identities, unparented checkpoint identities)``.

    Runs the registry through the same two functions
    ``/api/ingestion/connectors/summaries`` uses, so the assertions below are
    about what an operator actually sees rather than about a column value that
    merely correlates with it.
    """
    rows = await pool.fetch(
        f"SELECT {_REGISTRY_COLUMNS} FROM switchboard.connector_registry WHERE deleted_at IS NULL"
    )
    roster_rows, checkpoint_rows = _partition_by_operational_role(rows)
    roster_keys = {(r["connector_type"], r["endpoint_identity"]) for r in roster_rows}
    _by_parent, unparented = _group_checkpoints(checkpoint_rows, roster_keys)
    return (
        [r["endpoint_identity"] for r in roster_rows],
        [r["endpoint_identity"] for r in unparented],
    )


async def test_self_identity_cursor_does_not_refill_unparented_bucket(
    pool: asyncpg.Pool,
) -> None:
    """CONTROL: a fresh cursor from a non-Health connector must not land unparented.

    ``save_ha_checkpoint`` is a real, unmodified call site: Home Assistant keys
    its checkpoint by the same ``endpoint_identity`` its heartbeat registers, so
    the row it creates is the runtime instance's own. Before bu-ogs8x this call
    created a ``checkpoint`` row with a NULL parent and the identity appeared in
    ``unparented_checkpoints`` on the very next dashboard read.
    """
    endpoint = "home_assistant:synthetic-host.invalid:8123"

    await save_ha_checkpoint(
        pool,
        endpoint,
        datetime(2026, 8, 25, 12, 0, 0, tzinfo=UTC),
        "sensor.synthetic_probe",
        "websocket",
    )

    roster, unparented = await _dashboard_buckets(pool)
    assert unparented == [], (
        f"A cursor keyed by the connector's own runtime identity landed in "
        f"unparented_checkpoints: {unparented!r}"
    )
    assert endpoint in roster


async def test_self_identity_cursor_row_is_unclaimed_not_storage(
    pool: asyncpg.Pool,
) -> None:
    """The row is ``unknown`` — unclaimed until a heartbeat proves a process owns it.

    ``save_cursor`` is not evidence that anything is running, so it must not
    claim ``runtime_instance`` (the heartbeat tool is the only writer allowed to
    do that). But it is also not storage state belonging to a parent, so
    ``checkpoint`` is wrong: that role hides the row from the roster entirely.
    ``unknown`` is the honest third answer, and read paths surface it as an
    explicitly unclassified row rather than as a healthy or an invisible one.
    """
    endpoint = "gmail:user:synthetic@example.invalid"

    await save_cursor(
        pool,
        "gmail",
        endpoint,
        '{"history_id": "1"}',
        parent_endpoint_identity=NO_PARENT,
    )

    row = await pool.fetchrow(
        "SELECT operational_role, parent_endpoint_identity FROM switchboard.connector_registry "
        "WHERE connector_type = $1 AND endpoint_identity = $2",
        "gmail",
        endpoint,
    )
    assert row["operational_role"] == "unknown"
    assert row["parent_endpoint_identity"] is None


async def test_declared_parent_nests_the_checkpoint_under_its_runtime_instance(
    pool: asyncpg.Pool,
) -> None:
    """A cursor key with extra dimensions stays storage state under its parent."""
    parent = "google_health:user:synthetic@example.invalid"
    child = f"{parent}:00000000-0000-4000-8000-000000000000:steps"

    await pool.execute(
        "INSERT INTO switchboard.connector_registry "
        "(connector_type, endpoint_identity, operational_role, last_heartbeat_at) "
        "VALUES ($1, $2, 'runtime_instance', NOW())",
        "google_health",
        parent,
    )
    await save_cursor(
        pool,
        "google_health",
        child,
        "cursor-synthetic-1",
        parent_endpoint_identity=parent,
    )

    rows = await pool.fetch(
        f"SELECT {_REGISTRY_COLUMNS} FROM switchboard.connector_registry WHERE deleted_at IS NULL"
    )
    roster_rows, checkpoint_rows = _partition_by_operational_role(rows)
    roster_keys = {(r["connector_type"], r["endpoint_identity"]) for r in roster_rows}
    by_parent, unparented = _group_checkpoints(checkpoint_rows, roster_keys)

    assert unparented == []
    assert [r["endpoint_identity"] for r in by_parent[("google_health", parent)]] == [child]


async def test_save_cursor_never_writes_a_parentless_checkpoint(pool: asyncpg.Pool) -> None:
    """Invariant: every ``checkpoint`` row this module creates names a parent.

    This is what stops a NULL parent from meaning two different things. sw_031's
    backfill can still leave a NULL parent on a *pre-existing* checkpoint whose
    owner it could not resolve — that row is genuinely orphaned and belongs in
    the unparented bucket. Nothing written from here on adds to it by omission.
    """
    parent = "google_health:user:synthetic@example.invalid"
    await save_cursor(
        pool,
        "google_health",
        f"{parent}:00000000-0000-4000-8000-000000000001:sleep",
        "cursor-synthetic-2",
        parent_endpoint_identity=parent,
    )
    await save_cursor(
        pool,
        "owntracks",
        "owntracks:synthetic-tracker",
        "1700000000",
        parent_endpoint_identity=NO_PARENT,
    )

    orphans = await pool.fetch(
        "SELECT endpoint_identity FROM switchboard.connector_registry "
        "WHERE operational_role = 'checkpoint' AND parent_endpoint_identity IS NULL"
    )
    assert [r["endpoint_identity"] for r in orphans] == []


async def test_drive_per_account_heartbeat_claims_the_runtime_role(pool: asyncpg.Pool) -> None:
    """Drive's per-account rows are heartbeated by SQL, so that SQL must claim the role.

    Every other connector reaches ``connector.heartbeat``, which stamps
    ``runtime_instance`` on check-in. Drive's manager instead UPDATEs
    ``last_heartbeat_at`` directly for each ``google_drive:user:<email>`` row,
    and before bu-ogs8x that UPDATE left ``operational_role`` alone — so a new
    account's row kept whatever ``save_cursor`` created it as, forever.
    """
    email = "synthetic-drive@example.invalid"
    endpoint = f"google_drive:user:{email}"

    await save_cursor(
        pool,
        "google_drive",
        endpoint,
        '{"page_token": "synthetic-token"}',
        parent_endpoint_identity=NO_PARENT,
    )

    manager = GDriveConnectorManager(
        db_pool=None,  # type: ignore[arg-type]
        cursor_pool=pool,
        heartbeat_interval_s=0,
    )
    manager.get_health = lambda: MultiAccountHealthStatus(  # type: ignore[method-assign]
        status="healthy",
        uptime_seconds=1.0,
        active_accounts=1,
        account_health=[
            AccountHealthStatus(
                email=email,
                endpoint_identity=endpoint,
                status="healthy",
                last_checkpoint_save_at=None,
                last_ingest_submit_at=None,
                source_api_connectivity="connected",
            )
        ],
        timestamp="2026-08-25T12:00:00+00:00",
    )

    manager._running = True
    task = asyncio.create_task(manager._account_heartbeat_loop())
    try:
        await _wait_for_role(pool, "google_drive", endpoint, "runtime_instance")
    finally:
        manager._running = False
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    roster, unparented = await _dashboard_buckets(pool)
    assert unparented == []
    assert endpoint in roster


async def _wait_for_role(
    pool: asyncpg.Pool,
    connector_type: str,
    endpoint_identity: str,
    expected: str,
    timeout_s: float = 5.0,
) -> None:
    """Poll until the row reports ``expected``, so the test never sleeps blind."""
    deadline = asyncio.get_running_loop().time() + timeout_s
    role = None
    while asyncio.get_running_loop().time() < deadline:
        role = await pool.fetchval(
            "SELECT operational_role FROM switchboard.connector_registry "
            "WHERE connector_type = $1 AND endpoint_identity = $2",
            connector_type,
            endpoint_identity,
        )
        if role == expected:
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"operational_role stayed {role!r}, never became {expected!r}")
