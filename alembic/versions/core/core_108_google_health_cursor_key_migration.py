"""google_health cursor key migration: embed account_uuid in endpoint_identity.

Revision ID: core_108
Revises: core_107
Create Date: 2026-05-26 00:00:00.000000

Rewrites existing ``switchboard.connector_registry`` rows whose
``connector_type = 'google_health'`` from the old 4-segment shape::

    google_health:user:<email>:<resource>

to the new 5-segment shape::

    google_health:user:<email>:<account_uuid>:<resource>

The ``account_uuid`` is sourced from ``public.google_accounts.id`` by
joining on ``public.google_accounts.email``.

Idempotency
-----------
Only rows whose ``endpoint_identity`` matches the OLD shape are touched.
The old shape has exactly 4 colon-separated segments and its 4th segment
(the resource) is NOT a UUID.  The new shape has exactly 5 segments and
its 4th segment IS a UUID.  We filter by absence of a UUID-shaped segment
using the regex ``'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'``.
Re-running this migration on an already-migrated DB is a no-op.

Reversibility
-------------
Downgrade strips the ``:<account_uuid>:`` segment, recovering the old key.
Only rows in the new shape are touched on downgrade.
"""

from __future__ import annotations

from alembic import op

revision = "core_108"
down_revision = "core_107"
branch_labels = None
depends_on = None

# Regex pattern that matches a UUID in standard 8-4-4-4-12 hex format.
_UUID_PATTERN = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"


def upgrade() -> None:
    # ------------------------------------------------------------------
    # Rewrite old-shape cursor rows to the new account_uuid-embedded shape.
    #
    # Old:  google_health:user:<email>:<resource>
    # New:  google_health:user:<email>:<account_uuid>:<resource>
    #
    # We match rows that:
    #   1. Have connector_type = 'google_health'
    #   2. Start with 'google_health:user:'
    #   3. Do NOT already contain a UUID in position 4
    #      (i.e. the 4th colon-delimited segment is NOT a UUID).
    #
    # The UPDATE joins public.google_accounts on email equality.  Rows with
    # no matching google_accounts entry are left unchanged (cannot migrate
    # without an account_uuid).
    #
    # NOTE: exec_driver_sql is used instead of op.execute() to bypass
    # SQLAlchemy's text() bind-parameter parsing.  The SQL comments contain
    # colon-prefixed tokens (e.g. ":resource", ":user") and the LIKE clause
    # contains 'google_health:user:%%'; SQLAlchemy's text() parser would
    # misinterpret these as named bind parameters, raising InvalidRequestError.
    # exec_driver_sql bypasses SQLAlchemy text() and goes directly to psycopg2,
    # which requires literal '%' in SQL to be written as '%%'.
    #
    # Guard: switchboard.connector_registry is created by the switchboard butler
    # migration chain.  When the core chain runs alone (e.g. in unit tests or a
    # minimal deployment), the table may not yet exist.  Skip gracefully.
    # ------------------------------------------------------------------
    bind = op.get_bind()
    table_exists = bind.exec_driver_sql(
        "SELECT to_regclass(%s) IS NOT NULL",
        ("switchboard.connector_registry",),
    ).scalar()
    if not table_exists:
        return

    bind.exec_driver_sql(f"""
        UPDATE switchboard.connector_registry cr
        SET endpoint_identity =
            -- Rebuild: prefix:user:email:<account_uuid>:<resource>
            -- Split on ':' gives: [0]='google_health' [1]='user' [2]=<email> [3]=<resource>
            split_part(cr.endpoint_identity, ':', 1)
            || ':' || split_part(cr.endpoint_identity, ':', 2)
            || ':' || split_part(cr.endpoint_identity, ':', 3)
            || ':' || ga.id::text
            || ':' || split_part(cr.endpoint_identity, ':', 4)
        FROM public.google_accounts ga
        WHERE cr.connector_type = 'google_health'
          AND cr.endpoint_identity LIKE 'google_health:user:%%'
          -- Only target old-shape rows: segment 4 is non-empty (has a resource)
          -- and segment 5 is empty (exactly 4 colon-separated segments).
          AND split_part(cr.endpoint_identity, ':', 4) != ''
          AND split_part(cr.endpoint_identity, ':', 5) = ''
          -- Exclude already-migrated rows: new shape has a UUID at position 4.
          AND split_part(cr.endpoint_identity, ':', 4) !~ '{_UUID_PATTERN}'
          -- Match on email (3rd segment of old key); only join active accounts
          -- to avoid non-deterministic results from rotation or re-add.
          AND ga.email = split_part(cr.endpoint_identity, ':', 3)
          AND ga.status = 'active'
    """)


def downgrade() -> None:
    # ------------------------------------------------------------------
    # Strip the :<account_uuid>: segment from migrated rows.
    #
    # New:  google_health:user:<email>:<account_uuid>:<resource>
    # Old:  google_health:user:<email>:<resource>
    #
    # We match rows that have a UUID at segment position 4 and rebuild
    # the key by dropping that segment.
    #
    # NOTE: exec_driver_sql is used instead of op.execute() — see upgrade()
    # for the rationale (SQLAlchemy text() bind-parameter parsing conflict).
    # The '%%' in LIKE clauses is the psycopg2 escape for a literal '%'.
    #
    # Guard: same as upgrade() — skip if switchboard.connector_registry
    # does not exist.
    # ------------------------------------------------------------------
    bind = op.get_bind()
    table_exists = bind.exec_driver_sql(
        "SELECT to_regclass(%s) IS NOT NULL",
        ("switchboard.connector_registry",),
    ).scalar()
    if not table_exists:
        return

    bind.exec_driver_sql(f"""
        UPDATE switchboard.connector_registry
        SET endpoint_identity =
            -- Rebuild: prefix:user:email:<resource>  (drop UUID segment)
            split_part(endpoint_identity, ':', 1)
            || ':' || split_part(endpoint_identity, ':', 2)
            || ':' || split_part(endpoint_identity, ':', 3)
            || ':' || split_part(endpoint_identity, ':', 5)
        WHERE connector_type = 'google_health'
          AND endpoint_identity LIKE 'google_health:user:%%'
          -- Only touch new-shape rows: segment 4 is a UUID, segment 5 is
          -- non-empty (has a resource), and segment 6 is empty (exactly
          -- 5 colon-separated segments).
          AND split_part(endpoint_identity, ':', 4) ~ '{_UUID_PATTERN}'
          AND split_part(endpoint_identity, ':', 5) != ''
          AND split_part(endpoint_identity, ':', 6) = ''
    """)
