"""Prepared retention grants (bu-s11n0s.7, slice 4a).

Revision ID: chronicler_036
Revises: chronicler_035

Raw OwnTracks expiry happens in two committed stages: Chronicler prepares a grant naming the
raw rows it is finished with, then (a later slice) the connector deletes exactly those rows
under its own role. This revision creates the grant tables. Nothing writes them yet and
nothing is deleted: ``location_retention.ENFORCEMENT_STATUS`` stays ``not_enforced``.

``location_retention_grants`` holds one row per preparation run, its receipt: the policy
version, horizon and cutoff it ran under, the config revision of every covered adapter it
matched coverage against, how many rows it granted and whether its limit stopped it.
``state`` is ``prepared``; later slices add the ready and reconciled states.

``location_retention_grant_items`` holds one row per granted raw row: its id
(``connectors.owntracks_points.id``, no cross-schema foreign key, since the raw row is meant to
go), its ``idempotency_key``, its immutable ``retention_at`` and the content digest the grant
was decided for. ``raw_source_id`` is unique: preparation is an irreversible decision, so a row
is granted at most once. Items are written first and the grant last, in one transaction; the
item's grant key is checked at commit (``DEFERRABLE INITIALLY DEFERRED``).

Both tables are append-only by trigger (an init-db replay re-grants schema-wide DML, so a grant
alone would not hold). Grants follow chronicler_035: ``REVOKE ALL ... FROM PUBLIC`` and from
``butler_chronicler_rw``, then ``SELECT, INSERT`` to that role. A re-run finds both tables and
the guard in the catalog and changes nothing.

Downgrade drops both tables. Nothing has been deleted on their account.
"""

from __future__ import annotations

from sqlalchemy import text

from alembic import op

revision = "chronicler_036"
down_revision = "chronicler_035"
branch_labels = None
depends_on = None

_ROLE = "butler_chronicler_rw"
_GRANTS = "location_retention_grants"
_ITEMS = "location_retention_grant_items"
_GUARD = "chronicler.location_retention_grant_append_only()"
_DIGEST = "'^v1:[0-9a-f]{64}$'"
_ADAPTERS = "ARRAY['owntracks.points', 'owntracks.place_cluster', 'owntracks.ssid_presence']"


def upgrade() -> None:
    # The revision's DDL is one transaction, so both tables exist only together with their
    # guard, triggers and grants: a re-run only reads the catalog.
    installed = (
        op.get_bind()
        .execute(
            text(
                f"SELECT to_regclass('{_GRANTS}') IS NOT NULL "
                f"AND to_regclass('{_ITEMS}') IS NOT NULL "
                f"AND to_regprocedure('{_GUARD}') IS NOT NULL"
            )
        )
        .scalar()
    )
    if installed:
        return
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_GRANTS} (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            policy_version INTEGER NOT NULL CHECK (policy_version > 0),
            retention_days INTEGER NOT NULL CHECK (retention_days BETWEEN 1 AND 30),
            cutoff TIMESTAMPTZ NOT NULL,
            config_revisions JSONB NOT NULL CHECK (
                jsonb_typeof(config_revisions) = 'object'
                AND config_revisions ?& {_ADAPTERS}
                AND config_revisions - {_ADAPTERS} = '{{}}'::jsonb
            ),
            item_count INTEGER NOT NULL CHECK (item_count >= 0),
            capped BOOLEAN NOT NULL,
            state TEXT NOT NULL DEFAULT 'prepared' CHECK (state = 'prepared'),
            prepared_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_ITEMS} (
            grant_id UUID NOT NULL REFERENCES {_GRANTS} (id) DEFERRABLE INITIALLY DEFERRED,
            raw_source_id UUID NOT NULL UNIQUE,
            logical_source_key TEXT NOT NULL CHECK (logical_source_key <> ''),
            retention_at TIMESTAMPTZ NOT NULL,
            content_digest TEXT NOT NULL CHECK (content_digest ~ {_DIGEST}),
            PRIMARY KEY (grant_id, raw_source_id)
        )
        """
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {_GUARD}
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, pg_temp
        AS $fn$
        BEGIN
            RAISE EXCEPTION 'location retention grants are append-only'
                USING ERRCODE = '55000';
        END;
        $fn$
        """
    )
    for table in (_GRANTS, _ITEMS):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table}")
        op.execute(
            f"""
            CREATE TRIGGER {table}_append_only
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION {_GUARD}
            """
        )
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC, {_ROLE}")
        op.execute(f"GRANT SELECT, INSERT ON {table} TO {_ROLE}")


def downgrade() -> None:
    op.execute(f"DROP TABLE IF EXISTS {_ITEMS}")
    op.execute(f"DROP TABLE IF EXISTS {_GRANTS}")
    op.execute(f"DROP FUNCTION IF EXISTS {_GUARD}")
