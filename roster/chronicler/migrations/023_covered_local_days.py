"""covered_local_days witness + tier2_cache admission columns

Revision ID: chronicler_023
Revises: chronicler_022
Create Date: 2026-07-25 00:00:00.000000

Landing the ``clarify-chronicles-narrative-truth`` contract (bu-ep4ks.1):
Chronicles previously had no durable evidence for "was this local day
actually chronicled", so an absent/failed read and a genuinely quiet day
were both rendered as a calm "Quiet day." This migration adds:

1. ``covered_local_days`` — the authoritative, Chronicler-owned
   covered-local-day witness (design decision 1). A row is written only
   after the owning coverage computation's required Chronicle evidence
   reads for that exact owner-timezone local date completed without an
   owned query failure (see ``editorial.record_coverage_witness``, called
   from the ``chronicler_day_close`` completion hook on every successful
   nightly dispatch, independent of whether the dispatch produced
   non-empty prose — a covered quiet day has no episode).

   Backfill: seeds witnesses for local days that already have durable
   proof of chronicle activity — either a ``day_close:{date}`` tier2_cache
   row (the pipeline ran and completed) or at least one ``episodes`` row
   that local day. This intentionally UNDER-covers history: a genuinely
   quiet historical day that produced empty day-close output before this
   migration existed has no positive evidence and is left uncovered
   (renders `unavailable`, not `quiet`) rather than guessed. Per design
   decision 1, guessing coverage from an operational proxy is explicitly
   prohibited; under-covering is the safe direction (never fabricate
   calm), over-covering is not.

2. ``tier2_cache.date_label`` / ``tier2_cache.invalid_reason`` — the
   cache-admission contract (design decision 2). ``date_label`` records
   the structured local date the writer bound the candidate prose to;
   ``invalid_reason`` (``inadmissible_prose`` | ``date_mismatch``) marks a
   row that failed the deterministic shape/date-binding predicate so the
   reader can contain it (never render its prose) without deleting it.

   Backfill: existing ``day_close:%`` rows predate this predicate and
   carry no ``date_label``, so their binding to the owner-timezone local
   day they claim is unproven. They are marked
   ``invalid_reason = 'date_mismatch'`` so they are contained on read
   rather than trusted retroactively; the next admissible day-close write
   for that date clears it. Non-day-close cache rows (e.g.
   ``episode_explain:*``) are untouched — the admission contract is
   day-close-prose-specific.
"""

from __future__ import annotations

from alembic import op

revision = "chronicler_023"
down_revision = "chronicler_022"
branch_labels = None
depends_on = None

# Matches editorial._resolve_owner_tz_default's stable fallback (router.py);
# the backfill has no runtime owner-settings access, so it anchors to the
# same fallback timezone used everywhere else in chronicler when none is
# supplied explicitly.
_BACKFILL_TIMEZONE = "Asia/Singapore"


def upgrade() -> None:
    # ── covered_local_days ──────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS covered_local_days (
            local_date  DATE NOT NULL,
            timezone    TEXT NOT NULL,
            covered_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (local_date, timezone)
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS covered_local_days_timezone_idx
        ON covered_local_days (timezone, local_date)
    """)

    # Backfill from tier2_cache day_close rows (pipeline ran and completed).
    op.execute(
        f"""
        INSERT INTO covered_local_days (local_date, timezone)
        SELECT DISTINCT
            (substring(cache_key from 11))::date,
            '{_BACKFILL_TIMEZONE}'
        FROM tier2_cache
        WHERE cache_key LIKE 'day_close:%'
        ON CONFLICT DO NOTHING
        """
    )

    # Backfill from episodes (activity was captured that local day).
    op.execute(
        f"""
        INSERT INTO covered_local_days (local_date, timezone)
        SELECT DISTINCT
            (start_at AT TIME ZONE '{_BACKFILL_TIMEZONE}')::date,
            '{_BACKFILL_TIMEZONE}'
        FROM episodes
        ON CONFLICT DO NOTHING
        """
    )

    # ── tier2_cache admission columns ───────────────────────────────────
    op.execute("ALTER TABLE tier2_cache ADD COLUMN IF NOT EXISTS date_label TEXT")
    op.execute("ALTER TABLE tier2_cache ADD COLUMN IF NOT EXISTS invalid_reason TEXT")
    op.execute("""
        ALTER TABLE tier2_cache
        ADD CONSTRAINT tier2_cache_invalid_reason_check
        CHECK (invalid_reason IS NULL OR invalid_reason IN ('inadmissible_prose', 'date_mismatch'))
    """)

    # Contain pre-existing day-close rows: unproven date binding.
    op.execute("""
        UPDATE tier2_cache
        SET invalid_reason = 'date_mismatch'
        WHERE cache_key LIKE 'day_close:%'
          AND date_label IS NULL
          AND invalid_reason IS NULL
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE tier2_cache DROP CONSTRAINT IF EXISTS tier2_cache_invalid_reason_check")
    op.execute("ALTER TABLE tier2_cache DROP COLUMN IF EXISTS invalid_reason")
    op.execute("ALTER TABLE tier2_cache DROP COLUMN IF EXISTS date_label")
    op.execute("DROP TABLE IF EXISTS covered_local_days")
