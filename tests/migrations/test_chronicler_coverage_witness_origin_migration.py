"""Real PostgreSQL regression coverage for Chronicler coverage origins.

``chronicler_023`` made every historical episode/cache-shaped row look like a
coverage witness. This test upgrades a genuine pre-origin database through the
next migration and proves only Chronicler-local, durable proof is promoted.
"""

from __future__ import annotations

import shutil
from datetime import UTC, date, datetime

import asyncpg
import pytest

from alembic import command
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import create_migration_db, migration_db_name

_DOCKER_AVAILABLE = shutil.which("docker") is not None


@pytest.fixture(scope="module")
def pre_origin_chronicler_db_url(postgres_container) -> str:
    """A real Chronicler schema immediately after ``chronicler_023``."""
    db_url = create_migration_db(postgres_container, migration_db_name())
    command.upgrade(
        _build_alembic_config(db_url, chains=["chronicler"]),
        "chronicler@chronicler_023",
    )
    return db_url


@pytest.mark.integration
@pytest.mark.skipif(not _DOCKER_AVAILABLE, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_upgrade_classifies_historic_coverage_by_local_proof(
    pre_origin_chronicler_db_url: str,
) -> None:
    """Intent, tombstones, and timezone-mismatched rows remain unverified."""
    pre_origin_pool = await asyncpg.create_pool(
        pre_origin_chronicler_db_url, min_size=1, max_size=2
    )
    try:
        await pre_origin_pool.execute(
            """
            INSERT INTO source_adapter_state (source_name, chronicler_compatibility)
            VALUES ('migration.coverage-origin', 'supported')
            """
        )
        await pre_origin_pool.executemany(
            """
            INSERT INTO covered_local_days (local_date, timezone)
            VALUES ($1, $2)
            """,
            [
                (date(2026, 5, 1), "UTC"),  # valid day-close cache
                (date(2026, 5, 2), "UTC"),  # intent only
                (date(2026, 5, 3), "UTC"),  # evidence
                (date(2026, 5, 4), "UTC"),  # activity
                (date(2026, 5, 7), "UTC"),  # tombstoned activity
                (date(2026, 5, 8), "UTC"),  # invalid day-close cache
                (date(2026, 5, 9), "Not/ARealTimezone"),  # bad historic timezone text
                (date(2026, 5, 5), "Asia/Singapore"),  # wrong local date in owner tz
                (date(2026, 5, 6), "Asia/Singapore"),  # activity at 00:30 SGT
                (date(2026, 5, 12), "UTC"),  # activity later tombstoned by an override
                (date(2026, 5, 13), "UTC"),  # evidence later moved by an override
                (date(2026, 5, 15), "UTC"),  # activity later moved by an override
                (date(2026, 5, 17), "UTC"),  # evidence later tombstoned by an override
                # Matching key/date-label alone is not proof: this cache uses
                # UTC midnights rather than the Singapore-local day window.
                (date(2026, 5, 10), "Asia/Singapore"),
                # A cache with the exact Singapore-local UTC window is proof.
                (date(2026, 5, 11), "Asia/Singapore"),
            ],
        )
        await pre_origin_pool.execute(
            """
            INSERT INTO tier2_cache (
                cache_key, start_at, end_at, prose, date_label, invalid_reason
            )
            VALUES (
                'day_close:2026-05-01',
                $1,
                $2,
                'This valid day-close summary is durably bound to 2026-05-01.',
                '2026-05-01',
                NULL
            )
            """,
            datetime(2026, 5, 1, 0, 0, tzinfo=UTC),
            datetime(2026, 5, 2, 0, 0, tzinfo=UTC),
        )
        await pre_origin_pool.execute(
            """
            INSERT INTO tier2_cache (
                cache_key, start_at, end_at, prose, date_label, invalid_reason
            )
            VALUES (
                'day_close:2026-05-08',
                $1,
                $2,
                'A cache row with an invalid date binding must not prove coverage.',
                NULL,
                'date_mismatch'
            )
            """,
            datetime(2026, 5, 8, 0, 0, tzinfo=UTC),
            datetime(2026, 5, 9, 0, 0, tzinfo=UTC),
        )
        await pre_origin_pool.executemany(
            """
            INSERT INTO tier2_cache (
                cache_key, start_at, end_at, prose, date_label, invalid_reason
            )
            VALUES ($1, $2, $3, $4, $5, NULL)
            """,
            [
                (
                    "day_close:2026-05-10",
                    datetime(2026, 5, 10, 0, 0, tzinfo=UTC),
                    datetime(2026, 5, 11, 0, 0, tzinfo=UTC),
                    "A date-labelled cache with the wrong UTC day window.",
                    "2026-05-10",
                ),
                (
                    "day_close:2026-05-11",
                    datetime(2026, 5, 10, 16, 0, tzinfo=UTC),
                    datetime(2026, 5, 11, 16, 0, tzinfo=UTC),
                    "A date-labelled cache with the exact Singapore day window.",
                    "2026-05-11",
                ),
            ],
        )
        await pre_origin_pool.executemany(
            """
            INSERT INTO episodes (
                source_name, source_ref, episode_type, start_at, end_at, layer, tombstone_at
            )
            VALUES ($1, $2, 'migration_fixture', $3, $4, $5, $6)
            """,
            [
                (
                    "migration.coverage-origin",
                    "intent-only",
                    datetime(2026, 5, 2, 12, 0, tzinfo=UTC),
                    datetime(2026, 5, 2, 13, 0, tzinfo=UTC),
                    "intent",
                    None,
                ),
                (
                    "migration.coverage-origin",
                    "evidence",
                    datetime(2026, 5, 3, 12, 0, tzinfo=UTC),
                    datetime(2026, 5, 3, 13, 0, tzinfo=UTC),
                    "evidence",
                    None,
                ),
                (
                    "migration.coverage-origin",
                    "activity",
                    datetime(2026, 5, 4, 12, 0, tzinfo=UTC),
                    datetime(2026, 5, 4, 13, 0, tzinfo=UTC),
                    "activity",
                    None,
                ),
                (
                    "migration.coverage-origin",
                    "tombstoned-activity",
                    datetime(2026, 5, 7, 12, 0, tzinfo=UTC),
                    datetime(2026, 5, 7, 13, 0, tzinfo=UTC),
                    "activity",
                    datetime(2026, 5, 8, 0, 0, tzinfo=UTC),
                ),
                (
                    "migration.coverage-origin",
                    "singapore-activity",
                    datetime(2026, 5, 5, 16, 30, tzinfo=UTC),
                    datetime(2026, 5, 5, 17, 30, tzinfo=UTC),
                    "activity",
                    None,
                ),
                (
                    "migration.coverage-origin",
                    "override-tombstoned-activity",
                    datetime(2026, 5, 12, 12, 0, tzinfo=UTC),
                    datetime(2026, 5, 12, 13, 0, tzinfo=UTC),
                    "activity",
                    None,
                ),
                (
                    "migration.coverage-origin",
                    "override-moved-evidence",
                    datetime(2026, 5, 13, 12, 0, tzinfo=UTC),
                    datetime(2026, 5, 13, 13, 0, tzinfo=UTC),
                    "evidence",
                    None,
                ),
                (
                    "migration.coverage-origin",
                    "override-moved-activity",
                    datetime(2026, 5, 15, 12, 0, tzinfo=UTC),
                    datetime(2026, 5, 15, 13, 0, tzinfo=UTC),
                    "activity",
                    None,
                ),
                (
                    "migration.coverage-origin",
                    "override-tombstoned-evidence",
                    datetime(2026, 5, 17, 12, 0, tzinfo=UTC),
                    datetime(2026, 5, 17, 13, 0, tzinfo=UTC),
                    "evidence",
                    None,
                ),
            ],
        )
        override_targets = {
            row["source_ref"]: row["id"]
            for row in await pre_origin_pool.fetch(
                """
                SELECT id, source_ref
                FROM episodes
                WHERE source_name = 'migration.coverage-origin'
                  AND source_ref = ANY($1::text[])
                """,
                [
                    "override-tombstoned-activity",
                    "override-moved-evidence",
                    "override-moved-activity",
                    "override-tombstoned-evidence",
                ],
            )
        }
        await pre_origin_pool.executemany(
            """
            INSERT INTO overrides (
                target_kind, target_id, corrected_start_at, corrected_end_at,
                corrected_tombstone_at, note
            )
            VALUES ('episode', $1, $2, $3, $4, $5)
            """,
            [
                (
                    override_targets["override-tombstoned-activity"],
                    None,
                    None,
                    datetime(2026, 5, 12, 20, 0, tzinfo=UTC),
                    "The historical activity was retracted.",
                ),
                (
                    override_targets["override-moved-evidence"],
                    datetime(2026, 5, 14, 12, 0, tzinfo=UTC),
                    datetime(2026, 5, 14, 13, 0, tzinfo=UTC),
                    None,
                    "The evidence belonged to the following day.",
                ),
                (
                    override_targets["override-moved-activity"],
                    datetime(2026, 5, 16, 12, 0, tzinfo=UTC),
                    datetime(2026, 5, 16, 13, 0, tzinfo=UTC),
                    None,
                    "The activity belonged to the following day.",
                ),
                (
                    override_targets["override-tombstoned-evidence"],
                    None,
                    None,
                    datetime(2026, 5, 17, 20, 0, tzinfo=UTC),
                    "The historical evidence was retracted.",
                ),
            ],
        )
    finally:
        await pre_origin_pool.close()

    command.upgrade(
        _build_alembic_config(pre_origin_chronicler_db_url, chains=["chronicler"]),
        "chronicler@head",
    )

    pool = await asyncpg.create_pool(pre_origin_chronicler_db_url, min_size=1, max_size=2)
    try:
        rows = await pool.fetch(
            """
            SELECT local_date, timezone, origin
            FROM covered_local_days
            ORDER BY timezone, local_date
            """
        )
    finally:
        await pool.close()

    assert {(row["timezone"], row["local_date"]): row["origin"] for row in rows} == {
        ("UTC", date(2026, 5, 1)): "day_close_cache",
        ("UTC", date(2026, 5, 2)): "legacy_unverified",
        ("UTC", date(2026, 5, 3)): "episode_evidence",
        ("UTC", date(2026, 5, 4)): "episode_activity",
        ("UTC", date(2026, 5, 7)): "legacy_unverified",
        ("UTC", date(2026, 5, 8)): "legacy_unverified",
        ("Not/ARealTimezone", date(2026, 5, 9)): "legacy_unverified",
        ("Asia/Singapore", date(2026, 5, 5)): "legacy_unverified",
        ("Asia/Singapore", date(2026, 5, 6)): "episode_activity",
        ("Asia/Singapore", date(2026, 5, 10)): "legacy_unverified",
        ("Asia/Singapore", date(2026, 5, 11)): "day_close_cache",
        ("UTC", date(2026, 5, 12)): "legacy_unverified",
        ("UTC", date(2026, 5, 13)): "legacy_unverified",
        ("UTC", date(2026, 5, 14)): "episode_evidence",
        ("UTC", date(2026, 5, 15)): "legacy_unverified",
        ("UTC", date(2026, 5, 16)): "episode_activity",
        ("UTC", date(2026, 5, 17)): "legacy_unverified",
    }
