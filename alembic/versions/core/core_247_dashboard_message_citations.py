"""Add canonical answer citations and per-message butler attribution."""

from __future__ import annotations

from alembic import op

revision = "core_247"
down_revision = "core_246"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE public.dashboard_messages "
        "ADD COLUMN IF NOT EXISTS citations JSONB NULL, "
        "ADD COLUMN IF NOT EXISTS routed_butler TEXT NULL"
    )
    # Legacy sources are names, not links. Preserve that distinction during
    # the additive rollout and leave malformed rows unknown for inspection.
    # Nested CASE branches are deliberate: jsonb_array_elements* must never be
    # evaluated against a malformed, non-array legacy value.
    op.execute(
        """
        UPDATE public.dashboard_messages
        SET citations = CASE
            WHEN jsonb_typeof(sources) <> 'array' OR jsonb_array_length(sources) = 0
                THEN NULL
            WHEN EXISTS (
                SELECT 1 FROM jsonb_array_elements(sources) AS entry
                WHERE jsonb_typeof(entry) <> 'string'
            ) THEN NULL
            WHEN EXISTS (
                SELECT 1 FROM jsonb_array_elements_text(sources) AS entry(item)
                WHERE btrim(item) = ''
                   OR char_length(btrim(item)) > 200
                   OR item ~ '[[:cntrl:]]'
            ) THEN NULL
            ELSE (
                SELECT jsonb_agg(
                    jsonb_build_object(
                        'label', btrim(item), 'target', NULL, 'kind', 'unlinked'
                    )
                    ORDER BY ordinality
                )
                FROM jsonb_array_elements_text(sources)
                     WITH ORDINALITY AS entries(item, ordinality)
            )
        END
        WHERE citations IS NULL
          AND sources IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE public.dashboard_messages "
        "DROP COLUMN IF EXISTS citations, DROP COLUMN IF EXISTS routed_butler"
    )
