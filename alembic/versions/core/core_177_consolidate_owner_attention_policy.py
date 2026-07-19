"""Consolidate the Owner Attention Policy into ``public.approvals_policy``.

Revision ID: core_177
Revises: core_176
Create Date: 2026-07-19 00:00:00.000000

``public.insight_settings`` historically owned a second quiet-window triplet.
The global ``public.approvals_policy`` singleton is now the sole owner-attention
authority. This migration is deliberately guarded: core-only databases may not
have the insight table, and core migrations can be replayed across schemas.
"""

from __future__ import annotations

from alembic import op

revision = "core_177"
down_revision = "core_176"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Backfill only an incomplete canonical policy, then remove legacy fields."""
    op.execute(
        """
        DO $$
        DECLARE
            canonical_start INTEGER;
            canonical_end INTEGER;
            canonical_timezone TEXT;
            legacy_start INTEGER;
            legacy_end INTEGER;
            legacy_timezone TEXT;
            selected_timezone TEXT;
            canonical_ready BOOLEAN;
            legacy_ready BOOLEAN;
        BEGIN
            canonical_ready := to_regclass('public.approvals_policy') IS NOT NULL
                AND (
                    SELECT count(*)
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'approvals_policy'
                      AND column_name IN (
                          'id', 'quiet_start_hour', 'quiet_end_hour', 'timezone', 'updated_at'
                        )
                ) = 5;

            IF canonical_ready THEN
                EXECUTE '
                    INSERT INTO public.approvals_policy (id, timezone)
                    VALUES (1, ''UTC'')
                    ON CONFLICT (id) DO NOTHING
                ';
                EXECUTE '
                    SELECT quiet_start_hour, quiet_end_hour, timezone
                    FROM public.approvals_policy
                    WHERE id = 1
                '
                INTO canonical_start, canonical_end, canonical_timezone;

                -- A complete canonical pair is authoritative, even when it
                -- conflicts with legacy data. Runtime handles bad persisted
                -- values fail-open; migration never silently reinterprets them.
                IF canonical_start IS NULL OR canonical_end IS NULL THEN
                    legacy_ready := to_regclass('public.insight_settings') IS NOT NULL
                        AND (
                            SELECT count(*)
                            FROM information_schema.columns
                            WHERE table_schema = 'public'
                              AND table_name = 'insight_settings'
                              AND column_name IN (
                                  'id', 'quiet_start', 'quiet_end', 'quiet_timezone'
                              )
                        ) = 4;

                    IF legacy_ready THEN
                        EXECUTE '
                            SELECT quiet_start, quiet_end, quiet_timezone
                            FROM public.insight_settings
                            WHERE id = 1
                        '
                        INTO legacy_start, legacy_end, legacy_timezone;
                    END IF;

                    IF legacy_ready
                       AND legacy_start IS NOT NULL AND legacy_end IS NOT NULL
                       AND legacy_start BETWEEN 0 AND 23
                       AND legacy_end BETWEEN 0 AND 23
                    THEN
                        selected_timezone := CASE
                            WHEN NULLIF(btrim(legacy_timezone), '') IS NOT NULL
                                THEN legacy_timezone
                            WHEN NULLIF(btrim(canonical_timezone), '') IS NOT NULL
                                THEN canonical_timezone
                            ELSE 'UTC'
                        END;
                        EXECUTE '
                            UPDATE public.approvals_policy
                            SET quiet_start_hour = $1,
                                quiet_end_hour = $2,
                                timezone = $3,
                                updated_at = now()
                            WHERE id = 1
                        '
                        USING legacy_start, legacy_end, selected_timezone;
                    ELSE
                        -- A partial canonical row has no safely inferred
                        -- window. Disable both endpoints instead of mixing
                        -- sources or inventing a timezone.
                        EXECUTE '
                            UPDATE public.approvals_policy
                            SET quiet_start_hour = NULL,
                                quiet_end_hour = NULL,
                                updated_at = now()
                            WHERE id = 1
                        ';
                    END IF;
                END IF;
            END IF;
        END
        $$;
        """
    )

    op.execute(
        """
        DO $$
        DECLARE
            canonical_ready BOOLEAN;
        BEGIN
            -- Never discard the only legacy source if a malformed/pre-core_095
            -- installation does not expose the canonical table shape yet.
            canonical_ready := to_regclass('public.approvals_policy') IS NOT NULL
                AND (
                    SELECT count(*)
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'approvals_policy'
                      AND column_name IN (
                          'id', 'quiet_start_hour', 'quiet_end_hour', 'timezone', 'updated_at'
                        )
                ) = 5;
            IF canonical_ready AND to_regclass('public.insight_settings') IS NOT NULL THEN
                EXECUTE '
                    ALTER TABLE public.insight_settings
                        DROP CONSTRAINT IF EXISTS chk_insight_settings_quiet_start,
                        DROP CONSTRAINT IF EXISTS chk_insight_settings_quiet_end,
                        DROP COLUMN IF EXISTS quiet_start,
                        DROP COLUMN IF EXISTS quiet_end,
                        DROP COLUMN IF EXISTS quiet_timezone
                ';
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    """Restore legacy columns and mirror the canonical row for an older binary."""
    op.execute(
        """
        DO $$
        DECLARE
            canonical_start INTEGER;
            canonical_end INTEGER;
            canonical_timezone TEXT;
            canonical_ready BOOLEAN;
            legacy_ready BOOLEAN;
        BEGIN
            legacy_ready := to_regclass('public.insight_settings') IS NOT NULL
                AND EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'insight_settings'
                      AND column_name = 'id'
                );
            IF legacy_ready THEN
                EXECUTE '
                    ALTER TABLE public.insight_settings
                        ADD COLUMN IF NOT EXISTS quiet_start INTEGER,
                        ADD COLUMN IF NOT EXISTS quiet_end INTEGER,
                        ADD COLUMN IF NOT EXISTS quiet_timezone TEXT
                ';

                canonical_ready := to_regclass('public.approvals_policy') IS NOT NULL
                    AND (
                        SELECT count(*)
                        FROM information_schema.columns
                        WHERE table_schema = 'public'
                          AND table_name = 'approvals_policy'
                          AND column_name IN (
                              'id', 'quiet_start_hour', 'quiet_end_hour', 'timezone'
                          )
                    ) = 4;
                IF canonical_ready THEN
                    EXECUTE '
                        SELECT quiet_start_hour, quiet_end_hour, timezone
                        FROM public.approvals_policy
                        WHERE id = 1
                    '
                    INTO canonical_start, canonical_end, canonical_timezone;
                    EXECUTE '
                        INSERT INTO public.insight_settings
                            (id, quiet_start, quiet_end, quiet_timezone)
                        VALUES (1, $1, $2, $3)
                        ON CONFLICT (id) DO UPDATE
                            SET quiet_start = EXCLUDED.quiet_start,
                                quiet_end = EXCLUDED.quiet_end,
                                quiet_timezone = EXCLUDED.quiet_timezone
                    '
                    USING canonical_start, canonical_end, canonical_timezone;
                END IF;
            END IF;
        END
        $$;
        """
    )
