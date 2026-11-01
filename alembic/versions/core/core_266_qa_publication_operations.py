"""Create the QA publication operation ledger (bu-mc01y5.5).

Revision ID: core_266
Revises: core_265
Create Date: 2026-10-18 00:00:00.000000

REQ-qa-publication-authority-002 / -006 (change confine-qa-publication-authority).
Publication stays disabled; this revision adds only the durable state the
publisher will use.  One row binds one closed publication operation to its
healing attempt and is written *before* any egress.  A row left in
``egress_started`` or ``ambiguous`` holds the attempt's fingerprint lineage
until an exact read-only reconciliation is recorded, so no watchdog, cooldown,
follow-up or restart can open a fresh attempt, branch or PR.

Receipts carry only categories, digests and the bound object identifier, never
credentials, provider bodies or private identifiers.

Row security is the durable fence.  ``scripts/init-db.sql`` re-grants
public-schema DML to every runtime role on each run and a policy is not a
grant, so a re-run cannot widen what the policies refuse.  The QA role is the
only writer; every role may read (the hold must be visible to any dispatcher).
FORCE applies the policies to the table owner as well.  The bootstrap re-grants
DELETE, so rows cannot be deleted only because no DELETE policy exists.  TRUNCATE is
not a row operation and no policy applies to it: the owner/runtime login keeps it (and
ALTER/DROP POLICY); no runtime path truncates.  That right cannot be revoked and is
deliberately not trigger-guarded here, because existing fixtures clear
``healing_attempts`` with ``TRUNCATE ... CASCADE``.  The table is therefore
unreadable to ``deploy/backup/pg_dump.sh``'s dump role and is on its
exclusion list; the restore fence that re-establishes holds belongs to
bu-mc01y5.7.
"""

from __future__ import annotations

from alembic import op

revision = "core_266"
down_revision = "core_265"
branch_labels = None
depends_on = None

_TABLE = "public.qa_publication_operations"
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

_HOLD_STAGES = "('egress_started', 'ambiguous')"

_ROLLBACK_REFUSAL = (
    "core_266 cannot drop public.qa_publication_operations while an unresolved "
    "publication hold exists (stage egress_started or ambiguous). Record the exact "
    "read-only reconciliation for each held operation first; retained rows are never "
    "deleted automatically."
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


def _core_266_installation_count() -> int:
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
                    THEN substring(version_num FROM 6)::integer >= 266
                    ELSE false END
            )
            """
        ).scalar()
        count += int(bool(installed))
    return count


def upgrade() -> None:
    op.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_266:qa-publication', 0))"
    )
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_TABLE} (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            attempt_id          UUID NOT NULL
                REFERENCES public.healing_attempts(id) ON DELETE RESTRICT,
            kind                TEXT NOT NULL,
            stage               TEXT NOT NULL,
            stage_order         SMALLINT NOT NULL,
            binding_digest      TEXT NOT NULL,
            request_digest      TEXT NOT NULL,
            expected_object_id  TEXT,
            new_object_id       TEXT,
            result              TEXT,
            reconciliation      TEXT,
            reconciled_object_id TEXT,
            started_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            ended_at            TIMESTAMPTZ,
            reconciled_at       TIMESTAMPTZ,
            CONSTRAINT uq_qa_publication_operations_attempt_kind UNIQUE (attempt_id, kind),
            CONSTRAINT ck_qa_publication_operations_kind CHECK (
                kind IN ('branch_push', 'pr_create', 'pr_update', 'pr_labels')
            ),
            CONSTRAINT ck_qa_publication_operations_stage CHECK (
                (stage = 'bound' AND stage_order = 1)
                OR (stage = 'egress_started' AND stage_order = 2)
                OR (stage IN ('succeeded', 'failed', 'ambiguous') AND stage_order = 3)
                OR (stage = 'reconciled' AND stage_order = 4)
            ),
            CONSTRAINT ck_qa_publication_operations_result CHECK (
                result IS NULL OR result IN ('success', 'failed', 'ambiguous')
            ),
            CONSTRAINT ck_qa_publication_operations_reconciliation CHECK (
                reconciliation IS NULL
                OR reconciliation IN ('resource_found', 'resource_absent')
            ),
            CONSTRAINT ck_qa_publication_operations_digests CHECK (
                binding_digest ~ '^[0-9a-f]{{64}}$' AND request_digest ~ '^[0-9a-f]{{64}}$'
            ),
            CONSTRAINT ck_qa_publication_operations_reconciled CHECK (
                (stage = 'reconciled') = (reconciled_at IS NOT NULL AND reconciliation IS NOT NULL)
            )
        )
        """
    )
    op.execute(
        f"""
        CREATE INDEX IF NOT EXISTS ix_qa_publication_operations_hold
            ON {_TABLE} (attempt_id) WHERE stage IN {_HOLD_STAGES}
        """
    )

    op.execute(f"REVOKE ALL PRIVILEGES ON TABLE {_TABLE} FROM PUBLIC")
    for role in _OTHER_RUNTIME_ROLES:
        _for_existing_role(role, f"REVOKE ALL PRIVILEGES ON TABLE {_TABLE} FROM {role}")
        _for_existing_role(role, f"GRANT SELECT ON TABLE {_TABLE} TO {role}")
    _for_existing_role(_QA_ROLE, f"REVOKE ALL PRIVILEGES ON TABLE {_TABLE} FROM {_QA_ROLE}")
    _for_existing_role(_QA_ROLE, f"GRANT SELECT, INSERT, UPDATE ON TABLE {_TABLE} TO {_QA_ROLE}")

    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY")
    for policy in ("read", "insert", "update"):
        op.execute(f"DROP POLICY IF EXISTS qa_publication_operations_{policy} ON {_TABLE}")
    op.execute(f"CREATE POLICY qa_publication_operations_read ON {_TABLE} FOR SELECT USING (true)")
    op.execute(
        f"""
        CREATE POLICY qa_publication_operations_insert ON {_TABLE}
            FOR INSERT WITH CHECK (current_user = '{_QA_ROLE}')
        """
    )
    op.execute(
        f"""
        CREATE POLICY qa_publication_operations_update ON {_TABLE}
            FOR UPDATE USING (current_user = '{_QA_ROLE}')
            WITH CHECK (current_user = '{_QA_ROLE}')
        """
    )


def downgrade() -> None:
    op.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_266:qa-publication', 0))"
    )
    # The core chain runs once per butler schema over one shared table. Only the last
    # schema to leave core_266 may hold-check and drop it; earlier ones still run head code.
    if _core_266_installation_count() > 1:
        return
    op.execute(
        f"""
        DO $$
        BEGIN
            -- Nested so the table reference is only planned once the table is known to exist
            -- (a single AND would be planned, and fail, on a partial-chain replay).
            IF to_regclass('{_TABLE}') IS NOT NULL THEN
                IF EXISTS (SELECT 1 FROM {_TABLE} WHERE stage IN {_HOLD_STAGES}) THEN
                    RAISE EXCEPTION '{_ROLLBACK_REFUSAL}';
                END IF;
            END IF;
        END
        $$
        """
    )
    op.execute(f"DROP TABLE IF EXISTS {_TABLE}")
