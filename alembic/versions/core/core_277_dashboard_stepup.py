"""Enlist D13 rule confirmation in its owning domain transaction.

Revision ID: core_277
Revises: core_275 (core_276 remains a separately held perception slot).

Only the existing privileged bootstrap stages the fixed installer. Ordinary
migrations never create roles, own proof storage, or accept arbitrary DDL.
"""

from sqlalchemy import text

from alembic import op

revision = "core_277"
down_revision = "core_275"
branch_labels = None
depends_on = None

_COLUMNS = (
    "id",
    "session_digest",
    "credential_epoch",
    "session_epoch",
    "kind",
    "target",
    "request_digest",
    "action_digest",
    "namespace",
    "expires_at",
    "nonce_digest",
    "revoked",
    "consumed_at",
    "consumed_tx",
    "result",
    "created_at",
)

_BOOTSTRAP = """
SELECT EXISTS(SELECT FROM pg_namespace n JOIN pg_roles r ON r.oid=n.nspowner
 WHERE n.nspname='dashboard_stepup' AND r.rolsuper
 AND obj_description(n.oid,'pg_namespace')='butlers:dashboard-stepup:core_277'
 AND NOT EXISTS(SELECT FROM aclexplode(COALESCE(n.nspacl,acldefault('n',n.nspowner))) a
  WHERE a.grantee=0 AND a.privilege_type='CREATE')
 AND EXISTS(SELECT FROM pg_class c WHERE c.relnamespace=n.oid
  AND c.relname='bootstrap_configuration' AND c.relkind='r' AND c.relowner=n.nspowner)
 AND 3=(SELECT count(*) FROM pg_proc p WHERE p.pronamespace=n.oid
  AND p.proname IN('install_interface','finalize_interface','deactivate_interface')
  AND p.pronargs=0 AND p.prorettype='void'::regtype AND p.prosecdef AND p.proowner=n.nspowner
  AND p.proconfig=ARRAY['search_path=pg_catalog, pg_temp']::text[]))
"""

_FINAL = """
SELECT EXISTS(SELECT FROM pg_roles owner JOIN pg_namespace n ON n.nspname='dashboard_stepup'
 JOIN pg_class c ON c.relnamespace=n.oid AND c.relname='bindings' AND c.relkind='r'
 WHERE owner.rolname='dashboard_stepup_owner' AND c.relowner=owner.oid
 AND NOT(owner.rolcanlogin OR owner.rolsuper OR owner.rolcreatedb OR owner.rolcreaterole
  OR owner.rolreplication OR owner.rolbypassrls)
 AND NOT EXISTS(SELECT FROM pg_auth_members WHERE roleid=owner.oid OR member=owner.oid)
 AND NOT has_schema_privilege(owner.oid,n.oid,'CREATE')
 AND NOT EXISTS(SELECT FROM aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) a
  WHERE a.grantee<>owner.oid AND a.privilege_type<>'SELECT')
 AND NOT EXISTS(SELECT FROM aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) a
  WHERE a.grantee=0)
 AND 2=(SELECT count(*) FROM pg_proc p WHERE p.pronamespace=n.oid AND p.proowner=owner.oid
  AND p.prosecdef AND p.proconfig=ARRAY['search_path=pg_catalog, pg_temp']::text[]
  AND ((p.proname='ceremony' AND p.proargtypes='25 3802'::oidvector)
   OR (p.proname='consume' AND p.proargtypes='25 25 25 25 25 25 25 3802'::oidvector)))
 AND NOT EXISTS(SELECT FROM pg_proc p
  CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
  WHERE p.pronamespace=n.oid AND p.proname IN('ceremony','consume') AND a.grantee=0))
"""


def _refuse():
    raise RuntimeError("Dashboard step-up requires intact trusted bootstrap/interface")


def _prepare():
    bind = op.get_bind()
    bind.execute(text("SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_277',0))"))
    if bind.execute(text(_BOOTSTRAP)).scalar() is not True:
        _refuse()
    return bind


def upgrade():
    bind = _prepare()
    exists = bind.execute(
        text("SELECT to_regclass('dashboard_stepup.bindings') IS NOT NULL")
    ).scalar()
    if exists:
        if bind.execute(text(_FINAL)).scalar() is not True:
            _refuse()
        columns = tuple(
            bind.execute(
                text("""
            SELECT attname FROM pg_attribute WHERE attrelid='dashboard_stepup.bindings'::regclass
              AND attnum>0 AND NOT attisdropped ORDER BY attnum
        """)
            ).scalars()
        )
        if columns != _COLUMNS:
            _refuse()
        bind.execute(text("SELECT dashboard_stepup.finalize_interface()"))
    else:
        bind.execute(text("SELECT dashboard_stepup.install_interface()"))
    if bind.execute(text(_FINAL)).scalar() is not True:
        _refuse()


def downgrade():
    bind = _prepare()
    if bind.execute(text(_FINAL)).scalar() is not True:
        _refuse()
    # A schema-scoped downgrade cannot erase global authorization history.
    # Disable consumer execution, revoke unused proofs and retain all receipts.
    bind.execute(text("SELECT dashboard_stepup.deactivate_interface()"))
