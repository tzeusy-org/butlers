"""classify covered-local-day witnesses by durable Chronicler-local proof

Revision ID: chronicler_024
Revises: chronicler_023
Create Date: 2026-08-02 00:00:00.000000

``chronicler_023`` established ``covered_local_days`` but its historical
backfill treated every episode and every day-close cache key as proof. That is
too broad: calendar intent is never proof of lived activity, tombstoned rows
are no longer active evidence, and a cache row is only usable when the
deterministic date-binding admission contract accepted it.

This migration keeps every historic row for auditability while assigning an
explicit origin. Only the four authoritative origins are eligible to establish
the archive floor or an exact covered day at read time. Everything else stays
``legacy_unverified`` and deliberately under-covers history rather than
fabricating a quiet day.
"""

from __future__ import annotations

from alembic import op

revision = "chronicler_024"
down_revision = "chronicler_023"
branch_labels = None
depends_on = None

_ORIGIN_LEGACY_UNVERIFIED = "legacy_unverified"
_ORIGIN_DAY_CLOSE_SUCCESS = "day_close_success"
_ORIGIN_DAY_CLOSE_CACHE = "day_close_cache"
_ORIGIN_EPISODE_ACTIVITY = "episode_activity"
_ORIGIN_EPISODE_EVIDENCE = "episode_evidence"


def upgrade() -> None:
    # Defaulting first is intentionally fail-closed for an interrupted rolling
    # deploy: an old writer that omits ``origin`` produces an unverified row,
    # not an accidental archive witness.
    op.execute(
        f"""
        ALTER TABLE covered_local_days
        ADD COLUMN IF NOT EXISTS origin TEXT NOT NULL
            DEFAULT '{_ORIGIN_LEGACY_UNVERIFIED}'
        """
    )
    op.execute(
        "ALTER TABLE covered_local_days DROP CONSTRAINT IF EXISTS covered_local_days_origin_check"
    )
    op.execute(
        f"""
        ALTER TABLE covered_local_days
        ADD CONSTRAINT covered_local_days_origin_check
        CHECK (origin IN (
            '{_ORIGIN_LEGACY_UNVERIFIED}',
            '{_ORIGIN_DAY_CLOSE_SUCCESS}',
            '{_ORIGIN_DAY_CLOSE_CACHE}',
            '{_ORIGIN_EPISODE_ACTIVITY}',
            '{_ORIGIN_EPISODE_EVIDENCE}'
        ))
        """
    )

    # Reclassify the broad chronicler_023 backfill from durable, local proof
    # only. A valid cache must be active, pass its stored date-binding
    # admission, and prove its [start_at, end_at) equals the exact owner-local
    # day window. A matching cache key/date label alone can still name a UTC
    # day for a non-UTC owner, which is not evidence for the claimed local day.
    # For episodes, intent is intentionally absent: a planned calendar block is
    # not evidence that the day was lived. The timezone expression is per-row
    # because each witness is owner-local. Historic ``timezone`` text was not
    # constrained, so CASE keeps a malformed legacy value safely unverified
    # instead of aborting the entire migration.
    op.execute(
        f"""
        UPDATE covered_local_days AS coverage
        SET origin = CASE
            WHEN EXISTS (
                SELECT 1
                FROM tier2_cache AS cache
                WHERE cache.cache_key = 'day_close:' || coverage.local_date::text
                  AND cache.superseded_at IS NULL
                  AND cache.invalid_reason IS NULL
                  AND cache.date_label = coverage.local_date::text
                  AND CASE WHEN EXISTS (
                      SELECT 1
                      FROM pg_timezone_names AS timezone_name
                      WHERE timezone_name.name = coverage.timezone
                  ) THEN (
                      cache.start_at = (
                          coverage.local_date::timestamp AT TIME ZONE coverage.timezone
                      )
                      AND cache.end_at = (
                          (coverage.local_date + 1)::timestamp AT TIME ZONE coverage.timezone
                      )
                  ) ELSE FALSE END
            ) THEN '{_ORIGIN_DAY_CLOSE_CACHE}'
            WHEN EXISTS (
                SELECT 1
                FROM episodes AS episode
                WHERE episode.tombstone_at IS NULL
                  AND episode.layer = 'activity'
                  AND (
                      CASE WHEN EXISTS (
                          SELECT 1
                          FROM pg_timezone_names AS timezone_name
                          WHERE timezone_name.name = coverage.timezone
                      ) THEN (episode.start_at AT TIME ZONE coverage.timezone)::date
                      ELSE NULL
                      END
                  ) = coverage.local_date
            ) THEN '{_ORIGIN_EPISODE_ACTIVITY}'
            WHEN EXISTS (
                SELECT 1
                FROM episodes AS episode
                WHERE episode.tombstone_at IS NULL
                  AND episode.layer = 'evidence'
                  AND (
                      CASE WHEN EXISTS (
                          SELECT 1
                          FROM pg_timezone_names AS timezone_name
                          WHERE timezone_name.name = coverage.timezone
                      ) THEN (episode.start_at AT TIME ZONE coverage.timezone)::date
                      ELSE NULL
                      END
                  ) = coverage.local_date
            ) THEN '{_ORIGIN_EPISODE_EVIDENCE}'
            ELSE '{_ORIGIN_LEGACY_UNVERIFIED}'
        END
        """
    )

    op.execute(
        f"""
        CREATE INDEX IF NOT EXISTS covered_local_days_authoritative_idx
        ON covered_local_days (timezone, local_date)
        WHERE origin IN (
            '{_ORIGIN_DAY_CLOSE_SUCCESS}',
            '{_ORIGIN_DAY_CLOSE_CACHE}',
            '{_ORIGIN_EPISODE_ACTIVITY}',
            '{_ORIGIN_EPISODE_EVIDENCE}'
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS covered_local_days_authoritative_idx")
    op.execute(
        "ALTER TABLE covered_local_days DROP CONSTRAINT IF EXISTS covered_local_days_origin_check"
    )
    op.execute("ALTER TABLE covered_local_days DROP COLUMN IF EXISTS origin")
