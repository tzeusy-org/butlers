"""Install the dark, role-isolated canonical perception representation.

Revision ID: core_276
Revises: core_275
Create Date: 2026-10-24

All definitions and privileged ownership live in the fixed init-db installer.
The ordinary core migration can invoke that installer, never create/adopt a
writer or grant itself service membership. Shared objects survive an empty
logical downgrade; nonempty history refuses downgrade instead of losing truth.
"""

from __future__ import annotations

from alembic import op

revision = "core_276"
down_revision = "core_275"
branch_labels = None
depends_on = None

_TRUSTED_FINALIZED_INTERFACE_SQL = """
SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='perception_owner'
    AND NOT rolcanlogin AND NOT rolinherit AND NOT rolsuper AND NOT rolbypassrls
    AND NOT rolcreaterole AND NOT rolcreatedb AND NOT rolreplication)
AND NOT EXISTS (SELECT 1 FROM pg_roles r WHERE r.rolname IN
    ('perception_owner_reader','perception_availability_reader','perception_dots_writer',
     'perception_context_writer','perception_health_writer','perception_expect_writer',
     'perception_sensing_writer','perception_attention_writer') AND
    (r.rolinherit OR r.rolsuper OR r.rolbypassrls OR r.rolcreatedb
     OR r.rolcreaterole OR r.rolreplication OR pg_has_role(current_user,r.oid,'MEMBER')
     OR EXISTS(SELECT 1 FROM pg_auth_members m WHERE m.roleid=r.oid OR m.member=r.oid)))
AND NOT EXISTS (SELECT 1 FROM pg_auth_members
    WHERE roleid='perception_owner'::regrole OR member='perception_owner'::regrole)
AND NOT EXISTS (
    SELECT 1 FROM (VALUES ('public','situation_slots'),('public','timeline'),
        ('public','owner_modes'),('public','perception_conditions'),
        ('public','perception_suppressions'),('perception_private','kinds'),
        ('perception_private','sources'),
        ('perception_private','source_refs'),('perception_private','suppression_refs'),
        ('perception_private','receipts')) required(ns,tbl)
    LEFT JOIN pg_namespace n ON n.nspname=required.ns
    LEFT JOIN pg_class c ON c.relnamespace=n.oid AND c.relname=required.tbl
    WHERE c.oid IS NULL OR c.relowner<>'perception_owner'::regrole
        OR NOT c.relrowsecurity OR NOT c.relforcerowsecurity
        OR EXISTS (SELECT 1 FROM aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) a
            WHERE a.grantee<>c.relowner AND a.privilege_type IN
                ('SELECT','INSERT','UPDATE','DELETE','TRUNCATE','TRIGGER','REFERENCES')))
AND NOT EXISTS (
    SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE (n.nspname='perception_private' OR n.nspname='public'
        AND p.proname IN ('perception_write','perception_read',
            'perception_timeline','perception_bind','perception_registry'))
    AND (p.proowner<>'perception_owner'::regrole
        OR p.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog, pg_temp']::text[]
        OR EXISTS (SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
            WHERE a.grantee=0)))
AND EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='public' AND p.proname='perception_write' AND p.prosecdef)
"""


def upgrade() -> None:
    op.execute("SELECT perception_admin.install_interface()")
    # Bootstrap's fixed superuser-owned interface is the only install path.
    if not op.get_bind().exec_driver_sql(_TRUSTED_FINALIZED_INTERFACE_SQL).scalar():
        raise RuntimeError("perception finalized catalog refused")


def downgrade() -> None:
    # The fixed helper takes the same finite table lock as its history check;
    # an ordinary migration cannot inspect or truncate private history itself.
    op.execute("SELECT perception_admin.rollback_interface()")
