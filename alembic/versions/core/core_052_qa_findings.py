"""qa_findings: create public.qa_findings table.

Revision ID: core_052
Revises: core_051
Create Date: 2026-04-05 00:00:00.000000

Creates the QA findings table in the public schema.
Each row represents a single error/issue discovered during a patrol cycle,
from any discovery source (log scanner, session records, butler reports).

Columns:
  id                  UUIDv7 PK
  patrol_id           UUID FK → public.qa_patrols.id (NOT NULL)
  fingerprint         TEXT NOT NULL — SHA-256 hex fingerprint of the error
  source_type         TEXT NOT NULL — discovery source name (log_scanner,
                          session_records, butler_reports)
  source_butler       TEXT NOT NULL — butler where the error originated
  severity            INTEGER NOT NULL — 0=critical, 1=high, 2=medium,
                          3=low, 4=info
  exception_type      TEXT NOT NULL — fully qualified exception class name
  event_summary       TEXT NOT NULL — sanitized human-readable summary
  call_site           TEXT NOT NULL — <file>:<function> of innermost app frame
  occurrence_count    INTEGER NOT NULL DEFAULT 1
  first_seen          TIMESTAMPTZ NOT NULL
  last_seen           TIMESTAMPTZ NOT NULL
  dedup_reason        TEXT — NULL if novel, otherwise why it was deduplicated
                          (e.g. 'active_attempt', 'open_pr', 'dismissed',
                           'cooldown', 'below_severity_threshold')
  healing_attempt_id  UUID FK → public.healing_attempts.id (nullable)
                          — set when an investigation is dispatched for this finding
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now()

Indexes:
  idx_qa_findings_patrol_id     — join to patrol records
  idx_qa_findings_fingerprint   — dedup lookups
  idx_qa_findings_source_butler — filter by butler
  idx_qa_findings_severity      — filter by severity for triage

FK: patrol_id references public.qa_patrols (created in core_051).
FK: healing_attempt_id references public.healing_attempts (created in core_005).
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_052"
down_revision = "core_051"
branch_labels = None
depends_on = None

_ALL_BUTLER_ROLES = (
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
    "butler_qa_rw",
)

_TABLE_PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE"


def _grant_best_effort(table_fqn: str, privilege: str, role: str) -> None:
    """GRANT privilege ON table TO role; tolerates missing role/table."""
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('{table_fqn}') IS NOT NULL
               AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}')
            THEN
                EXECUTE 'GRANT {privilege} ON TABLE {table_fqn} TO "{role}"';
            END IF;
        EXCEPTION
            WHEN insufficient_privilege THEN NULL;
            WHEN undefined_object THEN NULL;
            WHEN undefined_table THEN NULL;
            WHEN invalid_schema_name THEN NULL;
        END
        $$;
        """
    )


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.qa_findings (
            id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            patrol_id          UUID NOT NULL
                                   REFERENCES public.qa_patrols(id)
                                   ON DELETE CASCADE,
            fingerprint        TEXT NOT NULL,
            source_type        TEXT NOT NULL,
            source_butler      TEXT NOT NULL,
            severity           INTEGER NOT NULL,
            exception_type     TEXT NOT NULL,
            event_summary      TEXT NOT NULL,
            call_site          TEXT NOT NULL,
            occurrence_count   INTEGER NOT NULL DEFAULT 1,
            first_seen         TIMESTAMPTZ NOT NULL,
            last_seen          TIMESTAMPTZ NOT NULL,
            dedup_reason       TEXT,
            healing_attempt_id UUID
                                   REFERENCES public.healing_attempts(id)
                                   ON DELETE SET NULL,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_qa_findings_source_type CHECK (
                source_type IN ('log_scanner', 'session_records', 'butler_reports')
            ),
            CONSTRAINT ck_qa_findings_severity CHECK (severity BETWEEN 0 AND 4),
            CONSTRAINT ck_qa_findings_occurrence CHECK (occurrence_count >= 1)
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_qa_findings_patrol_id
        ON public.qa_findings (patrol_id)
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_qa_findings_fingerprint
        ON public.qa_findings (fingerprint)
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_qa_findings_source_butler
        ON public.qa_findings (source_butler)
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_qa_findings_severity
        ON public.qa_findings (severity)
    """)

    for role in _ALL_BUTLER_ROLES:
        _grant_best_effort("public.qa_findings", _TABLE_PRIVILEGES, role)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS public.idx_qa_findings_severity")
    op.execute("DROP INDEX IF EXISTS public.idx_qa_findings_source_butler")
    op.execute("DROP INDEX IF EXISTS public.idx_qa_findings_fingerprint")
    op.execute("DROP INDEX IF EXISTS public.idx_qa_findings_patrol_id")
    op.execute("DROP TABLE IF EXISTS public.qa_findings")
