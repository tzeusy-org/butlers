"""Owner place registry: one trusted table of named places (bu-jv1moa.7).

Revision ID: core_303
Revises: core_302
Create Date: 2026-11-02 00:00:00.000000

``public.owner_places`` holds the owner's named places (home, work, airports, others): a
label, a kind, a centre at no more than five decimals, a radius of 50..2000 m, aliases,
exact Wi-Fi SSIDs and an optional Home Assistant zone.  Each row keeps a stable UUID that
later perception records can carry, a ``version`` for owner-editor compare-and-swap, a
``paused`` switch and a ``deleted_at`` soft delete.  A deleted row is never revived, so an
identity is never recycled.

Role and read matrix (non-FORCE row security, the core_246 posture):

==========================  ======  ===============================================
identity                    access  why
==========================  ======  ===============================================
table owner (dashboard,     all     PostgreSQL owner bypass: the Home dashboard
migrations, pg_dump)                editor writes, migrations and backups see all
``butler_travel_rw``        SELECT  commuting ETA context producer (home centre)
``butler_chronicler_rw``    SELECT  OwnTracks place-cluster and SSID projections
every other runtime role    none    ``init-db.sql`` re-grants DML broadly, so the
                                    policy (not the grant) is the fence
==========================  ======  ===============================================

No runtime role holds a write policy: registry writes are owner dashboard (and the later
one-time legacy import) only, never generic state or model tools.  The two readers see
every row and filter live places in the shared read helper
(``butlers.owner_places.load_active_owner_places``).

Live-place invariants enforced here: a label is unique (case-insensitively) and at most
one place is ``home``.  SSID uniqueness across places is checked by the editor under its
advisory lock, since PostgreSQL has no stock overlap exclusion for ``text[]``.

The core chain runs once per butler schema over this one shared table, so only the last
schema to leave core_303 drops it, and only while it is empty: rolling back the schema
must not destroy owner-entered places.  Source rollback disables the editor and readers
instead and keeps the rows.
"""

from __future__ import annotations

from alembic import op

revision = "core_303"
down_revision = "core_302"
branch_labels = None
depends_on = None

_TABLE = "public.owner_places"
_POLICY = "owner_places_readers"
_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_303:owner-places', 0))"
_READERS = ("butler_travel_rw", "butler_chronicler_rw")
_RUNTIME_ROLES = (
    "butler_chronicler_rw",
    "butler_education_rw",
    "butler_finance_rw",
    "butler_general_rw",
    "butler_health_rw",
    "butler_home_rw",
    "butler_lifestyle_rw",
    "butler_messenger_rw",
    "butler_qa_rw",
    "butler_relationship_rw",
    "butler_switchboard_rw",
    "butler_travel_rw",
    "butler_calendar_rw",
    "connector_writer",
    "restore_drill_executor",
)

_ROLLBACK_REFUSAL = (
    "core_303 cannot drop public.owner_places while it holds owner places. "
    "Disable the editor and readers in source instead and keep core_303 installed."
)


def _for_existing_role(role: str, statement: str) -> None:
    escaped = statement.replace("'", "''")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                EXECUTE '{escaped}';
            END IF;
        EXCEPTION
            WHEN insufficient_privilege THEN NULL;
            WHEN undefined_object THEN NULL;
            WHEN undefined_table THEN NULL;
        END
        $$
        """
    )


def _installation_count() -> int:
    """Count schema-local core chains that still depend on the shared table."""
    bind = op.get_bind()
    schemas = bind.exec_driver_sql(
        """
        SELECT namespace.nspname
        FROM pg_class version_table
        JOIN pg_namespace namespace ON namespace.oid = version_table.relnamespace
        WHERE version_table.relname = 'alembic_version'
          AND version_table.relkind IN ('r', 'p')
          AND left(namespace.nspname, 3) <> 'pg_'
          AND namespace.nspname <> 'information_schema'
        """
    ).scalars()
    quote = bind.dialect.identifier_preparer.quote
    count = 0
    for schema in schemas:
        installed = bind.exec_driver_sql(
            f"""
            SELECT EXISTS (
                SELECT 1 FROM {quote(schema)}.alembic_version
                WHERE CASE WHEN version_num ~ '^core_[0-9]+$'
                    THEN substring(version_num FROM 6)::integer >= 303
                    ELSE false END
            )
            """
        ).scalar()
        count += int(bool(installed))
    return count


def upgrade() -> None:
    op.execute(_LOCK)
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_TABLE} (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            label       TEXT NOT NULL,
            kind        TEXT NOT NULL,
            lat         NUMERIC(7, 5) NOT NULL,
            lon         NUMERIC(8, 5) NOT NULL,
            radius_m    INTEGER NOT NULL,
            aliases     TEXT[] NOT NULL DEFAULT '{{}}',
            ssids       TEXT[] NOT NULL DEFAULT '{{}}',
            ha_zone     TEXT,
            paused      BOOLEAN NOT NULL DEFAULT false,
            version     INTEGER NOT NULL DEFAULT 1,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            deleted_at  TIMESTAMPTZ,
            CONSTRAINT ck_owner_places_label
                CHECK (char_length(label) BETWEEN 1 AND 80 AND label = btrim(label)),
            CONSTRAINT ck_owner_places_kind
                CHECK (kind IN ('home', 'work', 'airport', 'other')),
            CONSTRAINT ck_owner_places_lat CHECK (lat BETWEEN -90 AND 90),
            CONSTRAINT ck_owner_places_lon CHECK (lon BETWEEN -180 AND 180),
            CONSTRAINT ck_owner_places_radius CHECK (radius_m BETWEEN 50 AND 2000),
            CONSTRAINT ck_owner_places_ha_zone
                CHECK (ha_zone IS NULL OR ha_zone ~ '^zone\\.[a-z0-9_]+$'),
            CONSTRAINT ck_owner_places_version CHECK (version >= 1)
        )
        """
    )
    op.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS uq_owner_places_live_label "
        f"ON {_TABLE} (lower(label)) WHERE deleted_at IS NULL"
    )
    op.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS uq_owner_places_live_home "
        f"ON {_TABLE} (kind) WHERE kind = 'home' AND deleted_at IS NULL"
    )

    op.execute(f"REVOKE ALL PRIVILEGES ON TABLE {_TABLE} FROM PUBLIC")
    for role in _RUNTIME_ROLES:
        _for_existing_role(role, f"REVOKE ALL PRIVILEGES ON TABLE {_TABLE} FROM {role}")
    for role in _READERS:
        _for_existing_role(role, f"GRANT SELECT ON TABLE {_TABLE} TO {role}")

    # Not FORCE: the owner (dashboard, migrations, canonical backup) keeps its bypass, while a
    # runtime connection is fenced to the two readers even after init-db.sql re-grants DML.
    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_TABLE} NO FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS {_POLICY} ON {_TABLE}")
    readers = ", ".join(f"'{role}'" for role in _READERS)
    op.execute(
        f"""
        CREATE POLICY {_POLICY} ON {_TABLE}
            FOR SELECT TO PUBLIC
            USING (current_user IN ({readers}))
        """
    )


def downgrade() -> None:
    op.execute(_LOCK)
    if _installation_count() > 1:
        return
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('{_TABLE}') IS NOT NULL
               AND EXISTS (SELECT 1 FROM {_TABLE}) THEN
                RAISE EXCEPTION '{_ROLLBACK_REFUSAL}';
            END IF;
        END
        $$
        """
    )
    op.execute(f"DROP TABLE IF EXISTS {_TABLE}")
