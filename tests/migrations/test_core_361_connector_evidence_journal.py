"""core_361: ``connectors.evidence_journal`` is append-only, keyed, and refuses its downgrade.

Real PostgreSQL. Covers the unique identity key, the UPDATE/DELETE/TRUNCATE guard, the
connector-writer-only grants, the real write path through ``evidence_journal.commit_envelope``,
and the downgrade refusal while rows exist (bu-uhbhzq.4).
"""

from __future__ import annotations

import importlib.util
import shutil
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

import asyncpg
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from alembic import command
from butlers.connectors import evidence_journal as journal
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import (
    create_migration_db,
    init_db_sql_for_dbapi,
    migration_bootstrap_db_url,
    migration_db_name,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]

_CORE_361 = Path(__file__).resolve().parents[2] / (
    "alembic/versions/core/core_361_connector_evidence_journal.py"
)


def _below_core_361() -> str:
    """The revision just under core_361 (re-pointed when the chain head moves)."""
    spec = importlib.util.spec_from_file_location("core_361_under_test", _CORE_361)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return f"core@{module.down_revision}"


_ENVELOPE = {
    "source": {"channel": "gmail", "provider": "gmail", "endpoint_identity": "acct:synthetic"},
    "event": {"external_event_id": "m-1", "observed_at": "2026-11-20T10:00:00+00:00"},
    "sender": {"identity": "alice@example.test"},
    "payload": {"raw": {"subject": "hi"}, "normalized_text": "hi"},
    "control": {"idempotency_key": "gmail:acct:synthetic:m-1", "ingestion_tier": "full"},
}


async def test_commit_dedupes_and_appends_a_new_revision(db_pool: asyncpg.Pool) -> None:
    assert await journal.commit_envelope(db_pool, _ENVELOPE) is True
    assert await journal.commit_envelope(db_pool, _ENVELOPE) is False
    assert await journal.commit_envelope(db_pool, _ENVELOPE, provider_revision="r2") is True
    count = await db_pool.fetchval("SELECT count(*) FROM connectors.evidence_journal")
    assert count == 2
    row = await db_pool.fetchrow(
        "SELECT ingestion_tier, idempotency_key, raw FROM connectors.evidence_journal "
        "WHERE provider_revision = ''"
    )
    assert row["ingestion_tier"] == "full"
    assert row["idempotency_key"] == "gmail:acct:synthetic:m-1"
    assert "subject" in row["raw"]


async def test_the_journal_is_append_only(db_pool: asyncpg.Pool) -> None:
    await journal.commit(
        db_pool,
        source="gmail",
        endpoint_identity="e",
        provider_id="p",
        observed_at=datetime(2026, 11, 20, tzinfo=UTC),
        raw={"k": "v"},
        tier="metadata",
    )
    for statement in (
        "UPDATE connectors.evidence_journal SET provider_id = 'x'",
        "DELETE FROM connectors.evidence_journal",
        "TRUNCATE connectors.evidence_journal",
    ):
        with pytest.raises(asyncpg.PostgresError, match="append-only"):
            await db_pool.execute(statement)
    assert await db_pool.fetchval("SELECT count(*) FROM connectors.evidence_journal") == 1


async def test_the_tier_check_refuses_anything_but_full_or_metadata(db_pool: asyncpg.Pool) -> None:
    with pytest.raises(asyncpg.CheckViolationError):
        await db_pool.execute(
            "INSERT INTO connectors.evidence_journal (source, endpoint_identity, provider_id, "
            "observed_at, capture_schema_version, ingestion_tier, raw) "
            "VALUES ('gmail', 'e', 'p', now(), 1, 'skip', '{}'::jsonb)"
        )


def test_the_downgrade_refuses_while_rows_exist_then_drops(fresh_db) -> None:
    url = fresh_db("core")
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO connectors.evidence_journal (source, endpoint_identity, "
                    "provider_id, observed_at, capture_schema_version, ingestion_tier, raw) "
                    "VALUES ('gmail', 'e', 'p', now(), 1, 'full', '{}'::jsonb)"
                )
            )
        config = _build_alembic_config(url, chains=["core"])
        with pytest.raises(DBAPIError, match="downgrade refused"):
            command.downgrade(config, _below_core_361())
        with pytest.raises(DBAPIError, match="append-only"):
            with engine.begin() as conn:
                conn.execute(text("DELETE FROM connectors.evidence_journal"))
    finally:
        engine.dispose()


def test_the_downgrade_drops_an_empty_journal(fresh_db) -> None:
    url = fresh_db("core")
    config = _build_alembic_config(url, chains=["core"])
    command.downgrade(config, _below_core_361())
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            assert (
                conn.execute(text("SELECT to_regclass('connectors.evidence_journal')")).scalar()
                is None
            )
    finally:
        engine.dispose()


async def test_only_the_connector_writer_can_insert_and_read(db_pool: asyncpg.Pool) -> None:
    grants = await db_pool.fetch(
        "SELECT grantee, privilege_type FROM information_schema.role_table_grants "
        "WHERE table_schema = 'connectors' AND table_name = 'evidence_journal' "
        "AND grantee <> current_user"
    )
    assert {(g["grantee"], g["privilege_type"]) for g in grants} <= {
        ("connector_writer", "SELECT"),
        ("connector_writer", "INSERT"),
    }


def _journal_exists(url: str) -> bool:
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            return (
                conn.execute(text("SELECT to_regclass('connectors.evidence_journal')")).scalar()
                is not None
            )
    finally:
        engine.dispose()


def test_two_schema_chains_share_the_journal_and_only_the_last_downgrade_drops_it(
    postgres_container,
) -> None:
    """core_361 runs once per schema-local core chain; the shared table outlives all but the last."""
    db_url = create_migration_db(postgres_container, migration_db_name())
    below = _below_core_361().removeprefix("core@")
    first = _build_alembic_config(db_url, chains=["core"], target_schema="general")
    second = _build_alembic_config(db_url, chains=["core"], target_schema="health")
    command.upgrade(first, "core_361")
    command.upgrade(second, "core_361")  # replays over the table, triggers and grants
    assert _journal_exists(db_url)

    command.downgrade(first, below)
    assert _journal_exists(db_url), "another schema still depends on the journal"

    command.downgrade(second, below)
    assert not _journal_exists(db_url)


def test_the_last_schema_refuses_its_downgrade_while_rows_exist(postgres_container) -> None:
    db_url = create_migration_db(postgres_container, migration_db_name())
    below = _below_core_361().removeprefix("core@")
    first = _build_alembic_config(db_url, chains=["core"], target_schema="general")
    second = _build_alembic_config(db_url, chains=["core"], target_schema="health")
    command.upgrade(first, "core_361")
    command.upgrade(second, "core_361")
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO connectors.evidence_journal (source, endpoint_identity, "
                    "provider_id, observed_at, capture_schema_version, ingestion_tier, raw) "
                    "VALUES ('gmail', 'e', 'p', now(), 1, 'full', '{}'::jsonb)"
                )
            )
    finally:
        engine.dispose()
    command.downgrade(first, below)  # not the last schema: no refusal, nothing dropped
    assert _journal_exists(db_url)
    with pytest.raises(DBAPIError, match="downgrade refused"):
        command.downgrade(second, below)
    assert _journal_exists(db_url)


def _rerun_init_db(bootstrap_url: str, migration_url: str) -> None:
    """Replay the checked-in bootstrap, whose blanket connector grants predate the journal."""
    engine = create_engine(bootstrap_url, isolation_level="AUTOCOMMIT")
    raw = engine.raw_connection()
    try:
        raw.autocommit = True
        with raw.cursor() as cursor:
            cursor.execute(
                "SELECT set_config('butlers.connecting_user', %s, false)",
                (unquote(urlparse(migration_url).username or ""),),
            )
            cursor.execute(init_db_sql_for_dbapi())
    finally:
        raw.close()
        engine.dispose()


def test_the_connector_writer_cannot_rewrite_the_journal_even_after_a_bootstrap_replay(
    postgres_container,
) -> None:
    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    config = _build_alembic_config(db_url, chains=["core"])
    command.upgrade(config, "core_361")
    _rerun_init_db(migration_bootstrap_db_url(postgres_container, db_name), db_url)
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            held = {
                privilege
                for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE")
                if conn.execute(
                    text(
                        "SELECT has_table_privilege('connector_writer', "
                        "'connectors.evidence_journal', :p)"
                    ),
                    {"p": privilege},
                ).scalar_one()
            }
        assert held == {"SELECT", "INSERT"}
    finally:
        engine.dispose()
