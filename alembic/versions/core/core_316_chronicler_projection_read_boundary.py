"""Install the fixed Chronicler private-memory public projection boundary.

Revision ID: core_316
Revises: core_315

The bootstrap-owned finalizer creates the row-id-only readers and narrow
identity guards. Core replay and init-db replay converge on the same objects.
"""

from __future__ import annotations

from alembic import op

revision = "core_316"
down_revision = "core_315"
branch_labels = None
depends_on = None

_LOCK = (
    "SELECT pg_advisory_xact_lock(hashtextextended('butlers:chronicler-memory-read-boundary', 0))"
)


def upgrade() -> None:
    op.execute(_LOCK)
    op.execute(
        """
        DO $preflight$
        BEGIN
            IF to_regprocedure('chronicler_memory_admin.finalize_interface()') IS NULL THEN
                RAISE EXCEPTION 'Chronicler memory finalizer is missing; rerun init-db.sql'
                    USING ERRCODE = '55000';
            END IF;
            IF to_regclass('public.memory_catalog') IS NULL
               OR to_regclass('public.entity_graph_edges') IS NULL THEN
                RAISE EXCEPTION 'public memory projections are required before core_315'
                    USING ERRCODE = '55000';
            END IF;
        END
        $preflight$
        """
    )
    op.execute("SELECT chronicler_memory_admin.finalize_interface()")
    op.execute(
        """
        DO $verify$
        DECLARE
            v_owner OID;
            v_helper OID;
        BEGIN
            SELECT oid INTO v_owner FROM pg_roles WHERE rolname='chronicler_memory_owner';
            IF v_owner IS NULL OR EXISTS (
                SELECT 1 FROM pg_auth_members
                WHERE roleid=v_owner OR member=v_owner
            ) OR has_schema_privilege(v_owner, 'public', 'CREATE')
              OR has_schema_privilege(v_owner, 'chronicler_memory_admin', 'CREATE')
              OR EXISTS (
                SELECT 1 FROM pg_roles WHERE oid=v_owner AND
                    (rolcanlogin OR rolinherit OR rolsuper OR rolcreaterole OR rolcreatedb
                     OR rolreplication OR rolbypassrls)
            ) THEN
                RAISE EXCEPTION 'core_315: Chronicler projection owner is not isolated';
            END IF;
            FOR v_helper IN
                SELECT to_regprocedure(signature)
                FROM unnest(ARRAY[
                    'public.chronicler_catalog_row_visible(uuid)',
                    'public.chronicler_graph_row_visible(uuid)'
                ]) AS signatures(signature)
            LOOP
                IF v_helper IS NULL OR NOT EXISTS (
                    SELECT 1 FROM pg_proc AS function
                    WHERE function.oid=v_helper
                      AND function.proowner=v_owner
                      AND function.prosecdef
                      AND function.proconfig=ARRAY['search_path=pg_catalog, pg_temp']::text[]
                ) THEN
                    RAISE EXCEPTION 'core_315: row-id visibility helper is not finalized';
                END IF;
                IF EXISTS (
                    SELECT 1
                    FROM pg_proc AS function,
                         LATERAL aclexplode(
                             COALESCE(function.proacl, acldefault('f', function.proowner))
                         ) AS acl
                    WHERE function.oid=v_helper AND acl.grantee=0
                      AND acl.privilege_type='EXECUTE'
                ) THEN
                    RAISE EXCEPTION 'core_315: visibility helper remains executable by PUBLIC';
                END IF;
            END LOOP;
            IF NOT EXISTS (
                SELECT 1 FROM pg_trigger AS trigger_row
                WHERE trigger_row.tgrelid='public.memory_catalog'::regclass
                  AND trigger_row.tgname='chronicler_catalog_identity_guard'
                  AND NOT trigger_row.tgisinternal AND trigger_row.tgenabled='O'
                  AND trigger_row.tgfoid='public.guard_chronicler_catalog_identity()'::regprocedure
            ) OR NOT EXISTS (
                SELECT 1 FROM pg_trigger AS trigger_row
                WHERE trigger_row.tgrelid='public.entity_graph_edges'::regclass
                  AND trigger_row.tgname='chronicler_graph_identity_guard'
                  AND NOT trigger_row.tgisinternal AND trigger_row.tgenabled='O'
                  AND trigger_row.tgfoid='public.guard_chronicler_graph_identity()'::regprocedure
            ) THEN
                RAISE EXCEPTION 'core_315: private source identity guards are not enabled';
            END IF;
            IF EXISTS (
                SELECT 1 FROM pg_class
                WHERE oid IN ('public.memory_catalog'::regclass,
                              'public.entity_graph_edges'::regclass)
                  AND relrowsecurity
            ) THEN
                RAISE EXCEPTION 'core_315: existing projection RLS state changed';
            END IF;
        END
        $verify$
        """
    )


def downgrade() -> None:
    # The shared public boundary remains available to older migrated schemas.
    pass
