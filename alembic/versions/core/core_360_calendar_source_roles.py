"""calendar_sources: every source gets a role and a basis (bu-5ii4gj.1, REQ-module-calendar-034).

Revision ID: core_360
Revises: core_358
Create Date: 2026-11-23 00:00:00.000000

Calendar discovery registers every calendar the account can see as lane ``user``, and no reader
asked which calendar an event came from, so a partner's or colleague's appointment counted as the
owner's own time. This adds two columns to the schema's own ``calendar_sources``:

- ``time_role TEXT NOT NULL DEFAULT 'unclassified'``: ``owner``, ``household_member``,
  ``other_person``, ``team``, ``subscription``, ``holidays``, ``birthdays``, ``butler`` or
  ``unclassified``. The default fails toward "not the owner's time".
- ``role_basis TEXT NOT NULL DEFAULT 'provider_inferred'``: ``provider_inferred`` or
  ``owner_confirmed``. Rediscovery never overwrites an owner-confirmed role.

Existing rows are classified from the discovery facts already stored on them, never from a name:
a butler-lane source, or one whose metadata says ``butler_specific``, is ``butler``; the source
whose calendar id is the stored ``account_email`` is the account's primary calendar, ``owner``;
the holiday and contacts-birthday calendar ids are ``holidays`` and ``birthdays``; everything
else, including a writable non-primary calendar, is ``unclassified``. The next discovery
re-classifies provider-inferred rows from the full calendarList facts.

The columns are plain TEXT, so the public JSON projections (core_320, finance_020, health_007),
which are declared over JSONB columns, are unaffected. The core chain runs once per butler
schema and this revision only touches the current schema's ``calendar_sources``. A run that finds
both columns already in the expected shape is a catalog-only no-op. A column present with an
incompatible shape stops the migration instead of being reused.

Downgrade keeps both columns and their values (evidence-preserving), as core_341 does; older code
ignores them and a later upgrade reuses them.
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "core_360"
down_revision = "core_358"
branch_labels = None
depends_on = None

_ROLE_CHECK = "calendar_sources_time_role_check"
_BASIS_CHECK = "calendar_sources_role_basis_check"

_ROLES = (
    "owner",
    "household_member",
    "other_person",
    "team",
    "subscription",
    "holidays",
    "birthdays",
    "butler",
    "unclassified",
)

# (column, data_type, is_nullable, column_default) as information_schema reports them.
_EXPECTED = {
    "time_role": ("text", "NO", "'unclassified'::text"),
    "role_basis": ("text", "NO", "'provider_inferred'::text"),
}


def _existing_columns() -> dict[str, tuple[str, str, str | None]]:
    rows = (
        op.get_bind()
        .execute(
            text(
                "SELECT column_name, data_type, is_nullable, column_default "
                "FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = 'calendar_sources' "
                "AND column_name IN ('time_role', 'role_basis')"
            )
        )
        .fetchall()
    )
    return {name: (data_type, nullable, default) for name, data_type, nullable, default in rows}


def _table_exists() -> bool:
    return bool(
        op.get_bind()
        .execute(text("SELECT to_regclass('calendar_sources') IS NOT NULL"))
        .scalar_one()
    )


def upgrade() -> None:
    if not _table_exists():
        return
    existing = _existing_columns()
    for name, shape in existing.items():
        if shape != _EXPECTED[name]:
            raise RuntimeError(
                f"calendar_sources.{name} exists with an unexpected shape {shape!r}; "
                f"expected {_EXPECTED[name]!r}"
            )
    if len(existing) == len(_EXPECTED):
        return
    roles = ", ".join(f"'{role}'" for role in _ROLES)
    clauses = []
    if "time_role" not in existing:
        clauses += [
            "ADD COLUMN time_role TEXT NOT NULL DEFAULT 'unclassified'",
            f"ADD CONSTRAINT {_ROLE_CHECK} CHECK (time_role IN ({roles}))",
        ]
    if "role_basis" not in existing:
        clauses += [
            "ADD COLUMN role_basis TEXT NOT NULL DEFAULT 'provider_inferred'",
            f"ADD CONSTRAINT {_BASIS_CHECK} "
            "CHECK (role_basis IN ('provider_inferred', 'owner_confirmed'))",
        ]
    # One ALTER: a constant default is metadata-only, so the table is not rewritten.
    op.execute("ALTER TABLE calendar_sources " + ", ".join(clauses))
    if "time_role" not in existing:
        # Classify from stored discovery facts only. Every row is at the default here, so a
        # role an owner set cannot be overwritten.
        op.execute(
            """
            UPDATE calendar_sources SET time_role = CASE
                WHEN lane = 'butler' THEN 'butler'
                WHEN metadata->>'butler_specific' = 'true' THEN 'butler'
                WHEN calendar_id IS NOT NULL
                     AND calendar_id = metadata->>'account_email' THEN 'owner'
                WHEN calendar_id LIKE '%#holiday@group.v.calendar.google.com' THEN 'holidays'
                WHEN calendar_id = 'addressbook#contacts@group.v.calendar.google.com'
                    THEN 'birthdays'
                ELSE 'unclassified'
            END
            """
        )


def downgrade() -> None:
    # Evidence-preserving: the role columns and their values stay (see the module docstring).
    pass
