"""Install database-global owner authentication once across schema-scoped chains.

Revision ID: core_239
Revises: core_238
"""

from pathlib import Path

from sqlalchemy import text

from alembic import op

revision = "core_239"
down_revision = "core_238"
branch_labels = None
depends_on = None

_MARKER = "butlers:dashboard-owner-auth:core_239"
_COLUMNS = {
    "instance": (
        "singleton instance_id origin rp_id key_generation state credential_epoch session_epoch"
    ),
    "contexts": "digest csrf_digest expires_at credential_epoch revoked finish_minute finish_count",
    "intents": (
        "id context_digest operation credential_epoch expires_at authorized consumed created_at"
    ),
    "ceremonies": (
        "id context_digest intent_id operation credential_epoch session_epoch challenge "
        "user_handle expires_at consumed created_at"
    ),
    "credentials": (
        "credential_epoch credential_id credential_data user_handle backup_eligible "
        "backup_state counter active retired_at"
    ),
    "sessions": "digest credential_epoch session_epoch expires_at revoked",
    "csrf": "session_digest digest expires_at created_at",
    "rate_buckets": "kind minute count",
    "audit": "ts action outcome actor",
}


def _refuse() -> None:
    raise RuntimeError("Owner authentication migration requires intact, owned authentication state")


def _lock(bind) -> None:
    # Multiple butler schemas can advance their own core chain concurrently.
    bind.execute(text("SELECT pg_advisory_xact_lock(hashtext('butlers.dashboard_auth.core_239'))"))


def _schema_exists(bind) -> bool:
    return bool(bind.execute(text("SELECT to_regnamespace('dashboard_auth') IS NOT NULL")).scalar())


def _prior_installation_recorded(bind) -> bool:
    schemas = bind.execute(
        text("""
        SELECT n.nspname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE c.relname='alembic_version' AND c.relkind IN ('r','p')
          AND n.nspname NOT LIKE 'pg_%' AND n.nspname<>'information_schema'
    """)
    ).scalars()
    quote = bind.dialect.identifier_preparer.quote
    for schema in schemas:
        # Version metadata survives a damaged/deleted auth namespace. It must
        # never become an invitation to initialize a replacement owner identity.
        recorded = bind.execute(
            text(f"""
            SELECT EXISTS (SELECT FROM {quote(schema)}.alembic_version
                WHERE CASE WHEN version_num ~ '^core_[0-9]+$'
                    THEN substring(version_num FROM 6)::integer >= 239 ELSE false END)
        """)
        ).scalar()
        if recorded:
            return True
    return False


def _validate_existing(bind) -> dict[str, set[str]]:
    owned = bind.execute(
        text("""
        SELECT obj_description(n.oid,'pg_namespace')=:marker
           AND (n.nspowner=r.oid OR r.rolsuper)
        FROM pg_namespace n JOIN pg_roles r ON r.rolname=current_user
        WHERE n.nspname='dashboard_auth'
    """),
        {"marker": _MARKER},
    ).scalar()
    if owned is not True:
        _refuse()
    columns: dict[str, set[str]] = {}
    for table, column in bind.execute(
        text("""
        SELECT c.relname,a.attname FROM pg_class c
        JOIN pg_namespace n ON n.oid=c.relnamespace
        JOIN pg_attribute a ON a.attrelid=c.oid
        WHERE n.nspname='dashboard_auth' AND c.relkind IN ('r','p')
          AND a.attnum>0 AND NOT a.attisdropped
    """)
    ):
        columns.setdefault(table, set()).add(column)
    if any(
        not set(names.split()) <= columns.get(table, set()) for table, names in _COLUMNS.items()
    ):
        _refuse()
    functions = bind.execute(
        text("""
        SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='dashboard_auth' AND p.prosecdef AND p.proowner=n.nspowner
          AND ((p.proname IN ('api','host') AND p.proargtypes='25 3802'::oidvector)
            OR (p.proname='cleanup' AND p.pronargs=0))
    """)
    ).scalar()
    indexes = bind.execute(
        text("""
        SELECT count(*) FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='dashboard_auth' AND i.indisvalid AND i.indisunique
          AND c.relname IN ('one_active_credential','one_authorized_intent')
    """)
    ).scalar()
    if functions != 3 or indexes != 2:
        _refuse()
    # Serialize with API and host operations before checking state across tables.
    bind.execute(text("SELECT singleton FROM dashboard_auth.instance FOR UPDATE"))
    intact = bind.execute(
        text("""
        SELECT count(*)=1 AND bool_and(singleton IS TRUE AND instance_id IS NOT NULL
          AND credential_epoch>=1 AND session_epoch>=1
          AND state IN ('keyless_unenrolled','keyless_enrolled','configured_key','recovery_pending')
          AND (state='configured_key')=(key_generation IS NOT NULL)
          AND (origin IS NULL)=(rp_id IS NULL)
          AND (origin IS NULL OR origin='https://' || rp_id))
        FROM dashboard_auth.instance
    """)
    ).scalar()
    credential_intact = bind.execute(
        text("""
        SELECT CASE WHEN s.state='keyless_enrolled' THEN
          (SELECT count(*)=1 AND bool_and(credential_epoch=s.credential_epoch
             AND credential_id IS NOT NULL AND credential_data IS NOT NULL
             AND user_handle IS NOT NULL) FROM dashboard_auth.credentials WHERE active)
          ELSE NOT EXISTS(SELECT FROM dashboard_auth.credentials WHERE active) END
        FROM dashboard_auth.instance s
    """)
    ).scalar()
    if intact is not True or credential_intact is not True:
        _refuse()
    return columns


def upgrade() -> None:
    bind = op.get_bind()
    _lock(bind)
    if _schema_exists(bind):
        _validate_existing(bind)
        return
    if _prior_installation_recorded(bind):
        _refuse()
    # JSON colons are not bind parameters; psycopg requires literal percent
    # signs escaped when SQLAlchemy passes its empty DBAPI parameter mapping.
    sql = Path(__file__).with_suffix(".sql").read_text()
    bind.exec_driver_sql(sql.replace("%", "%%"))


def downgrade() -> None:
    bind = op.get_bind()
    _lock(bind)
    if not _schema_exists(bind):
        _refuse()
    columns = _validate_existing(bind)
    if columns != {table: set(names.split()) for table, names in _COLUMNS.items()}:
        _refuse()
    pristine = bind.execute(
        text("""
        SELECT origin IS NULL AND rp_id IS NULL AND key_generation IS NULL
          AND state='keyless_unenrolled' AND credential_epoch=1 AND session_epoch=1
        FROM dashboard_auth.instance
    """)
    ).scalar()
    if pristine is not True or any(
        bind.execute(text(f"SELECT EXISTS(SELECT FROM dashboard_auth.{table})")).scalar()
        for table in _COLUMNS
        if table != "instance"
    ):
        raise RuntimeError(
            "Owner auth downgrade requires a separately reviewed host revocation plan"
        )
    # Deliberate no-op: this database-global, never-initialized namespace may
    # still belong to another schema's core chain. Preserve its identity marker
    # and dependencies so later upgrades cannot mistake removal for first setup.
