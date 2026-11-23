"""qa_known_issue_links: owner-asserted links from a finding to the work item tracking it.

Revision ID: core_372
Revises: core_369
Create Date: 2026-11-24 00:00:00.000000

bu-ejow5p.9. A finding already tracked by a bead, pull request or issue is held back by the
'tracked' triage gate instead of being investigated again: a fixed publisher would otherwise
open duplicate PRs racing a human-owned defect, and every breaker reset would be spent on known
work. One row links a fingerprint to one tracker ref; ``(fingerprint, tracker_ref)`` is unique so
a repeat link is an idempotent upsert.

``state`` is the link's lifecycle: ``tracked`` and ``fix_merged`` hold the finding back;
``awaiting_recovery`` re-admits a recurrence as a regression; ``regressed`` and ``closed`` no
longer gate. ``expires_at`` bounds a link so it cannot suppress forever.

The core chain runs once per butler schema over the one shared ``public`` table: the install
is idempotent, and only the last schema to leave this revision drops the table (core_246).
"""

from __future__ import annotations

from alembic import op
from butlers.db_authority.roles import butler_roles

revision = "core_372"
down_revision = "core_369"
branch_labels = None
depends_on = None

_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_372:qa_known_issue_links', 0))"
_TABLE = "public.qa_known_issue_links"
_PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE"
_QA_ROLE = "butler_qa_rw"

#: Catalog-only read: true when an earlier schema's run already created the table.
_ALREADY_INSTALLED = f"SELECT to_regclass('{_TABLE}') IS NOT NULL"

_DDL = f"""
CREATE TABLE IF NOT EXISTS {_TABLE} (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    fingerprint   TEXT NOT NULL CHECK (btrim(fingerprint) <> ''),
    tracker_kind  TEXT NOT NULL CHECK (tracker_kind IN ('bead', 'github_pr', 'github_issue')),
    tracker_ref   TEXT NOT NULL CHECK (btrim(tracker_ref) <> ''),
    linked_by     TEXT NOT NULL CHECK (linked_by IN ('owner', 'qa_auto')),
    linked_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    fix_ref       TEXT,
    state         TEXT NOT NULL DEFAULT 'tracked' CHECK (
        state IN ('tracked', 'fix_merged', 'awaiting_recovery', 'regressed', 'closed')
    ),
    expires_at    TIMESTAMPTZ,
    CONSTRAINT uq_qa_known_issue_links_ref UNIQUE (fingerprint, tracker_ref)
);
CREATE INDEX IF NOT EXISTS idx_qa_known_issue_links_gate
    ON {_TABLE} (fingerprint)
    WHERE state IN ('tracked', 'fix_merged', 'awaiting_recovery');
"""


def _for_existing_role(role: str, statement: str) -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                EXECUTE '{statement}';
            END IF;
        EXCEPTION
            WHEN insufficient_privilege OR undefined_object OR undefined_table THEN NULL;
        END $$
        """
    )


def _installation_count() -> int:
    """Count schema-local core chains that still depend on this revision (core_246)."""
    bind = op.get_bind()
    schemas = bind.exec_driver_sql(
        """
        SELECT namespace.nspname
        FROM pg_class version_table
        JOIN pg_namespace namespace ON namespace.oid = version_table.relnamespace
        WHERE version_table.relname = 'alembic_version'
          AND version_table.relkind IN ('r', 'p')
          AND left(namespace.nspname, 3) <> 'pg_'
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
                    THEN substring(version_num FROM 6)::integer >= 372
                    ELSE false END
            )
            """
        ).scalar()
        count += int(bool(installed))
    return count


def upgrade() -> None:
    op.execute(_LOCK)
    if op.get_bind().exec_driver_sql(_ALREADY_INSTALLED).scalar():
        return
    op.execute(_DDL)
    op.execute(f"REVOKE ALL PRIVILEGES ON TABLE {_TABLE} FROM PUBLIC")
    # The table is the QA suppression control: a role that can write it can silence QA. Only
    # the QA butler's runtime role touches it (the dashboard API reaches it as the database
    # owner); every other butler and the connector writer get nothing.
    for role in (*butler_roles(), "connector_writer"):
        _for_existing_role(role, f"REVOKE ALL PRIVILEGES ON TABLE {_TABLE} FROM {role}")
    _for_existing_role(_QA_ROLE, f"GRANT {_PRIVILEGES} ON TABLE {_TABLE} TO {_QA_ROLE}")


def downgrade() -> None:
    op.execute(_LOCK)
    # One shared table serves every schema still at core_372; only the last one drops it.
    if _installation_count() > 1:
        return
    op.execute(f"DROP TABLE IF EXISTS {_TABLE}")
