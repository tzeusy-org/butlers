"""recover_updated_condition_statuses

Revision ID: health_006
Revises: health_005
Create Date: 2026-11-03 00:00:00.000000

bu-08zldr (follow-up to bu-cgl4na / health_004).  The old decay sweep stripped
``metadata.status`` from Health ``condition`` facts.  When the owner then updated a
stripped condition without naming a status, ``condition_update`` copied the status-less
metadata forward and wrote the content as ``"<name>: active"`` (the default), so the
superseding row lost the real status.  ``health_004`` could not repair that row: its
content already said ``active``, so it was either stamped ``status = 'active'`` (tagged
``status_restored_by = 'health_004'``) or, if written after ``health_004`` ran, left with
no status at all.  The real status survives only in the superseded predecessor's content
(``"<name>: managed"``).

This migration walks ``supersedes_id`` back from each such live row, through predecessors
that carry no ``metadata.status``, to the nearest one whose content suffix is a status
different from the live row's, and restores that status.

Candidates (scope ``health``, predicate ``condition``, validity active or fading, content
ending in ``: active``; a row whose content says managed or resolved is authoritative):

* no ``metadata.status`` at all; or
* ``status = 'active'`` tagged ``health_004`` whose immediate predecessor is not tagged,
  i.e. ``health_004`` stamped it in place.  A row inheriting the tag from a live
  predecessor (the owner updated it after the repair) is left alone, because its status
  may be one the owner chose.

A row with no differing status-less predecessor is untouched.  Each recovered row is
tagged ``metadata.status_recovered_by = 'health_006'``, distinct from
``status_restored_by``.

Guarded with ``to_regclass`` (no memory ``facts`` table is a no-op) and idempotent (a
recovered row carries a non-``active`` status and no longer matches).  The downgrade does
not touch data, as in ``health_003`` / ``health_004``: the previous state was the defect,
and a later owner update may have copied the tag forward.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "health_006"
down_revision = "health_005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
            IF to_regclass('facts') IS NULL THEN
                RETURN;
            END IF;
            WITH RECURSIVE cand AS (
                SELECT f.id,
                       f.supersedes_id,
                       'active' AS cur
                FROM facts f
                WHERE f.scope = 'health'
                  AND f.predicate = 'condition'
                  AND f.validity IN ('active', 'fading')
                  AND f.supersedes_id IS NOT NULL
                  AND f.content ~ '^.+: active$'
                  AND (
                      NOT (COALESCE(f.metadata, '{}'::jsonb) ? 'status')
                      OR (
                          f.metadata->>'status' = 'active'
                          AND f.metadata->>'status_restored_by' = 'health_004'
                          AND NOT EXISTS (
                              SELECT 1 FROM facts p
                              WHERE p.id = f.supersedes_id
                                AND p.metadata->>'status_restored_by' = 'health_004'
                          )
                      )
                  )
            ),
            chain(root_id, pred_id, depth) AS (
                SELECT c.id, c.supersedes_id, 1 FROM cand c
                UNION ALL
                SELECT ch.root_id, p.supersedes_id, ch.depth + 1
                FROM chain ch
                JOIN facts p ON p.id = ch.pred_id
                WHERE NOT (COALESCE(p.metadata, '{}'::jsonb) ? 'status')
                  AND p.supersedes_id IS NOT NULL
                  AND ch.depth < 50
            ),
            recovered AS (
                SELECT DISTINCT ON (ch.root_id)
                       ch.root_id,
                       substring(pr.content from ': (active|managed|resolved)$') AS status
                FROM chain ch
                JOIN cand c ON c.id = ch.root_id
                JOIN facts pr ON pr.id = ch.pred_id
                WHERE NOT (COALESCE(pr.metadata, '{}'::jsonb) ? 'status')
                  AND pr.content ~ '^.+: (active|managed|resolved)$'
                  AND substring(pr.content from ': (active|managed|resolved)$') <> c.cur
                ORDER BY ch.root_id, ch.depth
            )
            UPDATE facts f
            SET metadata = COALESCE(f.metadata, '{}'::jsonb)
                || jsonb_build_object(
                    'status', r.status,
                    'status_recovered_by', 'health_006'
                )
            FROM recovered r
            WHERE f.id = r.root_id;
        END
        $$
    """)


def downgrade() -> None:
    # The data change is not reversed: the previous state was the defect (as in health_004).
    # ``status_recovered_by`` is kept too, because ``condition_update`` copies metadata
    # forward and stripping by tag could erase a status the owner chose after the repair.
    pass
