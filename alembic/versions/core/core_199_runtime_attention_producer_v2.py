"""Activate the versioned runtime-attention producer interface.

Revision ID: core_199
Revises: core_198

The privileged bootstrap owns the interface evolution.  This migration may
only invoke the one-shot, narrowly granted v2 upgrader, then proves the public
catalog shape after that upgrader has revoked its own migration-role access.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "core_199"
down_revision = "core_198"
branch_labels = None
depends_on = None

_SERIALIZATION_LOCK_SQL = """
    SELECT pg_advisory_xact_lock(
        hashtextextended('butlers:core_199:runtime_attention_producer_v2', 0)
    )
"""

_V2_CATALOG_SQL = """
    SELECT EXISTS (
        SELECT 1
        FROM pg_roles AS outbox_owner
        JOIN pg_namespace AS public_schema ON public_schema.nspname = 'public'
        JOIN pg_namespace AS admin_schema
          ON admin_schema.nspname = 'runtime_attention_admin'
        JOIN pg_roles AS bootstrap_owner ON bootstrap_owner.oid = admin_schema.nspowner
        JOIN pg_class AS configuration
          ON configuration.relnamespace = admin_schema.oid
         AND configuration.relname = 'bootstrap_configuration'
         AND configuration.relkind = 'r'
        JOIN pg_class AS producer_control
          ON producer_control.relnamespace = public_schema.oid
         AND producer_control.relname = 'runtime_attention_producer_control'
         AND producer_control.relkind = 'r'
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
        JOIN pg_proc AS debounce_marker
          ON debounce_marker.pronamespace = public_schema.oid
         AND debounce_marker.proname = 'runtime_attention_plant_legacy_debounce_marker'
         AND debounce_marker.pronargs = 0
         AND debounce_marker.prorettype = 'trigger'::regtype
        WHERE outbox_owner.rolname = 'runtime_attention_outbox_owner'
          AND NOT outbox_owner.rolcanlogin
          AND NOT outbox_owner.rolsuper
          AND NOT outbox_owner.rolinherit
          AND bootstrap_owner.rolsuper
          AND configuration.relowner = bootstrap_owner.oid
          AND producer_control.relowner = outbox_owner.oid
          AND model_breaker.proowner = outbox_owner.oid
          AND fleet_halt.proowner = outbox_owner.oid
          AND debounce_marker.proowner = outbox_owner.oid
          AND model_breaker.prosecdef
          AND fleet_halt.prosecdef
          AND debounce_marker.prosecdef
          AND model_breaker.proconfig = ARRAY[
              'search_path=pg_catalog, public, pg_temp'
          ]::text[]
          AND fleet_halt.proconfig = ARRAY[
              'search_path=pg_catalog, public, pg_temp'
          ]::text[]
          AND debounce_marker.proconfig = ARRAY[
              'search_path=pg_catalog, public, pg_temp'
          ]::text[]
          AND EXISTS (
              SELECT 1
              FROM pg_trigger AS marker_trigger
              WHERE marker_trigger.tgrelid =
                    'public.model_dispatch_attempts'::regclass
                AND marker_trigger.tgname =
                    'runtime_attention_plant_legacy_debounce_marker_trigger'
                AND marker_trigger.tgfoid = debounce_marker.oid
                AND NOT marker_trigger.tgisinternal
                AND (marker_trigger.tgtype & 2) = 2
                AND (marker_trigger.tgtype & 4) = 4
          )
          AND (
              SELECT count(*)
              FROM pg_attribute AS attribute
              WHERE attribute.attrelid = configuration.oid
                AND attribute.attname = ANY (ARRAY[
                    'interface_version', 'producers_enabled', 'producer_activated_at'
                ]::name[])
                AND NOT attribute.attisdropped
          ) = 3
          AND (
              SELECT count(*)
              FROM pg_attribute AS attribute
              WHERE attribute.attrelid = producer_control.oid
                AND attribute.attname = ANY (ARRAY[
                    'singleton', 'interface_version', 'producers_enabled',
                    'producer_activated_at'
                ]::name[])
                AND NOT attribute.attisdropped
          ) = 4
    )
"""

_TRUSTED_UPGRADER_SQL = """
    SELECT EXISTS (
        SELECT 1
        FROM pg_roles AS migration_role
        JOIN pg_namespace AS admin_schema
          ON admin_schema.nspname = 'runtime_attention_admin'
        JOIN pg_roles AS bootstrap_owner ON bootstrap_owner.oid = admin_schema.nspowner
        JOIN pg_proc AS upgrader
          ON upgrader.pronamespace = admin_schema.oid
         AND upgrader.proname = 'upgrade_producers_v2'
         AND upgrader.pronargs = 0
         AND upgrader.prorettype = 'void'::regtype
        WHERE migration_role.rolname = current_user
          AND bootstrap_owner.rolsuper
          AND upgrader.proowner = bootstrap_owner.oid
          AND upgrader.prosecdef
          AND upgrader.proconfig = ARRAY['search_path=pg_catalog, pg_temp']::text[]
          AND (
              (
                  NOT migration_role.rolsuper
                  AND has_schema_privilege(migration_role.oid, admin_schema.oid, 'USAGE')
                  AND has_function_privilege(migration_role.oid, upgrader.oid, 'EXECUTE')
              )
              OR migration_role.oid = bootstrap_owner.oid
          )
          AND NOT EXISTS (
              SELECT 1
              FROM aclexplode(COALESCE(upgrader.proacl, acldefault('f', upgrader.proowner))) acl
              LEFT JOIN pg_roles AS granted_role ON granted_role.oid = acl.grantee
              WHERE acl.privilege_type = 'EXECUTE'
                AND acl.grantee <> bootstrap_owner.oid
                AND NOT (
                    NOT COALESCE(granted_role.rolsuper, true)
                    AND has_schema_privilege(
                        granted_role.oid, admin_schema.oid, 'USAGE'
                    )
                )
          )
    )
"""

_TRUSTED_DEACTIVATOR_SQL = """
    SELECT EXISTS (
        SELECT 1
        FROM pg_roles AS rollback_operator
        JOIN pg_namespace AS admin_schema
          ON admin_schema.nspname = 'runtime_attention_admin'
        JOIN pg_roles AS bootstrap_owner ON bootstrap_owner.oid = admin_schema.nspowner
        JOIN pg_proc AS deactivator
          ON deactivator.pronamespace = admin_schema.oid
         AND deactivator.proname = 'deactivate_producers_v2'
         AND deactivator.pronargs = 0
         AND deactivator.prorettype = 'void'::regtype
        WHERE rollback_operator.rolname = current_user
          AND rollback_operator.rolsuper
          AND bootstrap_owner.rolsuper
          AND deactivator.proowner = bootstrap_owner.oid
          AND deactivator.prosecdef
          AND deactivator.proconfig = ARRAY['search_path=pg_catalog, pg_temp']::text[]
    )
"""


def _is_v2(bind: sa.Connection) -> bool:
    return bool(bind.execute(sa.text(_V2_CATALOG_SQL)).scalar_one())


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text(_SERIALIZATION_LOCK_SQL))
    trusted_upgrader = bool(bind.execute(sa.text(_TRUSTED_UPGRADER_SQL)).scalar_one())
    if _is_v2(bind) and not trusted_upgrader:
        return
    if not trusted_upgrader:
        raise RuntimeError(
            "runtime-attention v2 bootstrap upgrader is missing or untrusted; "
            "run scripts/init-db.sql as the privileged bootstrap first"
        )
    bind.execute(sa.text("SELECT runtime_attention_admin.upgrade_producers_v2()"))
    if not _is_v2(bind):
        raise RuntimeError("runtime-attention v2 upgrade lacks exact finalized catalog proof")


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text(_SERIALIZATION_LOCK_SQL))
    if not bool(bind.execute(sa.text(_TRUSTED_DEACTIVATOR_SQL)).scalar_one()):
        raise RuntimeError(
            "core_199 downgrade requires the managed privileged bootstrap deactivator"
        )
    bind.execute(sa.text("SELECT runtime_attention_admin.deactivate_producers_v2()"))
