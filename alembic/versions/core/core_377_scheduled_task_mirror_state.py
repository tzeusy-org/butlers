"""scheduled_tasks: a link id apart from the provider id, and a per-row mirror state (bu-5ii4gj.4).

Revision ID: core_377
Revises: core_374
Create Date: 2026-11-24 00:00:00.000000

``calendar_event_id`` is read by the Google mirror as the provider's event id, but
``calendar_create_butler_event`` filled it with a local UUID, so the mirror skipped the create,
PATCHed an event that does not exist and swallowed the error. This keeps the caller's stable
handle in its own ``calendar_link_id`` and reserves ``calendar_event_id`` for the id Google
returned. A mirror row also records its own state so a failed push or delete is visible and
retried instead of erased:

* ``mirror_state``: ``pending`` (needs a push), ``mirrored`` (provider copy matches
  ``mirror_fingerprint``), ``failed`` (the last create or update failed) or ``retiring`` (the task is
  off and the provider copy still has to be deleted).
* ``mirror_error``, ``mirror_attempts`` and ``mirror_attempted_at`` describe the last failure
  (``mirror_attempted_at`` is also stamped just before a create, so a row that may own a provider
  event is never hard-deleted).
* ``mirror_attempt_fingerprint``: what the last failed push tried to send, so a row that parked
  after repeated failures resumes only when the task changes.
* ``delete_requested_at``: the owner deleted the task while it had, or may have had, a provider
  copy. The row is kept disabled in ``retiring`` until the mirror has deleted that copy, then
  purged, so a delete never leaves a ghost event.

A ``calendar_event_id`` shaped like a UUID cannot be a Google id (ids are base32hex, no dashes),
so those values are moved into ``calendar_link_id`` and the provider id is cleared, which lets the
mirror create the missing provider event on its next pass.

The core chain runs once per butler schema and this revision only touches the current schema's
``scheduled_tasks``; every statement is idempotent. Downgrade keeps the columns and values
(evidence-preserving), as core_360 and core_368 do.
"""

from __future__ import annotations

from sqlalchemy import text

from alembic import op

revision = "core_377"
down_revision = "core_375"
branch_labels = None
depends_on = None

# Qualified by the schema this chain run targets, so another schema's table never answers.
_TABLE_NAME = "quote_ident(current_schema()) || '.scheduled_tasks'"
_TABLE_EXISTS = f"SELECT to_regclass({_TABLE_NAME}) IS NOT NULL"
_STATE_CHECK = "scheduled_tasks_mirror_state_check"
_LINK_INDEX = "ix_scheduled_tasks_calendar_link_id"
_UUID_SHAPE = "'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'"


def upgrade() -> None:
    bind = op.get_bind()
    if not bind.execute(
        text("SELECT to_regclass(quote_ident(current_schema()) || '.scheduled_tasks') IS NOT NULL")
    ).scalar_one():
        return
    op.execute(
        "ALTER TABLE scheduled_tasks "
        "ADD COLUMN IF NOT EXISTS calendar_link_id TEXT, "
        "ADD COLUMN IF NOT EXISTS mirror_state TEXT NOT NULL DEFAULT 'pending', "
        "ADD COLUMN IF NOT EXISTS mirror_error TEXT, "
        "ADD COLUMN IF NOT EXISTS mirror_attempts INTEGER NOT NULL DEFAULT 0, "
        "ADD COLUMN IF NOT EXISTS mirror_attempted_at TIMESTAMPTZ, "
        "ADD COLUMN IF NOT EXISTS mirror_fingerprint TEXT, "
        "ADD COLUMN IF NOT EXISTS mirror_attempt_fingerprint TEXT, "
        "ADD COLUMN IF NOT EXISTS delete_requested_at TIMESTAMPTZ"
    )
    has_check = bind.execute(
        text(
            "SELECT EXISTS (SELECT 1 FROM pg_constraint "
            f"WHERE conrelid = to_regclass({_TABLE_NAME}) AND conname = :name)"
        ),
        {"name": _STATE_CHECK},
    ).scalar_one()
    if not has_check:
        op.execute(
            f"ALTER TABLE scheduled_tasks ADD CONSTRAINT {_STATE_CHECK} "
            "CHECK (mirror_state IN ('pending', 'mirrored', 'failed', 'retiring'))"
        )
    # Move the local handles out of the provider-id column. Idempotent: once moved, no row matches.
    op.execute(
        "UPDATE scheduled_tasks SET calendar_link_id = calendar_event_id, calendar_event_id = NULL "
        f"WHERE calendar_event_id ~ {_UUID_SHAPE} AND calendar_link_id IS NULL"
    )
    op.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS {_LINK_INDEX} "
        "ON scheduled_tasks (calendar_link_id) WHERE calendar_link_id IS NOT NULL"
    )


def downgrade() -> None:
    # Evidence-preserving: the columns and their values stay (see the module docstring).
    pass
