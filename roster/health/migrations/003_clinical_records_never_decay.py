"""clinical_records_never_decay

Revision ID: health_003
Revises: health_002
Create Date: 2026-10-18 00:00:00.000000

bu-bvzjhw. Health wrote doses and symptoms with permanence ``standard`` and
medications, conditions and measurements with ``stable``.  ``run_decay_sweep``
fades a fact once ``confidence * exp(-decay_rate * days)`` drops under the class
threshold (about 201 days for ``standard``, about 805 for ``stable``) and expires
it later, while the adherence report and the briefing read only
``validity = 'active'``.  Clinical records are a store of record, so they now
write ``permanent`` (decay_rate 0, which the sweep never selects).

This migration corrects rows written before that change.  For every clinical fact
(``scope = 'health'`` and predicate ``medication``, ``took_dose``, ``condition``,
``symptom`` or ``measurement_*`` written as ``stable`` by ``measurement_log``;
wellness ingest rows share the ``measurement_*`` names but were written as
``standard`` and are left alone) that is still current (``validity`` active,
fading or expired):

* ``permanence`` becomes ``permanent`` and ``decay_rate`` 0, so no later sweep
  touches it.
* ``fading`` and ``expired`` return to ``active``.  The only writer of a fact
  ``expired`` state for these predicates is the decay sweep; its
  ``archived_at`` / ``archived_content`` marks are removed with it.  An expired
  property fact (no ``valid_at``) returns only when it is the newest row for its
  uniqueness key (no live row holds the key, and no newer row of any state
  follows it), so the active-key unique indexes cannot fail the migration and a
  record the owner later retracted or superseded stays gone; a point-in-time
  fact always returns.

Superseded and retracted rows, other scopes and other predicates are untouched.
Health facts other than measurements are classified confidential and have no
``public.memory_catalog`` row; measurements stay at ``normal`` sensitivity on
purpose, so a faded measurement's catalog row may be stale.  This migration does
not write ``public.memory_catalog``; that repair is tracked in bu-l5l9ln.

Guarded with ``to_regclass`` (a schema without the memory ``facts`` table is a
no-op) and idempotent.  There is no downgrade of the data: the previous state was
the defect.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "health_003"
down_revision = "health_002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
            IF to_regclass('facts') IS NULL THEN
                RETURN;
            END IF;
            UPDATE facts f
            SET permanence = 'permanent',
                decay_rate = 0.0,
                metadata = CASE
                    WHEN f.validity = 'expired'
                        THEN f.metadata - 'archived_at' - 'archived_content'
                    ELSE f.metadata
                END,
                validity = CASE
                    WHEN f.validity IN ('fading', 'expired') THEN 'active'
                    ELSE f.validity
                END
            WHERE f.scope = 'health'
              AND (
                  f.predicate IN ('medication', 'took_dose', 'condition', 'symptom')
                  -- measurement_log wrote 'stable'; wellness ingest (google_health,
                  -- home_assistant) writes measurement_* as 'standard' and stays as is.
                  OR (f.predicate LIKE 'measurement\\_%' AND f.permanence = 'stable')
              )
              AND (
                  f.validity IN ('active', 'fading')
                  OR (
                      f.validity = 'expired'
                      AND (
                          -- Point-in-time facts have no uniqueness rule.
                          f.valid_at IS NOT NULL
                          -- A property fact returns only when it is the newest row for its
                          -- key: no live row holds the key and no newer row of any state
                          -- (a retracted or superseded successor) follows it.
                          OR NOT EXISTS (
                              SELECT 1 FROM facts o
                              WHERE o.id <> f.id
                                AND o.scope = f.scope
                                AND o.predicate = f.predicate
                                AND o.valid_at IS NULL
                                AND (
                                    (f.entity_id IS NOT NULL
                                        AND o.entity_id = f.entity_id
                                        AND o.object_entity_id IS NULL)
                                    OR (f.entity_id IS NULL
                                        AND o.entity_id IS NULL
                                        AND o.subject = f.subject)
                                )
                                AND (
                                    o.validity IN ('active', 'fading')
                                    OR (o.created_at, o.id) > (f.created_at, f.id)
                                )
                          )
                      )
                  )
              )
              AND (
                  f.permanence IS DISTINCT FROM 'permanent'
                  OR f.decay_rate IS DISTINCT FROM 0.0
                  OR f.validity <> 'active'
              );
        END
        $$
    """)


def downgrade() -> None:
    # Data correction only: the pre-migration state (clinical records decaying out
    # of the active readers) was the defect, so there is nothing to restore.
    pass
