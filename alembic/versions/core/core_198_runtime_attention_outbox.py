"""Install the inert runtime-attention outbox through trusted bootstrap only.

Revision ID: core_198
Revises: core_197
Create Date: 2026-08-14 00:00:00.000000

``public`` objects are database-global while the core chain runs once for each
target schema.  The normal migration login therefore never owns this boundary:
it can invoke the one-time, bootstrap-provisioned installer only, and later
schema runs no-op after proving the finalized catalog shape.  The installer is
also the post-``init-db`` ACL repair authority, because init-db intentionally
has broad baseline public grants for older shared tables.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "core_198"
down_revision = "core_197"
branch_labels = None
depends_on = None

_ADMIN_INSTALLER = "runtime_attention_admin.install_interface"
_ADMIN_ROLLBACK = "runtime_attention_admin.rollback_interface"

# Core migrations execute once per target schema, while this trusted interface
# is database-global.  Serialize both install and rollback decisions for the
# full migration transaction so every later target rechecks only after the
# first target has committed the shared boundary transition.
_INSTALLER_SERIALIZATION_LOCK_SQL = """
    SELECT pg_advisory_xact_lock(
        hashtextextended('butlers:core_198:runtime_attention_interface', 0)
    )
"""


# This is deliberately catalog based rather than a to_regclass shortcut.  A
# later core-chain invocation must only trust the exact bootstrap-owned,
# least-privilege interface, never silently adopt an attacker-shaped public
# table or a migration-login-owned SECURITY DEFINER function.
_TRUSTED_FINALIZED_INTERFACE_SQL_TEMPLATE = """
    SELECT EXISTS (
        SELECT 1
        FROM pg_roles AS outbox_owner
        JOIN pg_roles AS migration_role
          ON migration_role.rolname = current_user
        JOIN pg_roles AS general_runtime
          ON general_runtime.rolname = 'butler_general_rw'
        JOIN pg_roles AS switchboard_runtime
          ON switchboard_runtime.rolname = 'butler_switchboard_rw'
        JOIN pg_roles AS connector_runtime
          ON connector_runtime.rolname = 'connector_writer'
        JOIN pg_namespace AS public_schema
          ON public_schema.nspname = 'public'
        JOIN pg_namespace AS admin_schema
          ON admin_schema.nspname = 'runtime_attention_admin'
        JOIN pg_roles AS bootstrap_owner
          ON bootstrap_owner.oid = admin_schema.nspowner
        JOIN pg_class AS outbox
          ON outbox.relnamespace = public_schema.oid
         AND outbox.relname = 'runtime_attention_outbox'
         AND outbox.relkind = 'r'
        JOIN pg_class AS delivery_lease
          ON delivery_lease.relnamespace = public_schema.oid
         AND delivery_lease.relname = 'runtime_attention_delivery_lease'
         AND delivery_lease.relkind = 'r'
        JOIN pg_class AS dispatch_attempts
          ON dispatch_attempts.relnamespace = public_schema.oid
         AND dispatch_attempts.relname = 'model_dispatch_attempts'
         AND dispatch_attempts.relkind = 'r'
        JOIN pg_class AS bootstrap_configuration
          ON bootstrap_configuration.relnamespace = admin_schema.oid
         AND bootstrap_configuration.relname = 'bootstrap_configuration'
         AND bootstrap_configuration.relkind = 'r'
        JOIN pg_proc AS model_breaker
          ON model_breaker.pronamespace = public_schema.oid
         AND model_breaker.proname = 'append_runtime_attention_model_breaker'
         AND model_breaker.proargtypes = '20'::oidvector
         AND model_breaker.prorettype = 'uuid'::regtype
        JOIN pg_proc AS fleet_halt
          ON fleet_halt.pronamespace = public_schema.oid
         AND fleet_halt.proname = 'append_runtime_attention_fleet_halt'
         AND fleet_halt.pronargs = 0
         AND fleet_halt.prorettype = 'uuid'::regtype
        JOIN pg_proc AS active_switchboard_role
          ON active_switchboard_role.pronamespace = public_schema.oid
         AND active_switchboard_role.proname = 'runtime_attention_active_switchboard_role'
         AND active_switchboard_role.pronargs = 0
         AND active_switchboard_role.prorettype = 'boolean'::regtype
        JOIN pg_proc AS outbox_guard
          ON outbox_guard.pronamespace = public_schema.oid
         AND outbox_guard.proname = 'runtime_attention_outbox_guard'
         AND outbox_guard.pronargs = 0
         AND outbox_guard.prorettype = 'trigger'::regtype
        JOIN pg_proc AS delivery_lease_guard
          ON delivery_lease_guard.pronamespace = public_schema.oid
         AND delivery_lease_guard.proname = 'runtime_attention_delivery_lease_guard'
         AND delivery_lease_guard.pronargs = 0
         AND delivery_lease_guard.prorettype = 'trigger'::regtype
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
        WHERE outbox_owner.rolname = 'runtime_attention_outbox_owner'
          AND NOT outbox_owner.rolcanlogin
          AND NOT outbox_owner.rolsuper
          AND NOT outbox_owner.rolcreaterole
          AND NOT outbox_owner.rolcreatedb
          AND NOT outbox_owner.rolreplication
          AND NOT outbox_owner.rolbypassrls
          AND NOT outbox_owner.rolinherit
          AND __CALLER_ROLE_CONSTRAINT__
          AND NOT EXISTS (
              SELECT 1
              FROM pg_auth_members AS member
              WHERE member.roleid = outbox_owner.oid
                 OR member.member = outbox_owner.oid
          )
          AND bootstrap_owner.rolsuper
          AND bootstrap_configuration.relowner = admin_schema.nspowner
          AND outbox.relowner = outbox_owner.oid
          AND delivery_lease.relowner = outbox_owner.oid
          AND outbox.relrowsecurity
          AND outbox.relforcerowsecurity
          AND delivery_lease.relrowsecurity
          AND delivery_lease.relforcerowsecurity
          AND model_breaker.proowner = outbox_owner.oid
          AND fleet_halt.proowner = outbox_owner.oid
          AND active_switchboard_role.proowner = outbox_owner.oid
          AND outbox_guard.proowner = outbox_owner.oid
          AND delivery_lease_guard.proowner = outbox_owner.oid
          AND model_breaker.prosecdef
          AND fleet_halt.prosecdef
          AND active_switchboard_role.prosecdef
          AND outbox_guard.prosecdef
          AND delivery_lease_guard.prosecdef
          AND model_breaker.proconfig = ARRAY[
              'search_path=pg_catalog, public, pg_temp'
          ]::text[]
          AND fleet_halt.proconfig = ARRAY[
              'search_path=pg_catalog, public, pg_temp'
          ]::text[]
          AND active_switchboard_role.proconfig = ARRAY[
              'search_path=pg_catalog, public, pg_temp'
          ]::text[]
          AND outbox_guard.proconfig = ARRAY[
              'search_path=pg_catalog, public, pg_temp'
          ]::text[]
          AND delivery_lease_guard.proconfig = ARRAY[
              'search_path=pg_catalog, public, pg_temp'
          ]::text[]
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
              FROM aclexplode(
                  COALESCE(admin_schema.nspacl, acldefault('n', admin_schema.nspowner))
              ) AS acl
              WHERE acl.grantee <> bootstrap_owner.oid
          )
          AND NOT EXISTS (
              SELECT 1
              FROM pg_proc AS admin_function
              CROSS JOIN LATERAL aclexplode(
                  COALESCE(admin_function.proacl, acldefault('f', admin_function.proowner))
              ) AS acl
              WHERE admin_function.oid IN (installer.oid, finalizer.oid, rollback_interface.oid)
                AND acl.privilege_type = 'EXECUTE'
                AND acl.grantee <> bootstrap_owner.oid
          )
          AND NOT EXISTS (
              SELECT 1
              FROM aclexplode(COALESCE(outbox.relacl, acldefault('r', outbox.relowner))) AS acl
              WHERE acl.grantee = 0
                AND acl.privilege_type IN ('SELECT', 'INSERT', 'UPDATE', 'DELETE')
          )
          AND NOT EXISTS (
              SELECT 1
              FROM aclexplode(
                  COALESCE(delivery_lease.relacl, acldefault('r', delivery_lease.relowner))
              ) AS acl
              WHERE acl.grantee = 0
                AND acl.privilege_type IN ('SELECT', 'INSERT', 'UPDATE', 'DELETE')
          )
          AND NOT EXISTS (
              SELECT 1
              FROM aclexplode(
                  COALESCE(model_breaker.proacl, acldefault('f', model_breaker.proowner))
              ) AS acl
              WHERE acl.grantee = 0 AND acl.privilege_type = 'EXECUTE'
          )
          AND NOT EXISTS (
              SELECT 1
              FROM aclexplode(
                  COALESCE(fleet_halt.proacl, acldefault('f', fleet_halt.proowner))
              ) AS acl
              WHERE acl.grantee = 0 AND acl.privilege_type = 'EXECUTE'
          )
          AND NOT EXISTS (
              SELECT 1
              FROM pg_proc AS interface_function
              CROSS JOIN LATERAL aclexplode(
                  COALESCE(
                      interface_function.proacl,
                      acldefault('f', interface_function.proowner)
                  )
              ) AS acl
              WHERE interface_function.oid IN (
                  active_switchboard_role.oid,
                  outbox_guard.oid,
                  delivery_lease_guard.oid
              )
                AND acl.grantee = 0
                AND acl.privilege_type = 'EXECUTE'
          )
          AND NOT EXISTS (
              SELECT 1
              FROM aclexplode(
                  COALESCE(model_breaker.proacl, acldefault('f', model_breaker.proowner))
              ) AS acl
              LEFT JOIN pg_roles AS granted_role ON granted_role.oid = acl.grantee
              WHERE acl.privilege_type = 'EXECUTE'
                AND acl.grantee <> outbox_owner.oid
                AND COALESCE(granted_role.rolname, '') <> ALL (ARRAY[
                    'butler_chronicler_rw', 'butler_education_rw', 'butler_finance_rw',
                    'butler_general_rw', 'butler_health_rw', 'butler_home_rw',
                    'butler_lifestyle_rw', 'butler_messenger_rw', 'butler_qa_rw',
                    'butler_relationship_rw', 'butler_switchboard_rw', 'butler_travel_rw'
                ]::name[])
          )
          AND NOT EXISTS (
              SELECT 1
              FROM aclexplode(
                  COALESCE(fleet_halt.proacl, acldefault('f', fleet_halt.proowner))
              ) AS acl
              LEFT JOIN pg_roles AS granted_role ON granted_role.oid = acl.grantee
              WHERE acl.privilege_type = 'EXECUTE'
                AND acl.grantee <> outbox_owner.oid
                AND COALESCE(granted_role.rolname, '') <> ALL (ARRAY[
                    'butler_chronicler_rw', 'butler_education_rw', 'butler_finance_rw',
                    'butler_general_rw', 'butler_health_rw', 'butler_home_rw',
                    'butler_lifestyle_rw', 'butler_messenger_rw', 'butler_qa_rw',
                    'butler_relationship_rw', 'butler_switchboard_rw', 'butler_travel_rw'
                ]::name[])
          )
          AND has_function_privilege(general_runtime.oid, model_breaker.oid, 'EXECUTE')
          AND has_function_privilege(general_runtime.oid, fleet_halt.oid, 'EXECUTE')
          AND (
              SELECT count(*)
              FROM pg_roles AS producer_runtime
              WHERE producer_runtime.rolname = ANY (ARRAY[
                  'butler_chronicler_rw', 'butler_education_rw', 'butler_finance_rw',
                  'butler_general_rw', 'butler_health_rw', 'butler_home_rw',
                  'butler_lifestyle_rw', 'butler_messenger_rw', 'butler_qa_rw',
                  'butler_relationship_rw', 'butler_switchboard_rw', 'butler_travel_rw'
              ]::name[])
          ) = 12
          AND NOT EXISTS (
              SELECT 1
              FROM pg_roles AS producer_runtime
              WHERE producer_runtime.rolname = ANY (ARRAY[
                  'butler_chronicler_rw', 'butler_education_rw', 'butler_finance_rw',
                  'butler_general_rw', 'butler_health_rw', 'butler_home_rw',
                  'butler_lifestyle_rw', 'butler_messenger_rw', 'butler_qa_rw',
                  'butler_relationship_rw', 'butler_switchboard_rw', 'butler_travel_rw'
              ]::name[])
                AND (
                    NOT has_function_privilege(
                        producer_runtime.oid, model_breaker.oid, 'EXECUTE'
                    )
                    OR NOT has_function_privilege(
                        producer_runtime.oid, fleet_halt.oid, 'EXECUTE'
                    )
                )
          )
          AND NOT has_function_privilege(connector_runtime.oid, model_breaker.oid, 'EXECUTE')
          AND NOT has_function_privilege(connector_runtime.oid, fleet_halt.oid, 'EXECUTE')
          AND __CALLER_OUTBOX_OWNER_CONSTRAINT__
          AND NOT EXISTS (
              SELECT 1
              FROM aclexplode(
                  COALESCE(model_breaker.proacl, acldefault('f', model_breaker.proowner))
              ) AS acl
              WHERE acl.grantee = migration_role.oid
                AND acl.privilege_type = 'EXECUTE'
          )
          AND NOT EXISTS (
              SELECT 1
              FROM aclexplode(
                  COALESCE(fleet_halt.proacl, acldefault('f', fleet_halt.proowner))
              ) AS acl
              WHERE acl.grantee = migration_role.oid
                AND acl.privilege_type = 'EXECUTE'
          )
          AND NOT has_table_privilege(general_runtime.oid, outbox.oid, 'SELECT')
          AND NOT has_table_privilege(general_runtime.oid, outbox.oid, 'INSERT')
          AND NOT has_table_privilege(general_runtime.oid, outbox.oid, 'UPDATE')
          AND NOT has_table_privilege(general_runtime.oid, outbox.oid, 'DELETE')
          AND NOT has_table_privilege(connector_runtime.oid, outbox.oid, 'SELECT')
          AND NOT has_table_privilege(connector_runtime.oid, outbox.oid, 'INSERT')
          AND NOT has_table_privilege(connector_runtime.oid, outbox.oid, 'UPDATE')
          AND NOT has_table_privilege(connector_runtime.oid, outbox.oid, 'DELETE')
          AND NOT EXISTS (
              SELECT 1
              FROM pg_roles AS producer_runtime
              WHERE producer_runtime.rolname = ANY (ARRAY[
                  'butler_chronicler_rw', 'butler_education_rw', 'butler_finance_rw',
                  'butler_general_rw', 'butler_health_rw', 'butler_home_rw',
                  'butler_lifestyle_rw', 'butler_messenger_rw', 'butler_qa_rw',
                  'butler_relationship_rw', 'butler_travel_rw'
              ]::name[])
                AND (
                    has_table_privilege(producer_runtime.oid, outbox.oid, 'SELECT')
                    OR has_table_privilege(producer_runtime.oid, outbox.oid, 'INSERT')
                    OR has_table_privilege(producer_runtime.oid, outbox.oid, 'UPDATE')
                    OR has_table_privilege(producer_runtime.oid, outbox.oid, 'DELETE')
                )
          )
          AND has_table_privilege(
              outbox_owner.oid, 'public.model_catalog'::regclass, 'SELECT'
          )
          AND has_table_privilege(
              outbox_owner.oid, 'public.model_dispatch_attempts'::regclass, 'SELECT'
          )
          AND NOT has_table_privilege(
              outbox_owner.oid, 'public.model_catalog'::regclass, 'INSERT'
          )
          AND NOT has_table_privilege(
              outbox_owner.oid, 'public.model_catalog'::regclass, 'UPDATE'
          )
          AND NOT has_table_privilege(
              outbox_owner.oid, 'public.model_catalog'::regclass, 'DELETE'
          )
          AND NOT has_table_privilege(
              outbox_owner.oid, 'public.model_dispatch_attempts'::regclass, 'INSERT'
          )
          AND NOT has_table_privilege(
              outbox_owner.oid, 'public.model_dispatch_attempts'::regclass, 'UPDATE'
          )
          AND NOT has_table_privilege(
              outbox_owner.oid, 'public.model_dispatch_attempts'::regclass, 'DELETE'
          )
          AND has_table_privilege(switchboard_runtime.oid, outbox.oid, 'SELECT')
          AND NOT has_table_privilege(switchboard_runtime.oid, outbox.oid, 'INSERT')
          AND NOT has_table_privilege(switchboard_runtime.oid, outbox.oid, 'DELETE')
          AND has_column_privilege(
              switchboard_runtime.oid, outbox.oid, 'lifecycle_state', 'UPDATE'
          )
          AND has_column_privilege(
              switchboard_runtime.oid, outbox.oid, 'claim_epoch', 'UPDATE'
          )
          AND has_column_privilege(
              switchboard_runtime.oid, outbox.oid, 'claim_token', 'UPDATE'
          )
          AND NOT has_column_privilege(
              switchboard_runtime.oid, outbox.oid, 'delivery_error_class', 'UPDATE'
          )
          AND NOT has_column_privilege(
              switchboard_runtime.oid, outbox.oid, 'delivery_error_detail', 'UPDATE'
          )
          AND NOT has_column_privilege(
              switchboard_runtime.oid, outbox.oid, 'notification_ref', 'UPDATE'
          )
          AND has_table_privilege(switchboard_runtime.oid, delivery_lease.oid, 'SELECT')
          AND has_table_privilege(switchboard_runtime.oid, delivery_lease.oid, 'INSERT')
          AND has_table_privilege(switchboard_runtime.oid, delivery_lease.oid, 'UPDATE')
          AND NOT has_table_privilege(switchboard_runtime.oid, delivery_lease.oid, 'DELETE')
          AND has_function_privilege(
              switchboard_runtime.oid, active_switchboard_role.oid, 'EXECUTE'
          )
          AND NOT EXISTS (
              SELECT 1
              FROM pg_constraint AS constraint_row
              WHERE constraint_row.conrelid = outbox.oid
                AND constraint_row.contype = 'f'
          )
          AND (
              SELECT count(*)
              FROM pg_constraint AS constraint_row
              WHERE constraint_row.conrelid = outbox.oid
                AND constraint_row.conname = ANY (ARRAY[
                    'ck_runtime_attention_outbox_source_edge',
                    'ck_runtime_attention_outbox_snapshot_allowlist',
                    'ck_runtime_attention_outbox_payload_allowlist',
                    'ck_runtime_attention_outbox_retention',
                    'ck_runtime_attention_outbox_claim_shape',
                    'ck_runtime_attention_outbox_claim_expiry',
                    'ck_runtime_attention_outbox_delivery_shape',
                    'ck_runtime_attention_outbox_delivery_evidence'
                ]::name[])
          ) = 8
          AND EXISTS (
              SELECT 1
              FROM pg_attribute AS attribute
              WHERE attribute.attrelid = outbox.oid
                AND attribute.attname = 'delivery_error_class'
                AND attribute.atttypid = 'text'::regtype
                AND NOT attribute.attnotnull
                AND NOT attribute.atthasdef
          )
          AND EXISTS (
              SELECT 1
              FROM pg_attribute AS attribute
              WHERE attribute.attrelid = outbox.oid
                AND attribute.attname = 'delivery_error_detail'
                AND attribute.atttypid = 'text'::regtype
                AND NOT attribute.attnotnull
                AND NOT attribute.atthasdef
          )
          AND EXISTS (
              SELECT 1
              FROM pg_attribute AS attribute
              WHERE attribute.attrelid = outbox.oid
                AND attribute.attname = 'notification_ref'
                AND attribute.atttypid = 'uuid'::regtype
                AND NOT attribute.attnotnull
                AND NOT attribute.atthasdef
          )
          AND EXISTS (
              SELECT 1
              FROM pg_constraint AS constraint_row
              WHERE constraint_row.conrelid = delivery_lease.oid
                AND constraint_row.conname = 'ck_runtime_attention_delivery_lease_shape'
          )
          AND EXISTS (
              SELECT 1
              FROM pg_class AS index_relation
              JOIN pg_index AS index_row ON index_row.indexrelid = index_relation.oid
              JOIN pg_am AS access_method ON access_method.oid = index_relation.relam
              WHERE index_relation.relnamespace = public_schema.oid
                AND index_relation.relname = 'idx_model_dispatch_attempts_catalog_ts_id'
                AND index_relation.relkind = 'i'
                AND index_relation.relpersistence = 'p'
                AND index_relation.reloptions IS NULL
                AND access_method.amname = 'btree'
                AND index_row.indrelid = dispatch_attempts.oid
                AND index_row.indisvalid
                AND index_row.indisready
                AND index_row.indislive
                AND NOT index_row.indisunique
                AND NOT index_row.indnullsnotdistinct
                AND index_row.indnkeyatts = 3
                AND index_row.indnatts = 3
                AND index_row.indpred IS NULL
                AND index_row.indexprs IS NULL
                AND pg_get_indexdef(index_relation.oid) =
                    'CREATE INDEX idx_model_dispatch_attempts_catalog_ts_id '
                    || 'ON public.model_dispatch_attempts USING btree '
                    || '(catalog_entry_id, ts DESC, id DESC)'
          )
          AND EXISTS (
              SELECT 1
              FROM pg_class AS index_relation
              JOIN pg_index AS index_row ON index_row.indexrelid = index_relation.oid
              JOIN pg_am AS access_method ON access_method.oid = index_relation.relam
              WHERE index_relation.relnamespace = public_schema.oid
                AND index_relation.relname = 'idx_model_dispatch_attempts_outcome_ts_id'
                AND index_relation.relkind = 'i'
                AND index_relation.relpersistence = 'p'
                AND index_relation.reloptions IS NULL
                AND access_method.amname = 'btree'
                AND index_row.indrelid = dispatch_attempts.oid
                AND index_row.indisvalid
                AND index_row.indisready
                AND index_row.indislive
                AND NOT index_row.indisunique
                AND NOT index_row.indnullsnotdistinct
                AND index_row.indnkeyatts = 3
                AND index_row.indnatts = 3
                AND index_row.indpred IS NULL
                AND index_row.indexprs IS NULL
                AND pg_get_indexdef(index_relation.oid) =
                    'CREATE INDEX idx_model_dispatch_attempts_outcome_ts_id '
                    || 'ON public.model_dispatch_attempts USING btree '
                    || '(outcome, ts DESC, id DESC)'
          )
          AND EXISTS (
              SELECT 1 FROM pg_policy AS policy
              WHERE policy.polrelid = outbox.oid
                AND policy.polname = 'runtime_attention_outbox_owner'
                AND policy.polcmd = '*'
                AND policy.polroles = ARRAY[outbox_owner.oid]::oid[]
                AND pg_get_expr(policy.polqual, policy.polrelid) = 'true'
                AND pg_get_expr(policy.polwithcheck, policy.polrelid) = 'true'
          )
          AND EXISTS (
              SELECT 1 FROM pg_policy AS policy
              WHERE policy.polrelid = outbox.oid
                AND policy.polname = 'runtime_attention_outbox_switchboard'
                AND policy.polcmd = '*'
                AND policy.polroles = ARRAY[switchboard_runtime.oid]::oid[]
                AND pg_get_expr(policy.polqual, policy.polrelid)
                    LIKE '%runtime_attention_active_switchboard_role%'
                AND pg_get_expr(policy.polwithcheck, policy.polrelid)
                    LIKE '%runtime_attention_active_switchboard_role%'
          )
          AND EXISTS (
              SELECT 1 FROM pg_policy AS policy
              WHERE policy.polrelid = delivery_lease.oid
                AND policy.polname = 'runtime_attention_delivery_lease_owner'
                AND policy.polcmd = '*'
                AND policy.polroles = ARRAY[outbox_owner.oid]::oid[]
                AND pg_get_expr(policy.polqual, policy.polrelid) = 'true'
                AND pg_get_expr(policy.polwithcheck, policy.polrelid) = 'true'
          )
          AND EXISTS (
              SELECT 1 FROM pg_policy AS policy
              WHERE policy.polrelid = delivery_lease.oid
                AND policy.polname = 'runtime_attention_delivery_lease_switchboard'
                AND policy.polcmd = '*'
                AND policy.polroles = ARRAY[switchboard_runtime.oid]::oid[]
                AND pg_get_expr(policy.polqual, policy.polrelid)
                    LIKE '%runtime_attention_active_switchboard_role%'
                AND pg_get_expr(policy.polwithcheck, policy.polrelid)
                    LIKE '%runtime_attention_active_switchboard_role%'
          )
          AND NOT EXISTS (
              SELECT 1
              FROM pg_policy AS policy
              WHERE policy.polrelid IN (outbox.oid, delivery_lease.oid)
                AND policy.polname NOT IN (
                    'runtime_attention_outbox_owner',
                    'runtime_attention_outbox_switchboard',
                    'runtime_attention_delivery_lease_owner',
                    'runtime_attention_delivery_lease_switchboard'
                )
          )
          AND EXISTS (
              SELECT 1
              FROM pg_trigger AS trigger_row
              WHERE trigger_row.tgrelid = outbox.oid
                AND trigger_row.tgfoid = outbox_guard.oid
                AND trigger_row.tgname = 'runtime_attention_outbox_guard_trigger'
                AND NOT trigger_row.tgisinternal
          )
          AND EXISTS (
              SELECT 1
              FROM pg_trigger AS trigger_row
              WHERE trigger_row.tgrelid = delivery_lease.oid
                AND trigger_row.tgfoid = delivery_lease_guard.oid
                AND trigger_row.tgname = 'runtime_attention_delivery_lease_guard_trigger'
                AND NOT trigger_row.tgisinternal
          )
          AND NOT EXISTS (
              SELECT 1
              FROM pg_trigger AS trigger_row
              WHERE trigger_row.tgrelid = outbox.oid
                AND NOT trigger_row.tgisinternal
                AND (
                    trigger_row.tgname <> 'runtime_attention_outbox_guard_trigger'
                    OR trigger_row.tgfoid <> outbox_guard.oid
                )
          )
          AND NOT EXISTS (
              SELECT 1
              FROM pg_trigger AS trigger_row
              WHERE trigger_row.tgrelid = delivery_lease.oid
                AND NOT trigger_row.tgisinternal
                AND (
                    trigger_row.tgname <> 'runtime_attention_delivery_lease_guard_trigger'
                    OR trigger_row.tgfoid <> delivery_lease_guard.oid
                )
          )
    )
"""

# The catalog/ACL proof is identical for both callers.  Its caller-specific
# constraints remain explicit: normal schema migrations must stay non-super
# and lack owner-role usage; the managed bootstrap superuser may verify its own
# empty rollback/reapply.  PostgreSQL gives superusers implicit owner-role
# usage, so the bootstrap form retains the exact ACL-shape checks while omitting
# only that normal-caller membership assertion.  In either case, the surrounding
# upgrade path still requires the exact trusted bootstrap installer before it
# can create or repair this interface.
_TRUSTED_FINALIZED_INTERFACE_SQL = _TRUSTED_FINALIZED_INTERFACE_SQL_TEMPLATE.replace(
    "__CALLER_ROLE_CONSTRAINT__", "NOT migration_role.rolsuper"
).replace(
    "__CALLER_OUTBOX_OWNER_CONSTRAINT__",
    "NOT pg_has_role(current_user, outbox_owner.oid, 'USAGE')",
)
_TRUSTED_BOOTSTRAP_FINALIZED_INTERFACE_SQL = _TRUSTED_FINALIZED_INTERFACE_SQL_TEMPLATE.replace(
    "__CALLER_ROLE_CONSTRAINT__", "migration_role.rolsuper"
).replace("__CALLER_OUTBOX_OWNER_CONSTRAINT__", "TRUE")


def _has_trusted_finalized_interface(bind: sa.Connection) -> bool:
    """Accept only the exact catalog proof for the active caller's trust mode."""
    return any(
        bool(bind.execute(sa.text(proof_sql)).scalar_one())
        for proof_sql in (
            _TRUSTED_FINALIZED_INTERFACE_SQL,
            _TRUSTED_BOOTSTRAP_FINALIZED_INTERFACE_SQL,
        )
    )


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
        WHERE bootstrap_owner.rolsuper
          AND bootstrap_configuration.relowner = admin_schema.nspowner
          AND installer.proowner = admin_schema.nspowner
          AND finalizer.proowner = admin_schema.nspowner
          AND rollback_interface.proowner = admin_schema.nspowner
          AND installer.prosecdef
          AND finalizer.prosecdef
          AND rollback_interface.prosecdef
          AND installer.proconfig = ARRAY['search_path=pg_catalog, pg_temp']::text[]
          AND finalizer.proconfig = ARRAY['search_path=pg_catalog, pg_temp']::text[]
          AND rollback_interface.proconfig = ARRAY['search_path=pg_catalog, pg_temp']::text[]
          AND (
              (
                  has_schema_privilege(installer_operator.oid, admin_schema.oid, 'USAGE')
                  AND has_function_privilege(installer_operator.oid, installer.oid, 'EXECUTE')
                  AND NOT has_function_privilege(installer_operator.oid, finalizer.oid, 'EXECUTE')
                  AND NOT has_function_privilege(
                      installer_operator.oid, rollback_interface.oid, 'EXECUTE'
                  )
              )
              -- The empty-boundary rollback/reapply smoke uses the managed
              -- bootstrap superuser; it remains a catalog-proven installer,
              -- never a normal migration-login fallback.
              OR installer_operator.rolsuper
          )
          AND NOT EXISTS (
              SELECT 1
              FROM aclexplode(
                  COALESCE(admin_schema.nspacl, acldefault('n', admin_schema.nspowner))
              ) AS acl
              WHERE acl.grantee = 0
          )
          AND NOT EXISTS (
              SELECT 1
              FROM pg_proc AS admin_function
              CROSS JOIN LATERAL aclexplode(
                  COALESCE(admin_function.proacl, acldefault('f', admin_function.proowner))
              ) AS acl
              WHERE admin_function.oid IN (installer.oid, finalizer.oid, rollback_interface.oid)
                AND acl.grantee = 0
                AND acl.privilege_type = 'EXECUTE'
          )
    )
"""

_TRUSTED_BOOTSTRAP_ROLLBACK_SQL = """
    SELECT EXISTS (
        SELECT 1
        FROM pg_roles AS rollback_operator
        JOIN pg_namespace AS admin_schema
          ON admin_schema.nspname = 'runtime_attention_admin'
        JOIN pg_roles AS bootstrap_owner
          ON bootstrap_owner.oid = admin_schema.nspowner
        JOIN pg_class AS bootstrap_configuration_relation
          ON bootstrap_configuration_relation.relnamespace = admin_schema.oid
         AND bootstrap_configuration_relation.relname = 'bootstrap_configuration'
         AND bootstrap_configuration_relation.relkind = 'r'
        JOIN runtime_attention_admin.bootstrap_configuration AS bootstrap_configuration
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
              FROM aclexplode(
                  COALESCE(admin_schema.nspacl, acldefault('n', admin_schema.nspowner))
              ) AS acl
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


# The second core target must not call the bootstrap rollback a second time:
# the first target has already removed the shared objects.  This deliberately
# checks every core_198-owned public relation and expected function signature,
# including the two dispatch-attempt indexes.  A partial teardown is neither a
# normal no-op nor a state we are willing to stamp as downgraded.
_EXACT_ROLLBACK_READY_ABSENCE_SQL = """
    SELECT
        NOT EXISTS (
            SELECT 1
            FROM pg_class AS relation
            JOIN pg_namespace AS relation_schema
              ON relation_schema.oid = relation.relnamespace
            WHERE relation_schema.nspname = 'public'
              AND relation.relname = ANY (ARRAY[
                  'runtime_attention_outbox',
                  'runtime_attention_delivery_lease',
                  'idx_model_dispatch_attempts_catalog_ts_id',
                  'idx_model_dispatch_attempts_outcome_ts_id'
              ]::name[])
        )
        AND NOT EXISTS (
            SELECT 1
            FROM pg_proc AS interface_function
            JOIN pg_namespace AS function_schema
              ON function_schema.oid = interface_function.pronamespace
            WHERE function_schema.nspname = 'public'
              AND (
                  (
                      interface_function.proname = 'append_runtime_attention_model_breaker'
                      AND interface_function.proargtypes = '20'::oidvector
                      AND interface_function.prorettype = 'uuid'::regtype
                  )
                  OR (
                      interface_function.proname = 'append_runtime_attention_fleet_halt'
                      AND interface_function.pronargs = 0
                      AND interface_function.prorettype = 'uuid'::regtype
                  )
                  OR (
                      interface_function.proname = 'runtime_attention_active_switchboard_role'
                      AND interface_function.pronargs = 0
                      AND interface_function.prorettype = 'boolean'::regtype
                  )
                  OR (
                      interface_function.proname = 'runtime_attention_outbox_guard'
                      AND interface_function.pronargs = 0
                      AND interface_function.prorettype = 'trigger'::regtype
                  )
                  OR (
                      interface_function.proname = 'runtime_attention_delivery_lease_guard'
                      AND interface_function.pronargs = 0
                      AND interface_function.prorettype = 'trigger'::regtype
                  )
              )
        )
"""


def upgrade() -> None:
    """Install once, then verify/no-op for subsequent target-schema runs."""
    bind = op.get_bind()
    bind.execute(sa.text(_INSTALLER_SERIALIZATION_LOCK_SQL))
    if _has_trusted_finalized_interface(bind):
        return

    if not bool(bind.execute(sa.text(_TRUSTED_BOOTSTRAP_INSTALLER_SQL)).scalar_one()):
        op.execute(
            """
            DO $$
            BEGIN
                RAISE EXCEPTION
                    'runtime-attention bootstrap installer is missing or untrusted; '
                    'run scripts/init-db.sql as the privileged bootstrap first';
            END;
            $$;
            """
        )

    op.execute(f"SELECT {_ADMIN_INSTALLER}()")
    if not _has_trusted_finalized_interface(bind):
        op.execute(
            """
            DO $$
            BEGIN
                RAISE EXCEPTION
                    'runtime-attention installer completed without exact finalized catalog proof';
            END;
            $$;
            """
        )


def downgrade() -> None:
    """Permit only an empty, consumer-disabled rollback under bootstrap authority."""
    bind = op.get_bind()
    bind.execute(sa.text(_INSTALLER_SERIALIZATION_LOCK_SQL))
    if bool(bind.execute(sa.text(_EXACT_ROLLBACK_READY_ABSENCE_SQL)).scalar_one()):
        return

    if not (
        bool(bind.execute(sa.text(_TRUSTED_BOOTSTRAP_ROLLBACK_SQL)).scalar_one())
        and _has_trusted_finalized_interface(bind)
    ):
        op.execute(
            """
            DO $$
            BEGIN
                RAISE EXCEPTION
                    'core_198 downgrade requires trusted bootstrap rollback interface';
            END;
            $$;
            """
        )
    op.execute(f"SELECT {_ADMIN_ROLLBACK}()")
    if not bool(bind.execute(sa.text(_EXACT_ROLLBACK_READY_ABSENCE_SQL)).scalar_one()):
        op.execute(
            """
            DO $$
            BEGIN
                RAISE EXCEPTION
                    'core_198 rollback completed without exact shared-boundary absence proof';
            END;
            $$;
            """
        )
