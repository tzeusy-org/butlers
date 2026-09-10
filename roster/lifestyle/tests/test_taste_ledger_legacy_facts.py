"""Real-migration coverage for lifestyle_002's legacy prose-fact conversion.

Stages the lifestyle chain at ``lifestyle_001`` (pre-ledger), inserts a
``facts`` row the way ``memory_store_fact`` would, then upgrades to
``lifestyle_002`` and asserts the fact survives as a ``verdicts`` row with
``verdict_text`` — the acceptance outcome for bu-2jtfw.10 ("the 61 legacy
prose facts survive as verdict_text").

Issue: bu-2jtfw.10
"""

from __future__ import annotations

import asyncio
import shutil

import asyncpg
import pytest

from butlers.db import register_jsonb_codec, schema_search_path
from butlers.migrations import run_migrations
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


def test_active_lifestyle_fact_survives_upgrade_as_verdict(postgres_container) -> None:
    db_url = create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "memory", "lifestyle"],
        schemas={"memory": "lifestyle", "lifestyle": "lifestyle"},
        revisions={"lifestyle": "lifestyle_001"},
    )

    async def _seed_and_check() -> tuple[str | None, str | None]:
        search_path = schema_search_path("lifestyle")
        pool = await asyncpg.create_pool(
            db_url, server_settings={"search_path": search_path}, init=register_jsonb_codec
        )
        try:
            await pool.execute(
                """
                INSERT INTO facts (subject, predicate, content, scope, validity, permanence)
                VALUES ('user', 'likes_genre', 'loves jazz', 'lifestyle', 'active', 'stable')
                """
            )
            # An inactive/other-scope fact must NOT be migrated.
            await pool.execute(
                """
                INSERT INTO facts (subject, predicate, content, scope, validity, permanence)
                VALUES ('user', 'likes_genre', 'stale opinion', 'lifestyle', 'superseded', 'stable')
                """
            )
            await pool.execute(
                """
                INSERT INTO facts (subject, predicate, content, scope, validity, permanence)
                VALUES ('user', 'measurement_steps', 'irrelevant', 'health', 'active', 'stable')
                """
            )
        finally:
            await pool.close()

        await run_migrations(db_url, chain="lifestyle", schema="lifestyle")

        pool = await asyncpg.create_pool(
            db_url, server_settings={"search_path": search_path}, init=register_jsonb_codec
        )
        try:
            verdict_text = await pool.fetchval(
                "SELECT verdict_text FROM verdicts WHERE predicate = 'likes_genre' "
                "AND verdict_text = 'loves jazz'"
            )
            total_verdicts = await pool.fetchval("SELECT count(*) FROM verdicts")
            return verdict_text, total_verdicts
        finally:
            await pool.close()

    verdict_text, total_verdicts = asyncio.run(_seed_and_check())
    assert verdict_text == "loves jazz"
    # Exactly the one active lifestyle-scoped fact migrated — not the
    # superseded row, not the health-scoped row.
    assert total_verdicts == 1


def test_reapplying_legacy_fact_migration_creates_no_duplicate_verdict(
    postgres_container,
) -> None:
    """Alembic never re-runs an applied revision, but the migration's own
    ``INSERT ... ON CONFLICT (legacy_fact_id) DO NOTHING`` is what makes a
    manual replay of that step (e.g. a stamp-and-rerun recovery) safe.
    Exercise the upgrade() body directly, twice, on the same connection."""
    import importlib.util
    from pathlib import Path
    from unittest.mock import patch

    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext
    from sqlalchemy import create_engine, text

    module_path = Path(__file__).resolve().parents[1] / "migrations" / "002_taste_ledger.py"
    spec = importlib.util.spec_from_file_location("lifestyle_002", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    db_url = create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "memory", "lifestyle"],
        schemas={"memory": "lifestyle", "lifestyle": "lifestyle"},
        revisions={"lifestyle": "lifestyle_001"},
    )
    engine = create_engine(db_url)
    try:
        with engine.begin() as connection:
            connection.execute(text('SET search_path TO "lifestyle", public'))
            connection.execute(
                text(
                    "INSERT INTO facts (subject, predicate, content, scope, validity, permanence) "
                    "VALUES ('user', 'hobby', 'pottery on weekends', 'lifestyle', 'active', 'stable')"
                )
            )
            operations = Operations(MigrationContext.configure(connection))
            with patch.object(module, "op", operations):
                module.upgrade()
                module.upgrade()

            count = connection.execute(text("SELECT count(*) FROM verdicts")).scalar_one()
            assert count == 1
    finally:
        engine.dispose()


def test_downgrade_exports_direct_owner_verdict_as_legacy_fact(postgres_container) -> None:
    """Rolling back the ledger keeps directly-created owner assertions durable."""
    import importlib.util
    from pathlib import Path
    from unittest.mock import patch

    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext
    from sqlalchemy import create_engine, text

    module_path = Path(__file__).resolve().parents[1] / "migrations" / "002_taste_ledger.py"
    spec = importlib.util.spec_from_file_location("lifestyle_002_downgrade", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    db_url = create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "memory", "lifestyle"],
        schemas={"memory": "lifestyle", "lifestyle": "lifestyle"},
    )
    engine = create_engine(db_url)
    try:
        with engine.begin() as connection:
            connection.execute(text('SET search_path TO "lifestyle", public'))
            work_id = connection.execute(
                text(
                    "INSERT INTO works (kind, title, external_ids) "
                    "VALUES ('track', 'Owner Song', '{\"primary\": \"spotify:track:owner\"}') "
                    "RETURNING id"
                )
            ).scalar_one()
            verdict_id = connection.execute(
                text(
                    "INSERT INTO verdicts (work_id, predicate, verdict_text, source) "
                    "VALUES (:work_id, 'likes_artist', 'I love this', 'owner_assertion') "
                    "RETURNING id"
                ),
                {"work_id": work_id},
            ).scalar_one()

            operations = Operations(MigrationContext.configure(connection))
            with patch.object(module, "op", operations):
                module.downgrade()

            exported = (
                connection.execute(
                    text(
                        "SELECT subject, predicate, content, scope, validity, permanence, "
                        "idempotency_key, metadata ->> 'taste_ledger_verdict_id' AS verdict_id, "
                        "metadata #>> '{taste_ledger_work,external_ids,primary}' AS track_uri "
                        "FROM facts WHERE idempotency_key = :idempotency_key"
                    ),
                    {"idempotency_key": f"taste-ledger-verdict:{verdict_id}"},
                )
                .mappings()
                .one()
            )
            assert dict(exported) == {
                "subject": "user",
                "predicate": "likes_artist",
                "content": "I love this",
                "scope": "lifestyle",
                "validity": "active",
                "permanence": "stable",
                "idempotency_key": f"taste-ledger-verdict:{verdict_id}",
                "verdict_id": str(verdict_id),
                "track_uri": "spotify:track:owner",
            }
            assert connection.execute(text("SELECT to_regclass('verdicts')")).scalar_one() is None
    finally:
        engine.dispose()
