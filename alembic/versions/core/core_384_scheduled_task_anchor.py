"""scheduled_tasks: a typed anchor beside the timezone column (bu-5ii4gj.5 P6).

Revision ID: core_384
Revises: core_380
Create Date: 2026-11-26 00:00:00.000000

A schedule's ``timezone`` says where its cron fields are read, but not *why*: the ``'UTC'``
sentinel means "follow the owner" while every other zone means "pinned". This adds the kind
explicitly so an owner-clock transition can re-anchor the owner-local rows and leave the rest alone:

* ``anchor_kind``: ``owner_local`` (follow the owner clock), ``zone_pinned`` (a fixed zone, held in
  ``anchor_zone``), ``absolute`` (a one-shot row with no recurring wall clock) or ``interval``
  (reserved). NULL means "not declared": readers derive the kind from the row's columns
  (``core.scheduler.anchor.effective_anchor``), so existing rows are NOT backfilled and the
  derivation stays live. Only an explicit declaration sets the column, and the writers that change
  a row's timezone, cron, task type or until_at clear it, so a stale declaration never outlives
  its row.
* ``anchor_zone``: the IANA zone of a ``zone_pinned`` row.

Two CHECKs guard declared rows only (a NULL kind satisfies both): ``zone_pinned`` requires
``timezone = anchor_zone`` and ``owner_local`` requires the sentinel (or NULL).

``scheduled_tasks`` is a per-butler table, so this revision only touches the current schema's copy
and no shared table, role or grant (core_377 shape). Every statement is idempotent. Downgrade drops
the CHECKs (so an older writer can edit a declared row) and keeps the columns and values
(evidence-preserving), as core_377 does.
"""

from __future__ import annotations

from sqlalchemy import text

from alembic import op

revision = "core_384"
down_revision = "core_380"
branch_labels = None
depends_on = None

_TABLE_NAME = "quote_ident(current_schema()) || '.scheduled_tasks'"
_KIND_CHECK = "scheduled_tasks_anchor_kind_check"
_PINNED_CHECK = "scheduled_tasks_anchor_pinned_check"
_LOCAL_CHECK = "scheduled_tasks_anchor_local_check"


def _add_check(bind, name: str, expression: str) -> None:
    exists = bind.execute(
        text(
            "SELECT EXISTS (SELECT 1 FROM pg_constraint "
            f"WHERE conrelid = to_regclass({_TABLE_NAME}) AND conname = :name)"
        ),
        {"name": name},
    ).scalar_one()
    if not exists:
        op.execute(f"ALTER TABLE scheduled_tasks ADD CONSTRAINT {name} CHECK ({expression})")


def upgrade() -> None:
    bind = op.get_bind()
    if not bind.execute(text(f"SELECT to_regclass({_TABLE_NAME}) IS NOT NULL")).scalar_one():
        return
    op.execute(
        "ALTER TABLE scheduled_tasks "
        "ADD COLUMN IF NOT EXISTS anchor_kind TEXT, "
        "ADD COLUMN IF NOT EXISTS anchor_zone TEXT"
    )
    # A re-upgrade after a downgrade can meet declarations an older writer left stale (the CHECKs
    # were off). Clearing them is lossless: the row is then derived from its columns.
    op.execute(
        "UPDATE scheduled_tasks SET anchor_kind = NULL, anchor_zone = NULL "
        "WHERE anchor_kind IS NOT NULL AND ("
        "  anchor_kind NOT IN ('owner_local', 'zone_pinned', 'absolute', 'interval') "
        "  OR (anchor_kind = 'zone_pinned' AND (anchor_zone IS NULL OR timezone <> anchor_zone)) "
        "  OR (anchor_kind = 'owner_local' "
        "      AND upper(btrim(COALESCE(timezone, ''))) NOT IN ('', 'UTC'))"
        ")"
    )
    _add_check(
        bind,
        _KIND_CHECK,
        "anchor_kind IS NULL "
        "OR anchor_kind IN ('owner_local', 'zone_pinned', 'absolute', 'interval')",
    )
    _add_check(
        bind,
        _PINNED_CHECK,
        "anchor_kind IS DISTINCT FROM 'zone_pinned' "
        "OR (anchor_zone IS NOT NULL AND timezone = anchor_zone)",
    )
    _add_check(
        bind,
        _LOCAL_CHECK,
        "anchor_kind IS DISTINCT FROM 'owner_local' "
        "OR upper(btrim(COALESCE(timezone, ''))) IN ('', 'UTC')",
    )


def downgrade() -> None:
    bind = op.get_bind()
    if not bind.execute(text(f"SELECT to_regclass({_TABLE_NAME}) IS NOT NULL")).scalar_one():
        return
    # The columns and their values stay (evidence-preserving); only the constraints go.
    for name in (_KIND_CHECK, _PINNED_CHECK, _LOCAL_CHECK):
        op.execute(f"ALTER TABLE scheduled_tasks DROP CONSTRAINT IF EXISTS {name}")
