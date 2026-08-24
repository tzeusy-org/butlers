"""Integration tests for the connector_registry operational-role migration (sw_031).

Covers bu-6jv4m.11:
  - ``operational_role`` / ``parent_endpoint_identity`` columns and the
    role CHECK constraint exist after the switchboard chain runs.
  - The backfill classifies the *current live shape* from persisted evidence —
    a heartbeating Google Health account becomes a ``runtime_instance`` and its
    per-account/per-resource cursors become ``checkpoint`` rows attached to
    that account, per account, with no cross-account leakage.
  - Rows with no evidence either way stay ``unknown`` rather than being guessed
    into a runtime instance.
  - ``public.v_qa_connector_state`` reads the persisted role instead of
    re-inferring one from column nullability.
  - ``cursor_store.save_cursor`` stamps ``checkpoint`` on insert and never
    demotes a row the heartbeat producer already claimed.
  - Downgrade removes the constraint and both columns cleanly, restoring the
    sw_028 view.

The backfill runs *inside* sw_031's upgrade, so each test upgrades to sw_030
first, inserts fixture rows, then applies sw_031 and asserts the result.
"""

from __future__ import annotations

import asyncio
import shutil

import pytest
from sqlalchemy import create_engine, text

from alembic import command
from butlers.testing.migration import create_migration_db, get_column_info, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]

# Obviously-synthetic fixture identities, built in-test.
_OWNER = "owner@example.test"
_SECOND_OWNER = "second@example.test"
_ACCOUNT = "00000000-0000-4000-8000-000000000001"
_SECOND_ACCOUNT = "00000000-0000-4000-8000-000000000002"
_PARENT = f"google_health:user:{_OWNER}"
_SECOND_PARENT = f"google_health:user:{_SECOND_OWNER}"


def _prepare_pre_backfill_db(postgres_container) -> str:
    """Run core (full) + switchboard up to sw_030 (the revision before sw_031)."""
    from butlers.migrations import _build_alembic_config, run_migrations

    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    asyncio.run(run_migrations(db_url, chain="core"))
    config = _build_alembic_config(db_url, chains=["switchboard"])
    command.upgrade(config, "switchboard@sw_030")
    return db_url


def _apply_sw_031(db_url: str) -> None:
    from butlers.migrations import _build_alembic_config

    config = _build_alembic_config(db_url, chains=["switchboard"])
    command.upgrade(config, "switchboard@sw_031")


def _exec(db_url: str, sql: str, params: dict | None = None) -> None:
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text(sql), params or {})
    finally:
        engine.dispose()


def _scalar(db_url: str, sql: str, params: dict | None = None):
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            return conn.execute(text(sql), params or {}).scalar()
    finally:
        engine.dispose()


def _insert_runtime(db_url: str, connector_type: str, endpoint_identity: str) -> None:
    """A row as the heartbeat producer leaves it: instance identity + heartbeat."""
    _exec(
        db_url,
        "INSERT INTO connector_registry"
        " (connector_type, endpoint_identity, state, instance_id, last_heartbeat_at)"
        " VALUES (:t, :i, 'healthy', gen_random_uuid(), now())",
        {"t": connector_type, "i": endpoint_identity},
    )


def _insert_cursor(db_url: str, connector_type: str, endpoint_identity: str) -> None:
    """A row as cursor_store left it before sw_031: a cursor and nothing else."""
    _exec(
        db_url,
        "INSERT INTO connector_registry"
        " (connector_type, endpoint_identity, state, checkpoint_cursor, checkpoint_updated_at)"
        " VALUES (:t, :i, 'unknown', 'synthetic-cursor', now())",
        {"t": connector_type, "i": endpoint_identity},
    )


def _insert_bare(db_url: str, connector_type: str, endpoint_identity: str) -> None:
    """A row with no evidence of either producer — e.g. a settings-only write."""
    _exec(
        db_url,
        "INSERT INTO connector_registry (connector_type, endpoint_identity, state)"
        " VALUES (:t, :i, 'unknown')",
        {"t": connector_type, "i": endpoint_identity},
    )


def _role(db_url: str, endpoint_identity: str) -> str:
    return _scalar(
        db_url,
        "SELECT operational_role FROM connector_registry WHERE endpoint_identity = :i",
        {"i": endpoint_identity},
    )


def _parent(db_url: str, endpoint_identity: str) -> str | None:
    return _scalar(
        db_url,
        "SELECT parent_endpoint_identity FROM connector_registry WHERE endpoint_identity = :i",
        {"i": endpoint_identity},
    )


def _seed_live_google_health_shape(db_url: str) -> list[str]:
    """Two heartbeating accounts, each with its own per-resource cursor rows."""
    cursors: list[str] = []
    for parent, account in ((_PARENT, _ACCOUNT), (_SECOND_PARENT, _SECOND_ACCOUNT)):
        _insert_runtime(db_url, "google_health", parent)
        for resource in ("activity", "hrv", "sleep_sessions"):
            identity = f"{parent}:{account}:{resource}"
            _insert_cursor(db_url, "google_health", identity)
            cursors.append(identity)
    return cursors


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_role_columns_exist_with_safe_defaults(postgres_container):
    """The role is persisted, NOT NULL, and defaults to the unavailable state."""
    db_url = _prepare_pre_backfill_db(postgres_container)
    _apply_sw_031(db_url)

    role = get_column_info(db_url, "connector_registry", "operational_role")
    assert role is not None
    assert role["is_nullable"] == "NO"
    assert "unknown" in role["column_default"]

    parent = get_column_info(db_url, "connector_registry", "parent_endpoint_identity")
    assert parent is not None
    assert parent["is_nullable"] == "YES"


def test_role_check_constraint_rejects_unnamed_roles(postgres_container):
    """Only the three defined roles are storable — no free-text role strings."""
    db_url = _prepare_pre_backfill_db(postgres_container)
    _apply_sw_031(db_url)

    with pytest.raises(Exception, match="valid_operational_role"):
        _exec(
            db_url,
            "INSERT INTO connector_registry"
            " (connector_type, endpoint_identity, state, operational_role)"
            " VALUES ('gmail', 'gmail:synthetic', 'unknown', 'probably_fine')",
        )


def test_new_row_defaults_to_unknown_not_runtime(postgres_container):
    """An unclassified row must not arrive already counted as a live connector."""
    db_url = _prepare_pre_backfill_db(postgres_container)
    _apply_sw_031(db_url)

    _insert_bare(db_url, "gmail", "gmail:synthetic")

    assert _role(db_url, "gmail:synthetic") == "unknown"


# ---------------------------------------------------------------------------
# Backfill
# ---------------------------------------------------------------------------


def test_backfill_classifies_the_live_google_health_shape(postgres_container):
    """The account is a runtime instance; its resource cursors are checkpoints."""
    db_url = _prepare_pre_backfill_db(postgres_container)
    cursors = _seed_live_google_health_shape(db_url)

    _apply_sw_031(db_url)

    assert _role(db_url, _PARENT) == "runtime_instance"
    assert _role(db_url, _SECOND_PARENT) == "runtime_instance"
    for identity in cursors:
        assert _role(db_url, identity) == "checkpoint", identity


def test_backfill_attaches_each_cursor_to_its_own_account(postgres_container):
    """Multi-account isolation: no cursor is attached to the other account."""
    db_url = _prepare_pre_backfill_db(postgres_container)
    _seed_live_google_health_shape(db_url)

    _apply_sw_031(db_url)

    for resource in ("activity", "hrv", "sleep_sessions"):
        assert _parent(db_url, f"{_PARENT}:{_ACCOUNT}:{resource}") == _PARENT
        assert _parent(db_url, f"{_SECOND_PARENT}:{_SECOND_ACCOUNT}:{resource}") == _SECOND_PARENT


def test_backfill_leaves_runtime_instances_unparented(postgres_container):
    """A runtime instance owns itself — it never gets a parent."""
    db_url = _prepare_pre_backfill_db(postgres_container)
    _seed_live_google_health_shape(db_url)

    _apply_sw_031(db_url)

    assert _parent(db_url, _PARENT) is None
    assert _parent(db_url, _SECOND_PARENT) is None


def test_backfill_keeps_unclassifiable_rows_unknown(postgres_container):
    """No heartbeat and no cursor is not evidence of a healthy connector."""
    db_url = _prepare_pre_backfill_db(postgres_container)
    _insert_bare(db_url, "steam", "steam:synthetic-unconfigured")

    _apply_sw_031(db_url)

    assert _role(db_url, "steam:synthetic-unconfigured") == "unknown"
    assert _parent(db_url, "steam:synthetic-unconfigured") is None


def test_backfill_promotes_a_heartbeating_row_that_also_holds_a_cursor(
    postgres_container,
):
    """Most connectors checkpoint under their heartbeat identity — still runtime."""
    db_url = _prepare_pre_backfill_db(postgres_container)
    _exec(
        db_url,
        "INSERT INTO connector_registry"
        " (connector_type, endpoint_identity, state, instance_id, last_heartbeat_at,"
        "  checkpoint_cursor, checkpoint_updated_at)"
        " VALUES ('gmail', 'gmail:synthetic', 'healthy', gen_random_uuid(), now(),"
        "         'synthetic-cursor', now())",
    )

    _apply_sw_031(db_url)

    assert _role(db_url, "gmail:synthetic") == "runtime_instance"
    assert _parent(db_url, "gmail:synthetic") is None


def test_backfill_does_not_attach_a_cursor_to_a_same_prefix_sibling(
    postgres_container,
):
    """Attachment requires a ``:``-delimited extension, not a bare string prefix."""
    db_url = _prepare_pre_backfill_db(postgres_container)
    # A runtime identity that is a plain string prefix of the cursor identity
    # but is not a ``:``-delimited ancestor of it.
    _insert_runtime(db_url, "google_health", f"google_health:user:{_OWNER}-other")
    _insert_runtime(db_url, "google_health", _PARENT)
    _insert_cursor(db_url, "google_health", f"{_PARENT}:{_ACCOUNT}:activity")

    _apply_sw_031(db_url)

    assert _parent(db_url, f"{_PARENT}:{_ACCOUNT}:activity") == _PARENT


def test_backfill_is_idempotent(postgres_container):
    """Re-running the classification does not reshuffle already-classified rows."""
    db_url = _prepare_pre_backfill_db(postgres_container)
    _seed_live_google_health_shape(db_url)
    _apply_sw_031(db_url)

    before = _scalar(
        db_url,
        "SELECT count(*) FROM connector_registry WHERE operational_role = 'checkpoint'",
    )
    # The upgrade's own UPDATEs are guarded on ``operational_role = 'unknown'``,
    # so replaying them cannot move a row that is already classified.
    _exec(
        db_url,
        "UPDATE connector_registry SET operational_role = 'runtime_instance'"
        " WHERE operational_role = 'unknown'"
        " AND (instance_id IS NOT NULL OR last_heartbeat_at IS NOT NULL)",
    )
    after = _scalar(
        db_url,
        "SELECT count(*) FROM connector_registry WHERE operational_role = 'checkpoint'",
    )

    assert before == 6
    assert after == before


# ---------------------------------------------------------------------------
# QA liveness view
# ---------------------------------------------------------------------------


def test_qa_view_reads_the_persisted_role(postgres_container):
    """The view stops re-inferring storage rows from column nullability."""
    db_url = _prepare_pre_backfill_db(postgres_container)
    _apply_sw_031(db_url)

    definition = _scalar(db_url, "SELECT pg_get_viewdef('public.v_qa_connector_state', true)")

    assert "operational_role" in definition


def test_qa_view_excludes_checkpoints_and_keeps_runtime_instances(postgres_container):
    """A cursor row carries no liveness into QA; its parent still does."""
    db_url = _prepare_pre_backfill_db(postgres_container)
    _seed_live_google_health_shape(db_url)
    _apply_sw_031(db_url)

    identities = _scalar(
        db_url,
        "SELECT array_agg(endpoint_identity ORDER BY endpoint_identity)"
        " FROM public.v_qa_connector_state WHERE connector_type = 'google_health'",
    )

    assert sorted(identities) == sorted([_PARENT, _SECOND_PARENT])


# ---------------------------------------------------------------------------
# Writer semantics
#
# Both producers write the role, so both are exercised against a real migrated
# database. These use the ``switchboard``-schema layout (the production one)
# because ``cursor_store`` and the heartbeat tool both qualify their SQL with
# ``switchboard.``.
# ---------------------------------------------------------------------------


def _heartbeat_tool():
    """Load the switchboard ``connector.heartbeat`` tool.

    ``roster/`` is butler config, not an installed package, so the tool is
    loaded by path the same way the API routers are.
    """
    from pathlib import Path

    from butlers.api.router_discovery import _load_router_module

    module = _load_router_module(
        Path("roster/switchboard/tools/connector/heartbeat.py"),
        "switchboard_connector_heartbeat_tool",
    )
    return module.heartbeat


def _synthetic_heartbeat(connector_type: str, endpoint_identity: str) -> dict:
    """A minimal valid connector.heartbeat.v1 envelope, built in-test."""
    import uuid
    from datetime import UTC, datetime

    return {
        "schema_version": "connector.heartbeat.v1",
        "connector": {
            "connector_type": connector_type,
            "endpoint_identity": endpoint_identity,
            "instance_id": str(uuid.uuid4()),
            "version": "0.0.0-synthetic",
        },
        "status": {"state": "healthy", "error_message": None, "uptime_s": 1},
        "counters": {
            "messages_ingested": 0,
            "messages_failed": 0,
            "source_api_calls": 0,
            "checkpoint_saves": 0,
        },
        "sent_at": datetime.now(UTC).isoformat(),
    }


@pytest.fixture(scope="module")
def switchboard_db_url(postgres_container) -> str:
    """Full core + switchboard chains, switchboard tables in their own schema."""
    from butlers.testing.migration import create_migrated_test_db

    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "switchboard"],
        schemas={"switchboard": "switchboard"},
    )


def _sw_row(db_url: str, endpoint_identity: str) -> tuple:
    return _scalar(
        db_url,
        "SELECT (operational_role, parent_endpoint_identity, checkpoint_cursor)::text"
        " FROM switchboard.connector_registry WHERE endpoint_identity = :i",
        {"i": endpoint_identity},
    )


def test_save_cursor_stamps_checkpoint_and_records_parent(switchboard_db_url):
    """A brand-new cursor row is storage state, attached to its runtime parent."""
    import asyncpg

    from butlers.connectors.cursor_store import save_cursor

    identity = f"{_PARENT}:{_ACCOUNT}:activity"

    async def _write() -> None:
        pool = await asyncpg.create_pool(switchboard_db_url)
        try:
            await save_cursor(
                pool,
                "google_health",
                identity,
                "synthetic-cursor",
                parent_endpoint_identity=_PARENT,
            )
        finally:
            await pool.close()

    asyncio.run(_write())

    assert _sw_row(switchboard_db_url, identity) == (f"(checkpoint,{_PARENT},synthetic-cursor)")


def test_heartbeat_promotes_a_cursor_created_row(switchboard_db_url):
    """The heartbeat producer claims the row: role ownership flows one way."""
    import asyncpg

    from butlers.connectors.cursor_store import save_cursor

    heartbeat = _heartbeat_tool()

    identity = "gmail:synthetic-promoted@example.test"

    async def _write() -> None:
        pool = await asyncpg.create_pool(switchboard_db_url)
        try:
            # cursor_store gets there first — the row starts as storage state.
            await save_cursor(pool, "gmail", identity, "synthetic-cursor")
            assert _sw_row(switchboard_db_url, identity).startswith("(checkpoint,")
            await heartbeat(pool, _synthetic_heartbeat("gmail", identity))
        finally:
            await pool.close()

    asyncio.run(_write())

    assert _sw_row(switchboard_db_url, identity).startswith("(runtime_instance,")


def test_save_cursor_never_demotes_a_runtime_instance(switchboard_db_url):
    """A live connector checkpointing under its own identity stays in the fleet."""
    import asyncpg

    from butlers.connectors.cursor_store import save_cursor

    heartbeat = _heartbeat_tool()

    identity = "gmail:synthetic-live@example.test"

    async def _write() -> None:
        pool = await asyncpg.create_pool(switchboard_db_url)
        try:
            await heartbeat(pool, _synthetic_heartbeat("gmail", identity))
            await save_cursor(pool, "gmail", identity, "synthetic-cursor")
        finally:
            await pool.close()

    asyncio.run(_write())

    assert _sw_row(switchboard_db_url, identity) == ("(runtime_instance,,synthetic-cursor)")


# ---------------------------------------------------------------------------
# Downgrade
# ---------------------------------------------------------------------------


def test_downgrade_drops_role_columns_and_restores_the_view(postgres_container):
    """sw_031 downgrade removes both columns; the view stops referencing them."""
    from butlers.migrations import _build_alembic_config

    db_url = _prepare_pre_backfill_db(postgres_container)
    _seed_live_google_health_shape(db_url)
    _apply_sw_031(db_url)
    assert get_column_info(db_url, "connector_registry", "operational_role") is not None

    config = _build_alembic_config(db_url, chains=["switchboard"])
    command.downgrade(config, "switchboard@sw_030")

    assert get_column_info(db_url, "connector_registry", "operational_role") is None
    assert get_column_info(db_url, "connector_registry", "parent_endpoint_identity") is None
    definition = _scalar(db_url, "SELECT pg_get_viewdef('public.v_qa_connector_state', true)")
    assert "operational_role" not in definition
