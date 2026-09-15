"""Add durable approval-delivery admission state without activating delivery.

Revision ID: approvals_015
Revises: approvals_014
Create Date: 2026-09-13 00:00:00.000000

The tables are additive and intentionally empty on upgrade.  New pending
actions use them as a transactional outbox; no worker is activated here.
"""

from __future__ import annotations

from alembic import op

revision = "approvals_015"
down_revision = "approvals_014"
branch_labels = None
depends_on = None

_PRESENTATION_STATES = (
    "ready",
    "claimed",
    "handoff_started",
    "retry_wait",
    "delivered",
    "collapsed",
    "cancelled",
    "superseded",
    "ambiguous",
)
_REASON_CODES = (
    "quiet_hours",
    "owner_recipient_unavailable",
    "callback_secret_unavailable",
    "transport_unavailable",
    "provider_preflight_failed",
    "provider_outcome_unknown",
    "action_approved",
    "action_rejected",
    "action_expired",
    "action_abandoned",
    "defer_rescheduled",
    "cohort_empty",
)


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS approval_delivery_intents (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            action_id UUID NOT NULL UNIQUE
                REFERENCES pending_actions(id) ON DELETE CASCADE,
            action_key TEXT NOT NULL UNIQUE,
            owning_schema TEXT NOT NULL
                CHECK (owning_schema ~ '^[a-z][a-z0-9_]{0,62}$'),
            origin_butler TEXT NOT NULL,
            admission_mode TEXT NOT NULL
                CHECK (admission_mode IN ('single', 'cohort_anchor', 'collapsed')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT approval_delivery_intents_action_key_check CHECK (
                action_key = 'approval:' || owning_schema || ':' || action_id::text
            ),
            CONSTRAINT approval_delivery_intents_id_key_unique UNIQUE (id, action_key),
            CONSTRAINT approval_delivery_intents_origin_check CHECK (
                origin_butler ~ '^[a-z][a-z0-9_-]{0,62}$'
            )
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS approval_delivery_cohorts (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            cohort_key TEXT NOT NULL UNIQUE,
            owning_schema TEXT NOT NULL
                CHECK (owning_schema ~ '^[a-z][a-z0-9_]{0,62}$'),
            window_started_at TIMESTAMPTZ NOT NULL,
            window_ends_at TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT approval_delivery_cohorts_id_key_unique UNIQUE (id, cohort_key),
            CONSTRAINT approval_delivery_cohorts_window_check
                CHECK (window_ends_at = window_started_at + interval '10 minutes'),
            CONSTRAINT approval_delivery_cohorts_key_check CHECK (
                cohort_key = 'approval-cohort:' || owning_schema || ':' || id::text
            )
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_approval_delivery_cohorts_active_window
        ON approval_delivery_cohorts (window_started_at)
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS approval_delivery_cohort_members (
            cohort_id UUID NOT NULL
                REFERENCES approval_delivery_cohorts(id) ON DELETE CASCADE,
            intent_id UUID NOT NULL UNIQUE
                REFERENCES approval_delivery_intents(id) ON DELETE CASCADE,
            eligible BOOLEAN NOT NULL DEFAULT true,
            joined_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (cohort_id, intent_id)
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS approval_delivery_presentations (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            intent_id UUID REFERENCES approval_delivery_intents(id) ON DELETE CASCADE,
            cohort_id UUID REFERENCES approval_delivery_cohorts(id) ON DELETE CASCADE,
            subject_key TEXT NOT NULL,
            subject_kind TEXT NOT NULL CHECK (subject_kind IN ('action', 'cohort')),
            presentation_mode TEXT NOT NULL
                CHECK (presentation_mode IN ('single', 'burst_digest', 'collapsed')),
            presentation_generation INTEGER NOT NULL
                CHECK (presentation_generation BETWEEN 1 AND 1000),
            presentation_key TEXT NOT NULL UNIQUE,
            state TEXT NOT NULL CHECK (state IN ({_quoted(_PRESENTATION_STATES)})),
            last_reason_code TEXT CHECK (last_reason_code IN ({_quoted(_REASON_CODES)})),
            not_before TIMESTAMPTZ NOT NULL,
            next_attempt_at TIMESTAMPTZ,
            claim_fence BIGINT NOT NULL DEFAULT 0 CHECK (claim_fence >= 0),
            claim_token UUID,
            claim_expires_at TIMESTAMPTZ,
            attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT approval_delivery_presentations_key_check CHECK (
                presentation_key = subject_key || ':p:' || presentation_generation::text
            ),
            CONSTRAINT approval_delivery_presentations_subject_check CHECK (
                (subject_kind = 'action' AND intent_id IS NOT NULL AND cohort_id IS NULL
                    AND presentation_mode IN ('single', 'collapsed'))
                OR
                (subject_kind = 'cohort' AND intent_id IS NULL AND cohort_id IS NOT NULL
                    AND presentation_mode = 'burst_digest')
            ),
            CONSTRAINT approval_delivery_presentations_claim_check CHECK (
                (state IN ('claimed', 'handoff_started')
                    AND claim_token IS NOT NULL AND claim_expires_at IS NOT NULL)
                OR
                (state NOT IN ('claimed', 'handoff_started')
                    AND claim_token IS NULL AND claim_expires_at IS NULL)
            ),
            CONSTRAINT approval_delivery_presentations_schedule_check CHECK (
                (state IN ('ready', 'retry_wait')
                    AND next_attempt_at IS NOT NULL AND next_attempt_at >= not_before)
                OR
                (state NOT IN ('ready', 'retry_wait') AND next_attempt_at IS NULL)
            ),
            CONSTRAINT approval_delivery_presentations_collapsed_check CHECK (
                (presentation_mode = 'collapsed') = (state = 'collapsed')
            ),
            CONSTRAINT approval_delivery_presentations_id_generation_unique
                UNIQUE (id, presentation_generation)
        )
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION validate_approval_delivery_presentation_subject()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.subject_kind = 'action' AND NOT EXISTS (
                SELECT 1 FROM approval_delivery_intents
                 WHERE id = NEW.intent_id AND action_key = NEW.subject_key
            ) THEN
                RAISE EXCEPTION 'approval delivery action subject/key mismatch';
            ELSIF NEW.subject_kind = 'cohort' AND NOT EXISTS (
                SELECT 1 FROM approval_delivery_cohorts
                 WHERE id = NEW.cohort_id AND cohort_key = NEW.subject_key
            ) THEN
                RAISE EXCEPTION 'approval delivery cohort subject/key mismatch';
            END IF;
            RETURN NEW;
        END $$;
        DROP TRIGGER IF EXISTS trg_approval_delivery_presentation_subject
            ON approval_delivery_presentations;
        CREATE TRIGGER trg_approval_delivery_presentation_subject
        BEFORE INSERT OR UPDATE ON approval_delivery_presentations
        FOR EACH ROW EXECUTE FUNCTION validate_approval_delivery_presentation_subject()
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_approval_delivery_action_generation
        ON approval_delivery_presentations (intent_id, presentation_generation)
        WHERE intent_id IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_approval_delivery_cohort_generation
        ON approval_delivery_presentations (cohort_id, presentation_generation)
        WHERE cohort_id IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_approval_delivery_action_current_sendable
        ON approval_delivery_presentations (intent_id)
        WHERE intent_id IS NOT NULL AND state IN ('ready', 'claimed', 'retry_wait');
        CREATE UNIQUE INDEX IF NOT EXISTS ux_approval_delivery_cohort_current_sendable
        ON approval_delivery_presentations (cohort_id)
        WHERE cohort_id IS NOT NULL AND state IN ('ready', 'claimed', 'retry_wait')
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_approval_delivery_presentations_due
        ON approval_delivery_presentations (next_attempt_at, id)
        WHERE state IN ('ready', 'retry_wait')
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_approval_delivery_presentations_expired_lease
        ON approval_delivery_presentations (claim_expires_at, id)
        WHERE state IN ('claimed', 'handoff_started')
        """
    )
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS approval_delivery_attempts (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            presentation_id UUID NOT NULL,
            presentation_generation INTEGER NOT NULL CHECK (presentation_generation >= 1),
            attempt_number INTEGER NOT NULL CHECK (attempt_number >= 1),
            claim_fence BIGINT NOT NULL CHECK (claim_fence >= 1),
            started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            completed_at TIMESTAMPTZ,
            outcome TEXT NOT NULL
                CHECK (outcome IN ('started', 'confirmed', 'safe_retry', 'ambiguous')),
            reason_code TEXT CHECK (reason_code IN ({_quoted(_REASON_CODES)})),
            provider_reference TEXT CHECK (
                provider_reference IS NULL OR length(provider_reference) BETWEEN 1 AND 256
            ),
            FOREIGN KEY (presentation_id, presentation_generation)
                REFERENCES approval_delivery_presentations(id, presentation_generation)
                ON DELETE CASCADE,
            CHECK (
                (outcome = 'started' AND completed_at IS NULL)
                OR
                (outcome <> 'started' AND completed_at IS NOT NULL
                    AND completed_at >= started_at)
            )
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_approval_delivery_attempt_start
        ON approval_delivery_attempts (presentation_id, attempt_number)
        WHERE outcome = 'started';
        CREATE UNIQUE INDEX IF NOT EXISTS ux_approval_delivery_attempt_result
        ON approval_delivery_attempts (presentation_id, attempt_number)
        WHERE outcome <> 'started'
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_approval_delivery_attempts_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'approval_delivery_attempts is append-only: % is not allowed', TG_OP;
        END $$;
        DROP TRIGGER IF EXISTS trg_approval_delivery_attempts_immutable
            ON approval_delivery_attempts;
        CREATE TRIGGER trg_approval_delivery_attempts_immutable
        BEFORE UPDATE OR DELETE ON approval_delivery_attempts
        FOR EACH ROW EXECUTE FUNCTION prevent_approval_delivery_attempts_mutation()
        """
    )
    op.execute(
        """
        DO $$ BEGIN
            IF to_regclass('approval_events') IS NOT NULL THEN
                ALTER TABLE approval_events
                    DROP CONSTRAINT IF EXISTS approval_events_type_check;
                ALTER TABLE approval_events ADD CONSTRAINT approval_events_type_check
                CHECK (event_type IN (
                    'action_queued', 'action_auto_approved', 'action_approved',
                    'action_rejected', 'action_expired', 'action_abandoned',
                    'action_execution_succeeded', 'action_execution_failed',
                    'rule_created', 'rule_revoked', 'promotion_suggested',
                    'promotion_confirmed', 'promotion_dismissed', 'promotion_superseded',
                    'demotion_suggested', 'demotion_confirmed', 'demotion_dismissed',
                    'approval_delivery_terminal'
                ));
            END IF;
        END $$
        """
    )
    op.execute(
        """
        DO $$ BEGIN
            IF to_regclass('approval_events') IS NOT NULL THEN
                ALTER TABLE approval_events
                    DROP CONSTRAINT IF EXISTS approval_events_link_check;
                ALTER TABLE approval_events ADD CONSTRAINT approval_events_link_check CHECK (
                    action_id IS NOT NULL OR rule_id IS NOT NULL OR event_type IN (
                        'promotion_suggested', 'promotion_confirmed', 'promotion_dismissed',
                        'promotion_superseded', 'demotion_suggested', 'demotion_confirmed',
                        'demotion_dismissed', 'approval_delivery_terminal'
                    )
                );
            END IF;
        END $$
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        DECLARE
            names TEXT[] := ARRAY[
                'approval_delivery_attempts',
                'approval_delivery_cohort_members',
                'approval_delivery_presentations',
                'approval_delivery_cohorts',
                'approval_delivery_intents'
            ];
            name TEXT;
            lock_targets TEXT;
            has_rows BOOLEAN;
        BEGIN
            SELECT string_agg(format('%I.%I', current_schema(), item), ', ')
              INTO lock_targets
              FROM unnest(names) AS item
             WHERE to_regclass(format('%I.%I', current_schema(), item)) IS NOT NULL;
            IF lock_targets IS NOT NULL THEN
                EXECUTE 'LOCK TABLE ' || lock_targets || ' IN ACCESS EXCLUSIVE MODE';
            END IF;
            FOREACH name IN ARRAY names LOOP
                IF to_regclass(format('%I.%I', current_schema(), name)) IS NOT NULL THEN
                    EXECUTE format('SELECT EXISTS (SELECT 1 FROM %I.%I)', current_schema(), name)
                        INTO has_rows;
                    IF has_rows THEN
                        RAISE EXCEPTION '%',
                            'Cannot downgrade approvals_015 while approval delivery '
                            || 'recovery data exists';
                    END IF;
                END IF;
            END LOOP;
            IF EXISTS (
                SELECT 1 FROM approval_events
                 WHERE event_type = 'approval_delivery_terminal'
            ) THEN
                RAISE EXCEPTION
                    'Cannot downgrade approvals_015 while approval delivery audit data exists';
            END IF;
        END $$
        """
    )
    op.execute("DROP TABLE IF EXISTS approval_delivery_attempts")
    op.execute("DROP FUNCTION IF EXISTS prevent_approval_delivery_attempts_mutation()")
    op.execute("DROP TABLE IF EXISTS approval_delivery_cohort_members")
    op.execute("DROP TABLE IF EXISTS approval_delivery_presentations")
    op.execute("DROP TABLE IF EXISTS approval_delivery_cohorts")
    op.execute("DROP TABLE IF EXISTS approval_delivery_intents")
    op.execute("DROP FUNCTION IF EXISTS validate_approval_delivery_presentation_subject()")
    op.execute(
        """
        DO $$ BEGIN
            IF to_regclass('approval_events') IS NOT NULL THEN
                ALTER TABLE approval_events
                    DROP CONSTRAINT IF EXISTS approval_events_type_check;
                ALTER TABLE approval_events ADD CONSTRAINT approval_events_type_check
                CHECK (event_type IN (
                    'action_queued', 'action_auto_approved', 'action_approved',
                    'action_rejected', 'action_expired', 'action_abandoned',
                    'action_execution_succeeded', 'action_execution_failed',
                    'rule_created', 'rule_revoked', 'promotion_suggested',
                    'promotion_confirmed', 'promotion_dismissed', 'promotion_superseded',
                    'demotion_suggested', 'demotion_confirmed', 'demotion_dismissed'
                ));
            END IF;
        END $$
        """
    )
    op.execute(
        """
        DO $$ BEGIN
            IF to_regclass('approval_events') IS NOT NULL THEN
                ALTER TABLE approval_events
                    DROP CONSTRAINT IF EXISTS approval_events_link_check;
                ALTER TABLE approval_events ADD CONSTRAINT approval_events_link_check CHECK (
                    action_id IS NOT NULL OR rule_id IS NOT NULL OR event_type IN (
                        'promotion_suggested', 'promotion_confirmed', 'promotion_dismissed',
                        'promotion_superseded', 'demotion_suggested', 'demotion_confirmed',
                        'demotion_dismissed'
                    )
                );
            END IF;
        END $$
        """
    )
