"""Purpose-separated ordinary owner creation and shared clause store.

Only privileged bootstrap stages the fixed installer; no ordinary migration
can adopt arbitrary functions, create roles, or grant authentication reads.
"""

from sqlalchemy import text

from alembic import op

revision = "core_278"
down_revision = "core_277"
branch_labels = None
depends_on = None

_BOOTSTRAP = """
SELECT EXISTS(SELECT FROM pg_namespace n JOIN pg_roles r ON r.oid=n.nspowner
 WHERE n.nspname='dashboard_attention' AND r.rolsuper
 AND obj_description(n.oid,'pg_namespace')='butlers:dashboard-attention:core_278'
 AND EXISTS(SELECT FROM pg_class c WHERE c.relnamespace=n.oid
  AND c.relname='bootstrap_configuration' AND c.relkind='r' AND c.relowner=n.nspowner)
 AND 3=(SELECT count(*) FROM pg_proc p WHERE p.pronamespace=n.oid
  AND p.proname IN('install_interface','finalize_interface','deactivate_interface')
  AND p.pronargs=0 AND p.prorettype='void'::regtype AND p.prosecdef AND p.proowner=n.nspowner
  AND p.proconfig=ARRAY['search_path=pg_catalog, pg_temp']::text[]))
"""


def _prepare():
    connection = op.get_bind()
    connection.execute(text("SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_278',0))"))
    if connection.execute(text(_BOOTSTRAP)).scalar() is not True:
        raise RuntimeError("Owner attention requires trusted bootstrap")
    return connection


def upgrade():
    connection = _prepare()
    exists = connection.execute(
        text("SELECT to_regclass('dashboard_attention.tickets') IS NOT NULL")
    ).scalar()
    if exists:
        connection.execute(text("SELECT dashboard_attention.finalize_interface()"))
    else:
        connection.execute(text("SELECT dashboard_attention.install_interface()"))


def downgrade():
    _prepare().execute(text("SELECT dashboard_attention.deactivate_interface()"))
