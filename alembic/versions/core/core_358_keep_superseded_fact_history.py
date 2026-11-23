"""Superseded facts are kept: the fact retention horizon becomes NULL (bu-uhbhzq.3).

Revision ID: core_358
Revises: core_357
Create Date: 2026-11-23 00:00:00.000000

core_096 seeded ``public.memory_retention_policies`` ``kind='fact'`` with ``ttl_days = 7``, and the
nightly ``memory_purge_superseded`` job hard-deleted every superseded fact older than that. A
superseded fact is a closed validity interval, the evidence for "what was true on that day".

Owner decision run24-keep-history-default = A (2026-11-22): superseded facts are kept forever for
automatic expiry; ``NULL`` is the keep-forever horizon the purge job now honours.

* Upgrade sets ``ttl_days = NULL`` only where it still holds the seeded ``7`` and the dashboard
  did not write it (it stamps ``updated_by = 'owner'``), so a deliberately chosen horizon, even
  7, is left alone. The core chain replays per butler schema over this one shared
  row; the predicate makes every replay after the first a no-op.
* Downgrade restores ``7`` only on a row this revision cleared (``updated_by`` carries the
  revision), so it cannot overwrite a horizon an admin set afterwards.
* Rows already deleted are not restored.
"""

from __future__ import annotations

from alembic import op

revision = "core_358"
down_revision = "core_357"
branch_labels = None
depends_on = None

_MARK = "migration:core_358"


def upgrade() -> None:
    op.execute(f"""
        UPDATE public.memory_retention_policies
           SET ttl_days = NULL, updated_at = now(), updated_by = '{_MARK}'
         WHERE kind = 'fact' AND ttl_days = 7 AND updated_by IS DISTINCT FROM 'owner'
    """)


def downgrade() -> None:
    op.execute(f"""
        UPDATE public.memory_retention_policies
           SET ttl_days = 7, updated_at = now(), updated_by = 'system'
         WHERE kind = 'fact' AND ttl_days IS NULL AND updated_by = '{_MARK}'
    """)
