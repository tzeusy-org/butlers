"""calendar_events: persist the provider event type and typed working location (bu-s11n0s.4 S1).

Revision ID: core_341
Revises: core_340
Create Date: 2026-11-15 00:00:00.000000

Google Calendar marks out-of-office, focus-time and working-location entries with
``eventType`` (and ``workingLocationProperties`` for the last). The canonical normalizer
dropped both, so a declared out-of-office block looked like an ordinary meeting. This adds
two columns to the schema's own ``calendar_events``:

- ``event_type TEXT NOT NULL DEFAULT 'default'``: the provider type, kept verbatim
  (``outOfOffice``, ``focusTime``, ``workingLocation``, ``fromGmail``, ``birthday`` or a future
  value). No enumeration check: an unknown type must survive. A length check (1..64) matches the
  normalizer's bound.
- ``working_location JSONB NULL``: the normalized ``{type, label}`` object, or NULL. A check
  keeps it an object; the application validates its shape.

Existing rows become ``default``/NULL. Nothing is guessed from titles and no provider history
is fetched; the next provider sync replaces both fields on every event it reports.

The core chain runs once per butler schema. This revision only touches the current schema's
``calendar_events`` (no other schema's catalog rows). A run that finds both columns already in
the expected shape is a catalog-only no-op: it takes no table lock. A column present with an
incompatible shape stops the migration instead of being reused.

Downgrade keeps both columns and their values (evidence-preserving): older code ignores them,
and a later upgrade reuses them. The schema after a downgrade is therefore not identical to
core_340's.
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "core_341"
down_revision = "core_340"
branch_labels = None
depends_on = None

_EVENT_TYPE_CHECK = "calendar_events_event_type_length_check"
_WORKING_LOCATION_CHECK = "calendar_events_working_location_object_check"

# (column, data_type, is_nullable, column_default) as information_schema reports them.
_EXPECTED = {
    "event_type": ("text", "NO", "'default'::text"),
    "working_location": ("jsonb", "YES", None),
}


def _existing_columns() -> dict[str, tuple[str, str, str | None]]:
    rows = (
        op.get_bind()
        .execute(
            text(
                "SELECT column_name, data_type, is_nullable, column_default "
                "FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = 'calendar_events' "
                "AND column_name IN ('event_type', 'working_location')"
            )
        )
        .fetchall()
    )
    return {name: (data_type, nullable, default) for name, data_type, nullable, default in rows}


def _table_exists() -> bool:
    return bool(
        op.get_bind()
        .execute(text("SELECT to_regclass('calendar_events') IS NOT NULL"))
        .scalar_one()
    )


def upgrade() -> None:
    if not _table_exists():
        return
    existing = _existing_columns()
    for name, shape in existing.items():
        if shape != _EXPECTED[name]:
            raise RuntimeError(
                f"calendar_events.{name} exists with an unexpected shape {shape!r}; "
                f"expected {_EXPECTED[name]!r}"
            )
    clauses = []
    if "event_type" not in existing:
        clauses += [
            "ADD COLUMN event_type TEXT NOT NULL DEFAULT 'default'",
            f"ADD CONSTRAINT {_EVENT_TYPE_CHECK} CHECK (char_length(event_type) BETWEEN 1 AND 64)",
        ]
    if "working_location" not in existing:
        clauses += [
            "ADD COLUMN working_location JSONB",
            f"ADD CONSTRAINT {_WORKING_LOCATION_CHECK} "
            "CHECK (working_location IS NULL OR jsonb_typeof(working_location) = 'object')",
        ]
    if clauses:
        # One ALTER: a constant default is metadata-only and both checks hold trivially on the
        # new columns, so the table is not rewritten.
        op.execute("ALTER TABLE calendar_events " + ", ".join(clauses))


def downgrade() -> None:
    # Evidence-preserving: the typed columns and their values stay (see the module docstring).
    pass
