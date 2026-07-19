"""attention_ledger: one ledger for all proactive owner egress + seeded quiet hours.

Revision ID: core_160
Revises: core_159
Create Date: 2026-07-05 00:00:00.000000

Numbering note: this migration originally landed as core_158. A concurrent
migration (PR #2943, bu-vq97l's model_catalog_defaults reseed) claimed
core_158 first and was itself renumbered to core_159 before merging, so this
revision was renumbered to core_160 to avoid a duplicate-revision collision;
core_158 is intentionally left unused rather than reused. PR #2943 merged
first (2026-07-05), so this revision chains directly off core_159 — rebased
and rechained by the merging reviewer per the plan noted above.

Move 8 (2026-07-04 JARVIS pursuit, slice 1/5) — bu-qvnce.8.

Creates ``public.attention_ledger``, the single durable record of every
proactive-egress decision made at the notify()/insight-delivery-cycle
boundary (delivered, coalesced into a digest, deferred, or suppressed), so
that "the attention policy silently dropped a message" is no longer possible
to observe only by its absence. See ``src/butlers/core/attention_ledger.py``
for the writer and RFC 0011 Amendment 1 for the design rationale.

Also seeds sane owner-level quiet-hours defaults into the two existing
singleton policy tables that currently ship with quiet hours *disabled*
(NULL) fleet-wide with zero owner setup:

* ``public.approvals_policy``  — governs direct notify() owner-page suppression.
* ``public.insight_settings``  — governs the insight-delivery-cycle quiet window.

Both seeds are guarded by ``WHERE quiet_start_hour IS NULL AND
quiet_end_hour IS NULL`` (resp. ``quiet_start``/``quiet_end``) so an owner who
has already configured either policy is never overwritten. This is a
single-owner deployment (uniquosity@gmail.com, Asia/Singapore) — the seeded
window (23:00-08:00 SGT) is a starting default, not a hardcoded constraint;
either table remains editable via its existing surface.
"""

from __future__ import annotations

from alembic import op

revision = "core_160"
down_revision = "core_159"
branch_labels = None
depends_on = None

_ALL_RUNTIME_ROLES = (
    "butler_chronicler_rw",
    "butler_education_rw",
    "butler_finance_rw",
    "butler_general_rw",
    "butler_health_rw",
    "butler_home_rw",
    "butler_lifestyle_rw",
    "butler_messenger_rw",
    "butler_qa_rw",
    "butler_relationship_rw",
    "butler_switchboard_rw",
    "butler_travel_rw",
    "connector_writer",
)

_TABLE_PRIVILEGES = "SELECT, INSERT, UPDATE"

# Sane owner-level quiet-hours default: 23:00-08:00 Asia/Singapore.
_DEFAULT_QUIET_START_HOUR = 23
_DEFAULT_QUIET_END_HOUR = 8
_DEFAULT_QUIET_TIMEZONE = "Asia/Singapore"


def _grant_best_effort(table_fqn: str, privilege: str, role: str) -> None:
    """GRANT privilege ON table TO role; tolerate older DBs missing roles."""
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('{table_fqn}') IS NOT NULL
               AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}')
            THEN
                EXECUTE 'GRANT {privilege} ON TABLE {table_fqn} TO "{role}"';
            END IF;
        EXCEPTION
            WHEN insufficient_privilege THEN NULL;
            WHEN undefined_object THEN NULL;
            WHEN undefined_table THEN NULL;
            WHEN invalid_schema_name THEN NULL;
        END
        $$;
        """
    )


def upgrade() -> None:
    # =========================================================================
    # 1. public.attention_ledger
    # =========================================================================
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.attention_ledger (
            id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            occurred_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            origin_butler     TEXT NOT NULL,
            source            TEXT NOT NULL,
            channel           TEXT,
            intent            TEXT,
            priority_label    TEXT,
            priority_score    INTEGER,
            dedup_key         TEXT,
            outcome           TEXT NOT NULL,
            reason            TEXT,
            notification_ref  TEXT,
            metadata          JSONB,
            CONSTRAINT chk_attention_ledger_source
                CHECK (source IN ('notify', 'insight')),
            CONSTRAINT chk_attention_ledger_outcome
                CHECK (outcome IN ('delivered', 'coalesced', 'deferred', 'suppressed')),
            CONSTRAINT chk_attention_ledger_priority_score
                CHECK (priority_score IS NULL OR priority_score BETWEEN 1 AND 100)
        )
    """)

    # Dashboard/ledger-summary read: recent-first listing.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_attention_ledger_occurred_at
        ON public.attention_ledger (occurred_at DESC)
    """)

    # Notify-path counting: counts grouped by outcome over a window.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_attention_ledger_outcome_occurred_at
        ON public.attention_ledger (outcome, occurred_at DESC)
    """)

    # Cross-fleet dedup correlation (same insight/notify proposed by two butlers).
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_attention_ledger_dedup_key
        ON public.attention_ledger (dedup_key)
        WHERE dedup_key IS NOT NULL
    """)

    for role in _ALL_RUNTIME_ROLES:
        _grant_best_effort("public.attention_ledger", _TABLE_PRIVILEGES, role)

    # =========================================================================
    # 2. Seed owner-level quiet hours (only where nothing is configured yet)
    # =========================================================================
    op.execute(f"""
        UPDATE public.approvals_policy
        SET quiet_start_hour = {_DEFAULT_QUIET_START_HOUR},
            quiet_end_hour = {_DEFAULT_QUIET_END_HOUR},
            timezone = '{_DEFAULT_QUIET_TIMEZONE}',
            updated_at = now()
        WHERE id = 1
          AND quiet_start_hour IS NULL
          AND quiet_end_hour IS NULL
    """)

    # Later core migrations consolidate the legacy insight window into
    # approvals_policy.  Core migrations are also replayed against a fresh
    # schema-local alembic version table, so this historical seed must tolerate
    # a shared public table whose legacy columns were already retired.
    op.execute(f"""
        DO $seed_legacy_insight_quiet_hours$
        BEGIN
            IF to_regclass('public.insight_settings') IS NOT NULL
               AND (
                    SELECT count(*)
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'insight_settings'
                      AND column_name IN (
                          'quiet_start', 'quiet_end', 'quiet_timezone'
                        )
               ) = 3
            THEN
                EXECUTE $update_legacy_insight_quiet_hours$
                    UPDATE public.insight_settings
                    SET quiet_start = {_DEFAULT_QUIET_START_HOUR},
                        quiet_end = {_DEFAULT_QUIET_END_HOUR},
                        quiet_timezone = '{_DEFAULT_QUIET_TIMEZONE}',
                        updated_at = now()
                    WHERE id = 1
                      AND quiet_start IS NULL
                      AND quiet_end IS NULL
                      AND quiet_timezone IS NULL
                $update_legacy_insight_quiet_hours$;
            END IF;
        END
        $seed_legacy_insight_quiet_hours$;
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public.attention_ledger CASCADE")
    # Quiet-hours seed values are left in place on downgrade (data, not schema);
    # an owner who wants them cleared can null them out via the existing
    # settings surfaces.
