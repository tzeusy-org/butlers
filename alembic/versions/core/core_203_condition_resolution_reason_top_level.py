"""Relocate a nested condition ``resolution_reason`` to its single top-level home.

Revision ID: core_203
Revises: core_202
Create Date: 2026-08-24 00:00:00.000000

bu-o4i4j: the condition ledger wrote ``resolution_reason`` to two different
places depending on which path resolved the row -- top-level ``metadata`` for
the explicit resolver (``butlers.core.condition_ledger.resolve_condition``,
REQ-owner-condition-ledger-004/006), nested inside
``metadata.identity_payload`` for the identity-version supersede path inside
``reconcile_snapshot``. A reader had to know the provenance before it knew
where to look, and a query written against one location silently returned
nothing for rows resolved by the other. ``_resolve_episode`` now writes the
reason top-level for both paths; the reciprocal ``successor``/``predecessor``
lineage stays under ``identity_payload`` beside the ``version`` it correlates.

This backfill moves whatever the old code already wrote. It is expected to be
a no-op in production -- the supersede path only fires when a producer passes
``Observation.predecessor_fingerprint``, and no producer in this tree does
(only tests exercise it) -- but running it means the single-location claim
does not rest on that survey holding true for every database that ever ran
this code.

Both ledger tables share the same twelve-column shape, so both are treated
identically. Idempotent (a second run finds nothing nested left to move) and
guarded with ``to_regclass`` so it no-ops where a table does not exist. If a
row somehow carries BOTH locations, the existing top-level value is kept --
that is the one the resolver owns and the one every reader now consults.
"""

from __future__ import annotations

from alembic import op

revision = "core_203"
down_revision = "core_202"
branch_labels = None
depends_on = None


LIFT_RESOLUTION_REASON_SQL = """
DO $$
DECLARE
    ledger_table TEXT;
BEGIN
    FOREACH ledger_table IN ARRAY ARRAY[
        'public.infra_conditions', 'public.owner_conditions'
    ]
    LOOP
        IF to_regclass(ledger_table) IS NULL THEN
            CONTINUE;
        END IF;
        EXECUTE format(
            'UPDATE %s SET metadata = '
            '    jsonb_set('
            '        CASE WHEN metadata ? ''resolution_reason'' THEN metadata '
            '             ELSE jsonb_set('
            '                 metadata, ''{resolution_reason}'', '
            '                 metadata -> ''identity_payload'' -> ''resolution_reason'', true) '
            '        END, '
            '        ''{identity_payload}'', '
            '        (metadata -> ''identity_payload'') - ''resolution_reason'', true) '
            'WHERE jsonb_typeof(metadata) = ''object'' '
            '  AND jsonb_typeof(metadata -> ''identity_payload'') = ''object'' '
            '  AND metadata -> ''identity_payload'' ? ''resolution_reason''',
            ledger_table
        );
    END LOOP;
END
$$;
"""


def upgrade() -> None:
    """Move ``identity_payload.resolution_reason`` to top-level ``resolution_reason``."""
    op.execute(LIFT_RESOLUTION_REASON_SQL)


def downgrade() -> None:
    """Do not re-split resolution evidence back across two locations."""
