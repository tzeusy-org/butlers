"""Expose only the fixed Finance receiver's existing current-boot witness.

Revision ID: sw_052
Revises: sw_051

No boot registration, repair, table grant, RLS change or new owner is installed.
The ordinary migration login may own the existing registry objects and may
SET its runtime roles. The reverse runtime-to-owner direction must be absent.
"""

from alembic import op

revision = "sw_052"
down_revision = "sw_051"
branch_labels = None
depends_on = None

_SIGNATURE = "public.finance_warning_boot_current(uuid,bigint)"


def _statements(schema: str) -> tuple[str, ...]:
    quoted = '"' + schema.replace('"', '""') + '"'
    literal = "'" + schema.replace("'", "''") + "'"
    # The schema is selected by trusted Alembic configuration, baked into the
    # body; no caller parameter or search_path can select an authority relation.
    return (
        f"""DO $check$
        DECLARE v_owner oid; v_history_owner oid; v_function_owner oid;
        BEGIN
          SELECT c.relowner INTO v_owner FROM pg_catalog.pg_class c
            JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname={literal} AND c.relname='butler_registry_control_plane';
          SELECT c.relowner INTO v_history_owner FROM pg_catalog.pg_class c
            JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname={literal} AND c.relname='butler_boot_registrations';
          SELECT proowner INTO v_function_owner FROM pg_catalog.pg_proc
            WHERE oid=pg_catalog.to_regprocedure('public.register_butler_boot(text,uuid)');
          IF v_owner IS NULL OR v_owner IS DISTINCT FROM v_history_owner
             OR v_owner IS DISTINCT FROM v_function_owner
             OR NOT pg_catalog.pg_has_role(current_user,v_owner,'SET') THEN
            RAISE EXCEPTION 'finance_boot_read_owner_incompatible' USING ERRCODE='55000';
          END IF;
          IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles r
             WHERE (r.rolname LIKE 'butler_%_rw'
                    OR r.rolname IN ('connector_writer','dashboard_api','dashboard_auth_api'))
               AND (r.oid=v_owner OR pg_catalog.pg_has_role(r.oid,v_owner,'SET')
                    OR pg_catalog.pg_has_role(r.oid,v_owner,'USAGE'))) THEN
            RAISE EXCEPTION 'finance_boot_read_owner_reachable' USING ERRCODE='55000';
          END IF;
          IF EXISTS (SELECT 1 FROM pg_catalog.pg_class c
            JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname={literal}
              AND c.relname IN ('butler_registry_control_plane','butler_boot_registrations')
              AND (NOT c.relrowsecurity OR c.relforcerowsecurity)) THEN
            RAISE EXCEPTION 'finance_boot_read_rls_incompatible' USING ERRCODE='55000';
          END IF;
        END $check$""",
        f"""CREATE OR REPLACE FUNCTION {_SIGNATURE.replace("(uuid,bigint)", "(p_boot uuid,p_epoch bigint)")}
        RETURNS boolean LANGUAGE plpgsql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp AS $read$
        DECLARE v_current boolean;
        BEGIN
          IF pg_catalog.current_setting('role',true) IS DISTINCT FROM 'butler_finance_rw' THEN
            RAISE EXCEPTION 'finance_boot_read_role_required' USING ERRCODE='42501';
          END IF;
          IF p_boot IS NULL OR p_epoch IS NULL OR p_epoch<=0 THEN RETURN NULL; END IF;
          SELECT CASE WHEN h.boot_instance_id IS NULL THEN NULL
                      ELSE c.boot_instance_id=p_boot AND c.boot_epoch=p_epoch END
            INTO v_current
            FROM {quoted}.butler_registry_control_plane c
            LEFT JOIN {quoted}.butler_boot_registrations h
              ON h.name=c.name AND h.boot_instance_id=c.boot_instance_id
                 AND h.boot_epoch=c.boot_epoch
            WHERE c.name='finance';
          RETURN v_current;
        END $read$""",
        f"""DO $owner$ DECLARE v_owner name; BEGIN
          SELECT pg_catalog.pg_get_userbyid(c.relowner) INTO STRICT v_owner
            FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname={literal} AND c.relname='butler_registry_control_plane';
          EXECUTE pg_catalog.format('ALTER FUNCTION {_SIGNATURE} OWNER TO %I',v_owner);
        END $owner$""",
        f"REVOKE ALL ON FUNCTION {_SIGNATURE} FROM PUBLIC",
        # CREATE OR REPLACE keeps any preexisting ACL. Converge only this new
        # interface's grantees, not unrelated table/function or role authority.
        f"""DO $acl$ DECLARE r record; BEGIN
          FOR r IN SELECT DISTINCT x.grantee FROM pg_catalog.pg_proc p,
              LATERAL pg_catalog.aclexplode(COALESCE(p.proacl,pg_catalog.acldefault('f',p.proowner))) x
              WHERE p.oid=pg_catalog.to_regprocedure('{_SIGNATURE}')
                AND x.grantee<>p.proowner AND x.grantee<>0 LOOP
            EXECUTE pg_catalog.format('REVOKE ALL ON FUNCTION {_SIGNATURE} FROM %I',
                    pg_catalog.pg_get_userbyid(r.grantee));
          END LOOP;
        END $acl$""",
        f"GRANT EXECUTE ON FUNCTION {_SIGNATURE} TO butler_finance_rw",
    )


def upgrade() -> None:
    schema = str(op.get_bind().exec_driver_sql("SELECT current_schema()").scalar_one())
    for statement in _statements(schema):
        op.execute(statement)


def downgrade() -> None:
    op.execute(f"DROP FUNCTION IF EXISTS {_SIGNATURE}")
