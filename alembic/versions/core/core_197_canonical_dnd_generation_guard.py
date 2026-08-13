"""Install the canonical DND generation guard through trusted bootstrap only.

Revision ID: core_197
Revises: core_196
Create Date: 2026-08-13 00:00:00.000000

The shared migration login deliberately has no DND authority.  It cannot create
or take ownership of the RLS-protected ``public.user_context`` boundary, guard,
audit, gateway, or private definer.  ``scripts/init-db.sql`` installs the fixed
cluster-superuser-owned bootstrap interface; this revision only verifies its
catalog provenance and invokes its no-argument installer when the finalized
interface is absent, or its restricted rollback during a planned privileged
downgrade.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "core_197"
down_revision = "core_196"
branch_labels = None
depends_on = None

_ADMIN_INSTALLER = "dnd_generation_admin.install_interface"
_ADMIN_ROLLBACK = "dnd_generation_admin.rollback_interface"

_TRUSTED_FINALIZED_INTERFACE_SQL = """
    SELECT EXISTS (
        SELECT 1
        FROM pg_roles AS dnd_owner
        JOIN pg_roles AS general_runtime
            ON general_runtime.rolname = 'butler_general_rw'
        JOIN pg_roles AS switchboard_runtime
            ON switchboard_runtime.rolname = 'butler_switchboard_rw'
        JOIN pg_roles AS migration_role
            ON migration_role.rolname = current_user
        JOIN pg_namespace AS public_schema
            ON public_schema.nspname = 'public'
        JOIN pg_class AS user_context
            ON user_context.relnamespace = public_schema.oid
           AND user_context.relname = 'user_context'
           AND user_context.relkind = 'r'
        JOIN pg_class AS guard_row
            ON guard_row.relnamespace = public_schema.oid
           AND guard_row.relname = 'dnd_generation_guard'
           AND guard_row.relkind = 'r'
        JOIN pg_class AS mutation_audit
            ON mutation_audit.relnamespace = public_schema.oid
           AND mutation_audit.relname = 'dnd_generation_mutations'
           AND mutation_audit.relkind = 'r'
        JOIN pg_namespace AS private_schema
            ON private_schema.nspname = 'dnd_generation_private'
        JOIN pg_proc AS gateway
            ON gateway.pronamespace = public_schema.oid
           AND gateway.proname = 'context_dnd_mutate'
           AND gateway.pronargs = 7
           AND gateway.proargtypes = '2950 25 25 1184 25 700 3802'::oidvector
        JOIN pg_proc AS private_mutation
            ON private_mutation.pronamespace = private_schema.oid
           AND private_mutation.proname = 'mutate'
           AND private_mutation.pronargs = 7
           AND private_mutation.proargtypes = '2950 25 25 1184 25 700 3802'::oidvector
        JOIN pg_proc AS canonical_json
            ON canonical_json.pronamespace = private_schema.oid
           AND canonical_json.proname = 'canonical_json'
           AND canonical_json.pronargs = 1
           AND canonical_json.proargtypes = '3802'::oidvector
        JOIN pg_namespace AS admin_schema
            ON admin_schema.nspname = 'dnd_generation_admin'
        JOIN pg_roles AS bootstrap_owner
            ON bootstrap_owner.oid = admin_schema.nspowner
        JOIN pg_class AS bootstrap_configuration
            ON bootstrap_configuration.relnamespace = admin_schema.oid
           AND bootstrap_configuration.relname = 'bootstrap_configuration'
           AND bootstrap_configuration.relkind = 'r'
        JOIN pg_proc AS installer
            ON installer.pronamespace = admin_schema.oid
           AND installer.proname = 'install_interface'
           AND installer.pronargs = 0
           AND installer.prorettype = 'void'::regtype
        JOIN pg_proc AS finalizer
            ON finalizer.pronamespace = admin_schema.oid
           AND finalizer.proname = 'finalize_interface'
           AND finalizer.pronargs = 0
           AND finalizer.prorettype = 'void'::regtype
        JOIN pg_proc AS rollback_interface
            ON rollback_interface.pronamespace = admin_schema.oid
           AND rollback_interface.proname = 'rollback_interface'
           AND rollback_interface.pronargs = 0
           AND rollback_interface.prorettype = 'void'::regtype
        WHERE dnd_owner.rolname = 'dnd_generation_owner'
          AND NOT dnd_owner.rolcanlogin
          AND NOT dnd_owner.rolsuper
          AND NOT dnd_owner.rolcreaterole
          AND NOT dnd_owner.rolcreatedb
          AND NOT dnd_owner.rolreplication
          AND NOT dnd_owner.rolbypassrls
          AND NOT dnd_owner.rolinherit
          AND NOT migration_role.rolsuper
          AND NOT EXISTS (
              SELECT 1
              FROM pg_auth_members AS member
              WHERE member.roleid = dnd_owner.oid OR member.member = dnd_owner.oid
          )
          AND user_context.relowner = dnd_owner.oid
          AND guard_row.relowner = dnd_owner.oid
          AND mutation_audit.relowner = dnd_owner.oid
          AND private_schema.nspowner = dnd_owner.oid
          AND user_context.relrowsecurity
          AND user_context.relforcerowsecurity
          AND EXISTS (
              SELECT 1
              FROM pg_policy AS policy
              WHERE policy.polrelid = user_context.oid
                AND policy.polname = 'dnd_user_context_select'
                AND policy.polcmd = 'r'
                AND policy.polpermissive
                AND policy.polroles = ARRAY[0]::oid[]
                AND pg_get_expr(policy.polqual, policy.polrelid) = 'true'
          )
          AND EXISTS (
              SELECT 1
              FROM pg_policy AS policy
              WHERE policy.polrelid = user_context.oid
                AND policy.polname = 'dnd_user_context_insert'
                AND policy.polcmd = 'a'
                AND policy.polpermissive
                AND policy.polroles = ARRAY[0]::oid[]
                AND lower(COALESCE(pg_get_expr(policy.polwithcheck, policy.polrelid), ''))
                    LIKE '%signal_type%'
                AND lower(COALESCE(pg_get_expr(policy.polwithcheck, policy.polrelid), ''))
                    LIKE '%dnd_generation_owner%'
          )
          AND EXISTS (
              SELECT 1
              FROM pg_policy AS policy
              WHERE policy.polrelid = user_context.oid
                AND policy.polname = 'dnd_user_context_update'
                AND policy.polcmd = 'w'
                AND policy.polpermissive
                AND policy.polroles = ARRAY[0]::oid[]
                AND lower(COALESCE(pg_get_expr(policy.polqual, policy.polrelid), ''))
                    LIKE '%signal_type%'
                AND lower(COALESCE(pg_get_expr(policy.polwithcheck, policy.polrelid), ''))
                    LIKE '%dnd_generation_owner%'
          )
          AND EXISTS (
              SELECT 1
              FROM pg_policy AS policy
              WHERE policy.polrelid = user_context.oid
                AND policy.polname = 'dnd_user_context_delete'
                AND policy.polcmd = 'd'
                AND policy.polpermissive
                AND policy.polroles = ARRAY[0]::oid[]
                AND lower(COALESCE(pg_get_expr(policy.polqual, policy.polrelid), ''))
                    LIKE '%dnd_generation_owner%'
          )
          AND NOT EXISTS (
              SELECT 1
              FROM pg_policy AS policy
              WHERE policy.polrelid = user_context.oid
                AND policy.polname NOT IN (
                    'dnd_user_context_select',
                    'dnd_user_context_insert',
                    'dnd_user_context_update',
                    'dnd_user_context_delete'
                )
          )
          AND gateway.proowner = dnd_owner.oid
          AND NOT gateway.prosecdef
          AND gateway.proconfig = ARRAY[
              'search_path=pg_catalog, public, dnd_generation_private, pg_temp'
          ]::text[]
          AND private_mutation.proowner = dnd_owner.oid
          AND private_mutation.prosecdef
          AND private_mutation.proconfig = ARRAY[
              'search_path=pg_catalog, public, pg_temp'
          ]::text[]
          AND canonical_json.proowner = dnd_owner.oid
          AND NOT canonical_json.prosecdef
          AND canonical_json.proconfig = ARRAY[
              'search_path=pg_catalog, pg_temp'
          ]::text[]
          AND NOT EXISTS (
              SELECT 1
              FROM aclexplode(COALESCE(user_context.relacl, acldefault('r', user_context.relowner)))
                  AS acl
              WHERE acl.grantee = 0
                AND acl.privilege_type IN ('SELECT', 'INSERT', 'UPDATE', 'DELETE')
          )
          AND NOT EXISTS (
              SELECT 1
              FROM aclexplode(COALESCE(user_context.relacl, acldefault('r', user_context.relowner)))
                  AS acl
              WHERE acl.privilege_type IN ('DELETE', 'TRUNCATE', 'REFERENCES', 'TRIGGER')
                AND acl.grantee <> dnd_owner.oid
          )
          AND NOT EXISTS (
              SELECT 1
              FROM aclexplode(COALESCE(guard_row.relacl, acldefault('r', guard_row.relowner)))
                  AS acl
              WHERE (
                  acl.grantee = 0
                  OR (acl.privilege_type <> 'SELECT' AND acl.grantee <> dnd_owner.oid)
              )
          )
          AND NOT EXISTS (
              SELECT 1
              FROM aclexplode(
                  COALESCE(mutation_audit.relacl, acldefault('r', mutation_audit.relowner))
              ) AS acl
              WHERE acl.grantee = 0
          )
          AND NOT EXISTS (
              SELECT 1
              FROM aclexplode(COALESCE(gateway.proacl, acldefault('f', gateway.proowner)))
                  AS acl
              WHERE acl.grantee = 0 AND acl.privilege_type = 'EXECUTE'
          )
          AND NOT EXISTS (
              SELECT 1
              FROM aclexplode(
                  COALESCE(private_mutation.proacl, acldefault('f', private_mutation.proowner))
              ) AS acl
              WHERE acl.grantee = 0 AND acl.privilege_type = 'EXECUTE'
          )
          AND NOT EXISTS (
              SELECT 1
              FROM aclexplode(
                  COALESCE(canonical_json.proacl, acldefault('f', canonical_json.proowner))
              ) AS acl
              WHERE acl.grantee = 0 AND acl.privilege_type = 'EXECUTE'
          )
          AND has_function_privilege(general_runtime.oid, gateway.oid, 'EXECUTE')
          AND has_function_privilege(switchboard_runtime.oid, gateway.oid, 'EXECUTE')
          AND has_function_privilege(general_runtime.oid, private_mutation.oid, 'EXECUTE')
          AND has_function_privilege(switchboard_runtime.oid, private_mutation.oid, 'EXECUTE')
          AND has_schema_privilege(general_runtime.oid, private_schema.oid, 'USAGE')
          AND has_schema_privilege(switchboard_runtime.oid, private_schema.oid, 'USAGE')
          AND NOT EXISTS (
              SELECT 1
              FROM aclexplode(COALESCE(gateway.proacl, acldefault('f', gateway.proowner)))
                  AS acl
              WHERE acl.privilege_type = 'EXECUTE'
                AND acl.grantee NOT IN (
                    dnd_owner.oid, general_runtime.oid, switchboard_runtime.oid
                )
          )
          AND NOT EXISTS (
              SELECT 1
              FROM aclexplode(
                  COALESCE(private_mutation.proacl, acldefault('f', private_mutation.proowner))
              ) AS acl
              WHERE acl.privilege_type = 'EXECUTE'
                AND acl.grantee NOT IN (
                    dnd_owner.oid, general_runtime.oid, switchboard_runtime.oid
                )
          )
          AND NOT EXISTS (
              SELECT 1
              FROM aclexplode(
                  COALESCE(canonical_json.proacl, acldefault('f', canonical_json.proowner))
              ) AS acl
              WHERE acl.privilege_type = 'EXECUTE'
                AND acl.grantee <> dnd_owner.oid
          )
          AND NOT EXISTS (
              SELECT 1
              FROM aclexplode(
                  COALESCE(private_schema.nspacl, acldefault('n', private_schema.nspowner))
              ) AS acl
              WHERE acl.grantee NOT IN (
                  dnd_owner.oid, general_runtime.oid, switchboard_runtime.oid
              )
          )
          AND NOT EXISTS (
              SELECT 1
              FROM aclexplode(
                  COALESCE(mutation_audit.relacl, acldefault('r', mutation_audit.relowner))
              ) AS acl
              WHERE acl.grantee <> dnd_owner.oid
          )
          AND NOT EXISTS (
              SELECT 1
              FROM aclexplode(
                  COALESCE(
                      bootstrap_configuration.relacl,
                      acldefault('r', bootstrap_configuration.relowner)
                  )
              ) AS acl
              WHERE acl.grantee <> bootstrap_owner.oid
          )
          AND NOT EXISTS (
              SELECT 1
              FROM aclexplode(COALESCE(admin_schema.nspacl, acldefault('n', admin_schema.nspowner)))
                  AS acl
              WHERE acl.grantee <> bootstrap_owner.oid
          )
          AND NOT EXISTS (
              SELECT 1
              FROM aclexplode(COALESCE(installer.proacl, acldefault('f', installer.proowner)))
                  AS acl
              WHERE acl.privilege_type = 'EXECUTE'
                AND acl.grantee <> bootstrap_owner.oid
          )
          AND NOT EXISTS (
              SELECT 1
              FROM aclexplode(COALESCE(finalizer.proacl, acldefault('f', finalizer.proowner)))
                  AS acl
              WHERE acl.privilege_type = 'EXECUTE'
                AND acl.grantee <> bootstrap_owner.oid
          )
          AND NOT EXISTS (
              SELECT 1
              FROM aclexplode(
                  COALESCE(rollback_interface.proacl, acldefault('f', rollback_interface.proowner))
              ) AS acl
              WHERE acl.privilege_type = 'EXECUTE'
                AND acl.grantee <> bootstrap_owner.oid
          )
          AND bootstrap_owner.rolsuper
          AND bootstrap_configuration.relowner = bootstrap_owner.oid
          AND installer.proowner = admin_schema.nspowner
          AND finalizer.proowner = admin_schema.nspowner
          AND rollback_interface.proowner = admin_schema.nspowner
          AND installer.prosecdef
          AND finalizer.prosecdef
          AND rollback_interface.prosecdef
          AND installer.proconfig = ARRAY['search_path=pg_catalog, pg_temp']::text[]
          AND finalizer.proconfig = ARRAY['search_path=pg_catalog, pg_temp']::text[]
          AND rollback_interface.proconfig = ARRAY['search_path=pg_catalog, pg_temp']::text[]
          -- The shared connecting user inherits ordinary runtime-role grants.
          -- Prove it has no *direct* DND ACL: effective inherited gateway
          -- visibility is not sufficient authority because the invoker and
          -- private operation both reject it unless an active canonical SET
          -- ROLE is present. Core migrations run under this un-set role.
          AND NOT pg_has_role(current_user, dnd_owner.oid, 'USAGE')
          AND NOT EXISTS (
              SELECT 1
              FROM aclexplode(COALESCE(guard_row.relacl, acldefault('r', guard_row.relowner)))
                  AS acl
              WHERE acl.grantee = migration_role.oid
          )
          AND NOT EXISTS (
              SELECT 1
              FROM aclexplode(
                  COALESCE(mutation_audit.relacl, acldefault('r', mutation_audit.relowner))
              ) AS acl
              WHERE acl.grantee = migration_role.oid
          )
          AND NOT EXISTS (
              SELECT 1
              FROM aclexplode(
                  COALESCE(private_schema.nspacl, acldefault('n', private_schema.nspowner))
              ) AS acl
              WHERE acl.grantee = migration_role.oid
          )
          AND NOT EXISTS (
              SELECT 1
              FROM aclexplode(COALESCE(gateway.proacl, acldefault('f', gateway.proowner)))
                  AS acl
              WHERE acl.grantee = migration_role.oid
                AND acl.privilege_type = 'EXECUTE'
          )
          AND NOT EXISTS (
              SELECT 1
              FROM aclexplode(
                  COALESCE(private_mutation.proacl, acldefault('f', private_mutation.proowner))
              ) AS acl
              WHERE acl.grantee = migration_role.oid
                AND acl.privilege_type = 'EXECUTE'
          )
          AND NOT EXISTS (
              SELECT 1
              FROM aclexplode(COALESCE(admin_schema.nspacl, acldefault('n', admin_schema.nspowner)))
                  AS acl
              WHERE acl.grantee = migration_role.oid
          )
          AND NOT EXISTS (
              SELECT 1
              FROM aclexplode(COALESCE(installer.proacl, acldefault('f', installer.proowner)))
                  AS acl
              WHERE acl.grantee = migration_role.oid
                AND acl.privilege_type = 'EXECUTE'
          )
          AND NOT EXISTS (
              SELECT 1
              FROM aclexplode(COALESCE(finalizer.proacl, acldefault('f', finalizer.proowner)))
                  AS acl
              WHERE acl.grantee = migration_role.oid
                AND acl.privilege_type = 'EXECUTE'
          )
          AND NOT EXISTS (
              SELECT 1
              FROM aclexplode(
                  COALESCE(rollback_interface.proacl, acldefault('f', rollback_interface.proowner))
              ) AS acl
              WHERE acl.grantee = migration_role.oid
                AND acl.privilege_type = 'EXECUTE'
          )
    )
"""

_TRUSTED_BOOTSTRAP_INSTALLER_SQL = """
    SELECT EXISTS (
        SELECT 1
        FROM pg_namespace AS admin_schema
        JOIN pg_roles AS bootstrap_owner
            ON bootstrap_owner.oid = admin_schema.nspowner
        JOIN pg_roles AS installer_operator
            ON installer_operator.rolname = current_user
        JOIN pg_class AS bootstrap_configuration
            ON bootstrap_configuration.relnamespace = admin_schema.oid
           AND bootstrap_configuration.relname = 'bootstrap_configuration'
           AND bootstrap_configuration.relkind = 'r'
        JOIN pg_proc AS installer
            ON installer.pronamespace = admin_schema.oid
           AND installer.proname = 'install_interface'
           AND installer.pronargs = 0
           AND installer.prorettype = 'void'::regtype
        JOIN pg_proc AS finalizer
            ON finalizer.pronamespace = admin_schema.oid
           AND finalizer.proname = 'finalize_interface'
           AND finalizer.pronargs = 0
           AND finalizer.prorettype = 'void'::regtype
        JOIN pg_proc AS rollback_interface
            ON rollback_interface.pronamespace = admin_schema.oid
           AND rollback_interface.proname = 'rollback_interface'
           AND rollback_interface.pronargs = 0
           AND rollback_interface.prorettype = 'void'::regtype
        WHERE admin_schema.nspname = 'dnd_generation_admin'
          AND bootstrap_owner.rolsuper
          AND bootstrap_configuration.relowner = bootstrap_owner.oid
          AND installer.proowner = admin_schema.nspowner
          AND finalizer.proowner = admin_schema.nspowner
          AND rollback_interface.proowner = admin_schema.nspowner
          AND installer.prosecdef
          AND finalizer.prosecdef
          AND rollback_interface.prosecdef
          AND installer.proconfig = ARRAY['search_path=pg_catalog, pg_temp']::text[]
          AND finalizer.proconfig = ARRAY['search_path=pg_catalog, pg_temp']::text[]
          AND rollback_interface.proconfig = ARRAY['search_path=pg_catalog, pg_temp']::text[]
          AND NOT EXISTS (
              SELECT 1
              FROM aclexplode(COALESCE(admin_schema.nspacl, acldefault('n', admin_schema.nspowner)))
                  AS acl
              WHERE acl.grantee = 0
          )
          AND NOT EXISTS (
              SELECT 1
              FROM aclexplode(COALESCE(installer.proacl, acldefault('f', installer.proowner)))
                  AS acl
              WHERE acl.grantee = 0 AND acl.privilege_type = 'EXECUTE'
          )
          AND NOT EXISTS (
              SELECT 1
              FROM aclexplode(COALESCE(finalizer.proacl, acldefault('f', finalizer.proowner)))
                  AS acl
              WHERE acl.grantee = 0 AND acl.privilege_type = 'EXECUTE'
          )
          AND NOT EXISTS (
              SELECT 1
              FROM aclexplode(
                  COALESCE(rollback_interface.proacl, acldefault('f', rollback_interface.proowner))
              ) AS acl
              WHERE acl.grantee = 0 AND acl.privilege_type = 'EXECUTE'
          )
          AND (
              (
                  has_schema_privilege(current_user, admin_schema.oid, 'USAGE')
                  AND has_function_privilege(current_user, installer.oid, 'EXECUTE')
                  AND NOT has_function_privilege(current_user, finalizer.oid, 'EXECUTE')
                  AND NOT has_function_privilege(current_user, rollback_interface.oid, 'EXECUTE')
              )
              -- A controlled one-step core rollback/reapply uses the same
              -- catalog-proven interface under trusted cluster-superuser
              -- authority; it is not a migration-owned fallback.
              OR installer_operator.rolsuper
          )
    )
"""

_TRUSTED_BOOTSTRAP_ROLLBACK_SQL = """
    SELECT EXISTS (
        SELECT 1
        FROM pg_roles AS rollback_operator
        JOIN pg_namespace AS admin_schema
            ON admin_schema.nspname = 'dnd_generation_admin'
        JOIN pg_roles AS bootstrap_owner
            ON bootstrap_owner.oid = admin_schema.nspowner
        JOIN pg_class AS bootstrap_configuration_relation
            ON bootstrap_configuration_relation.relnamespace = admin_schema.oid
           AND bootstrap_configuration_relation.relname = 'bootstrap_configuration'
           AND bootstrap_configuration_relation.relkind = 'r'
        JOIN dnd_generation_admin.bootstrap_configuration AS bootstrap_configuration
            ON bootstrap_configuration.singleton
        JOIN pg_roles AS configured_bootstrap_owner
            ON configured_bootstrap_owner.rolname = bootstrap_configuration.bootstrap_role
        JOIN pg_roles AS migration_role
            ON migration_role.rolname = bootstrap_configuration.migration_role
        JOIN pg_proc AS finalizer
            ON finalizer.pronamespace = admin_schema.oid
           AND finalizer.proname = 'finalize_interface'
           AND finalizer.pronargs = 0
           AND finalizer.prorettype = 'void'::regtype
        JOIN pg_proc AS rollback_interface
            ON rollback_interface.pronamespace = admin_schema.oid
           AND rollback_interface.proname = 'rollback_interface'
           AND rollback_interface.pronargs = 0
           AND rollback_interface.prorettype = 'void'::regtype
        WHERE rollback_operator.rolname = current_user
          AND rollback_operator.rolsuper
          AND bootstrap_owner.rolsuper
          AND bootstrap_configuration_relation.relowner = bootstrap_owner.oid
          AND configured_bootstrap_owner.oid = bootstrap_owner.oid
          AND configured_bootstrap_owner.rolsuper
          AND NOT migration_role.rolsuper
          AND finalizer.proowner = admin_schema.nspowner
          AND finalizer.prosecdef
          AND finalizer.proconfig = ARRAY['search_path=pg_catalog, pg_temp']::text[]
          AND rollback_interface.proowner = admin_schema.nspowner
          AND rollback_interface.prosecdef
          AND rollback_interface.proconfig = ARRAY['search_path=pg_catalog, pg_temp']::text[]
          AND NOT has_function_privilege(migration_role.oid, rollback_interface.oid, 'EXECUTE')
          AND NOT EXISTS (
              SELECT 1
              FROM aclexplode(COALESCE(admin_schema.nspacl, acldefault('n', admin_schema.nspowner)))
                  AS acl
              WHERE acl.grantee <> bootstrap_owner.oid
          )
          AND NOT EXISTS (
              SELECT 1
              FROM aclexplode(
                  COALESCE(
                      bootstrap_configuration_relation.relacl,
                      acldefault('r', bootstrap_configuration_relation.relowner)
                  )
              ) AS acl
              WHERE acl.grantee <> bootstrap_owner.oid
          )
          AND NOT EXISTS (
              SELECT 1
              FROM aclexplode(COALESCE(finalizer.proacl, acldefault('f', finalizer.proowner)))
                  AS acl
              WHERE acl.privilege_type = 'EXECUTE'
                AND acl.grantee <> bootstrap_owner.oid
          )
          AND NOT EXISTS (
              SELECT 1
              FROM aclexplode(
                  COALESCE(rollback_interface.proacl, acldefault('f', rollback_interface.proowner))
              ) AS acl
              WHERE acl.privilege_type = 'EXECUTE'
                AND acl.grantee <> bootstrap_owner.oid
          )
    )
"""


def upgrade() -> None:
    """Invoke only a catalog-proven trusted bootstrap installer.

    Core migrations run once per schema.  The first invocation finalizes this
    database-global boundary and revokes the shared migration caller's installer
    access.  Later invocations can no-op only after proving the exact finalized
    catalog; a missing or spoofed shape never becomes migration-owned DDL.
    """
    bind = op.get_bind()
    if bool(bind.execute(sa.text(_TRUSTED_FINALIZED_INTERFACE_SQL)).scalar_one()):
        return

    if not bool(bind.execute(sa.text(_TRUSTED_BOOTSTRAP_INSTALLER_SQL)).scalar_one()):
        op.execute(
            """
            DO $$
            BEGIN
                RAISE EXCEPTION
                    'DND generation bootstrap installer is missing or untrusted; '
                    'run scripts/init-db.sql as the privileged bootstrap first';
            END;
            $$;
            """
        )

    # The fixed installer owns all authority-bearing DDL and rejects a partial
    # or attacker-shaped interface.  The normal migration caller cannot create
    # or repair a matching object as a fallback.
    op.execute(f"SELECT {_ADMIN_INSTALLER}()")


def downgrade() -> None:
    """Delegate only an unused boundary rollback to trusted bootstrap authority."""
    bind = op.get_bind()
    if not bool(bind.execute(sa.text(_TRUSTED_BOOTSTRAP_ROLLBACK_SQL)).scalar_one()):
        op.execute(
            """
            DO $$
            BEGIN
                RAISE EXCEPTION
                    'core_197 downgrade requires trusted bootstrap rollback interface';
            END;
            $$;
            """
        )

    # The bootstrap-owned routine rejects durable receipts/nonzero generations,
    # restores the pre-guard ownership and RLS shape, and re-exposes only the
    # fixed installer handoff for a subsequent upgrade.
    op.execute(f"SELECT {_ADMIN_ROLLBACK}()")
