"""Retain sealed QA export bytes outside agent control (bu-mc01y5.19).

Revision ID: core_294
Revises: core_293
Create Date: 2026-11-02 00:00:00.000000

Spec qa-investigation-dispatch, scenario "Committed proposal cannot be published":
the dispatcher retains the sealed artifact outside agent control before worktree
teardown.  The agent worktree's branch and objects are deleted at teardown, so
``QaPublisher`` writes the exact validated export bytes here before its first egress.
One row per healing attempt; the row is immutable once written.

The fence follows core_266.  ``scripts/init-db.sql`` re-grants public-schema DML to
every runtime role on each run and a policy is not a grant, so forced row security is
the durable fence: only ``butler_qa_rw`` may insert.  Every role may read, as for the
ledger: the bytes are the export that already passed ``validate_anonymized()`` and is
exactly what a successful publication would make public.  There is no UPDATE or DELETE
policy, so no runtime role can rewrite or remove a row; a BEFORE UPDATE trigger also
refuses rewrites from a privileged login that bypasses row security.  TRUNCATE is not
guarded (fixtures clear ``healing_attempts`` with ``TRUNCATE ... CASCADE``).

Forced row security fences the dump role, so ``deploy/backup/pg_dump.sh`` excludes the
table like the ledger.

The core chain runs once per butler schema over one shared table, so only the last
schema to leave core_294 drops it, and only while no export is retained.
"""

from __future__ import annotations

from alembic import op

revision = "core_294"
down_revision = "core_293"
branch_labels = None
depends_on = None

_TABLE = "public.qa_publication_exports"
_QA_ROLE = "butler_qa_rw"
_OTHER_RUNTIME_ROLES = (
    "butler_chronicler_rw",
    "butler_concierge_rw",
    "butler_education_rw",
    "butler_finance_rw",
    "butler_general_rw",
    "butler_health_rw",
    "butler_home_rw",
    "butler_lifestyle_rw",
    "butler_messenger_rw",
    "butler_relationship_rw",
    "butler_switchboard_rw",
    "butler_travel_rw",
    "butler_calendar_rw",
    "connector_writer",
)
_GUARD = "public.qa_publication_exports_immutable"

_ROLLBACK_REFUSAL = (
    "core_294 cannot drop public.qa_publication_exports while a sealed QA export is "
    "retained. Retained exports are never deleted automatically; resolve and remove "
    "them deliberately first."
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


def _core_294_installation_count() -> int:
    """Count schema-local core chains that still depend on the shared table (core_246 pattern)."""
    bind = op.get_bind()
    schemas = bind.exec_driver_sql(
        """
        SELECT namespace.nspname
        FROM pg_class version_table
        JOIN pg_namespace namespace ON namespace.oid = version_table.relnamespace
        WHERE version_table.relname = 'alembic_version'
          AND version_table.relkind IN ('r', 'p')
          AND namespace.nspname NOT LIKE 'pg_%%'
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
                    THEN substring(version_num FROM 6)::integer >= 294
                    ELSE false END
            )
            """
        ).scalar()
        count += int(bool(installed))
    return count


def upgrade() -> None:
    op.execute("SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_294:qa-exports', 0))")
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_TABLE} (
            attempt_id   UUID PRIMARY KEY
                REFERENCES public.healing_attempts(id) ON DELETE RESTRICT,
            base_sha     TEXT NOT NULL,
            head_sha     TEXT NOT NULL,
            commits      TEXT[] NOT NULL,
            objects      BYTEA NOT NULL,
            digest       TEXT NOT NULL,
            retained_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_qa_publication_exports_shas CHECK (
                base_sha ~ '^[0-9a-f]{{40}}$' AND head_sha ~ '^[0-9a-f]{{40}}$'
                AND base_sha <> head_sha
            ),
            CONSTRAINT ck_qa_publication_exports_commits CHECK (
                cardinality(commits) BETWEEN 1 AND 50
                AND commits[cardinality(commits)] = head_sha
            ),
            CONSTRAINT ck_qa_publication_exports_digest CHECK (digest ~ '^[0-9a-f]{{64}}$'),
            CONSTRAINT ck_qa_publication_exports_size CHECK (octet_length(objects) <= 22020096)
        )
        """
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {_GUARD}() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'retained QA export is immutable'
                USING ERRCODE = 'check_violation';
        END
        $$
        """
    )
    op.execute(f"DROP TRIGGER IF EXISTS qa_publication_exports_immutable ON {_TABLE}")
    op.execute(
        f"""
        CREATE TRIGGER qa_publication_exports_immutable
            BEFORE UPDATE ON {_TABLE}
            FOR EACH ROW EXECUTE FUNCTION {_GUARD}()
        """
    )

    op.execute(f"REVOKE ALL PRIVILEGES ON TABLE {_TABLE} FROM PUBLIC")
    for role in _OTHER_RUNTIME_ROLES:
        _for_existing_role(role, f"REVOKE ALL PRIVILEGES ON TABLE {_TABLE} FROM {role}")
        _for_existing_role(role, f"GRANT SELECT ON TABLE {_TABLE} TO {role}")
    _for_existing_role(_QA_ROLE, f"REVOKE ALL PRIVILEGES ON TABLE {_TABLE} FROM {_QA_ROLE}")
    _for_existing_role(_QA_ROLE, f"GRANT SELECT, INSERT ON TABLE {_TABLE} TO {_QA_ROLE}")

    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY")
    for policy in ("read", "insert"):
        op.execute(f"DROP POLICY IF EXISTS qa_publication_exports_{policy} ON {_TABLE}")
    op.execute(f"CREATE POLICY qa_publication_exports_read ON {_TABLE} FOR SELECT USING (true)")
    op.execute(
        f"""
        CREATE POLICY qa_publication_exports_insert ON {_TABLE}
            FOR INSERT WITH CHECK (current_user = '{_QA_ROLE}')
        """
    )


def downgrade() -> None:
    op.execute("SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_294:qa-exports', 0))")
    # Earlier schemas still run head code against the shared table; only the last drops it.
    if _core_294_installation_count() > 1:
        return
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('{_TABLE}') IS NOT NULL THEN
                IF EXISTS (SELECT 1 FROM {_TABLE}) THEN
                    RAISE EXCEPTION '{_ROLLBACK_REFUSAL}';
                END IF;
            END IF;
        END
        $$
        """
    )
    op.execute(f"DROP TABLE IF EXISTS {_TABLE}")
    op.execute(f"DROP FUNCTION IF EXISTS {_GUARD}()")
