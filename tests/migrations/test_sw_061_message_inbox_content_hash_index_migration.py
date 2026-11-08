"""sw_061: the cross-connector content-hash dedupe lookup is index-backed.

bu-dhloi1.178.  ``ingest_v1`` looks a message up by ``request_context ->> 'content_hash_key'``
twice per new message. Without an index that is a scan of every message_inbox partition
(14s on dev at ~200k rows), the whole of the connector's poll-to-submit gap.
Real PostgreSQL; seeded rows use synthetic identities only.
"""

from __future__ import annotations

import asyncio
import json
import shutil

import pytest
from sqlalchemy import create_engine, text

from alembic import command
from butlers.migrations import _build_alembic_config, run_migrations
from butlers.testing.migration import create_migration_db, migration_db_name

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

_RECEIVED = "2026-10-01T12:00:00+00:00"
_LATER = "2026-11-01T12:00:00+00:00"
_INDEX = "ix_message_inbox_content_hash_received_at"


def _index_exists(engine) -> bool:
    with engine.connect() as conn:
        return bool(conn.execute(text(f"SELECT to_regclass('switchboard.{_INDEX}')")).scalar())


def _uses_index(plan: str) -> bool:
    """True when some node probes the content-hash expression as an index condition."""
    return any("Index Cond" in line and "content_hash_key" in line for line in plan.splitlines())


def _lookup_plan(engine) -> str:
    """The planner's plan for the exact ingest_v1 lookup, with seq scans made unattractive."""
    with engine.connect() as conn:
        conn.execute(text("SET enable_seqscan = off"))
        rows = conn.execute(
            text(
                "EXPLAIN SELECT (request_context ->> 'request_id')::uuid "
                "FROM switchboard.message_inbox "
                "WHERE request_context ->> 'content_hash_key' = 'hash-a' "
                "ORDER BY received_at DESC LIMIT 1"
            )
        )
        return "\n".join(row[0] for row in rows)


def test_sw_061_indexes_content_hash_lookup(postgres_container) -> None:
    db_url = create_migration_db(postgres_container, migration_db_name())
    asyncio.run(run_migrations(db_url, chain="core"))
    config = _build_alembic_config(db_url, ["switchboard"], target_schema="switchboard")
    command.upgrade(config, "switchboard@sw_060")  # pinned-revision: sw_061 predecessor

    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            for ts in (_RECEIVED, _LATER):
                conn.execute(
                    text(
                        "SELECT switchboard.switchboard_message_inbox_ensure_partition("
                        "CAST(:ts AS timestamptz))"
                    ),
                    {"ts": ts},
                )
            conn.execute(
                text(
                    "INSERT INTO switchboard.message_inbox "
                    "(received_at, request_context, raw_payload, normalized_text, "
                    "lifecycle_state) VALUES (CAST(:ts AS timestamptz), "
                    "CAST(:ctx AS jsonb), '{}'::jsonb, 'seed', 'completed')"
                ),
                {"ts": _RECEIVED, "ctx": json.dumps({"content_hash_key": "hash-a"})},
            )
        assert not _index_exists(engine)
        assert not _uses_index(_lookup_plan(engine))

        command.upgrade(config, "switchboard@sw_061")  # pinned-revision: the migration under test
        assert _index_exists(engine)
        plan = _lookup_plan(engine)
        assert "Seq Scan" not in plan
        assert _uses_index(plan)

        # A partition created after the migration is indexed too.
        with engine.connect() as conn:
            conn.execute(
                text(
                    "SELECT switchboard.switchboard_message_inbox_ensure_partition("
                    "CAST(:ts AS timestamptz))"
                ),
                {"ts": "2027-02-01T12:00:00+00:00"},
            )
            indexed = conn.execute(
                text(
                    "SELECT count(*) FROM pg_indexes WHERE schemaname = 'switchboard' "
                    "AND tablename LIKE 'message_inbox_p%' "
                    "AND indexdef LIKE '%content_hash_key%'"
                )
            ).scalar()
            partitions = conn.execute(
                text(
                    "SELECT count(*) FROM pg_inherits "
                    "WHERE inhparent = 'switchboard.message_inbox'::regclass"
                )
            ).scalar()
        assert indexed == partitions

        command.downgrade(config, "switchboard@sw_060")  # pinned-revision: sw_061 predecessor
        assert not _index_exists(engine)
    finally:
        engine.dispose()
