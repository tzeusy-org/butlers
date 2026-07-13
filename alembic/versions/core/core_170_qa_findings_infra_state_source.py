"""qa_findings: allow infra_state source type.

Revision ID: core_170
Revises: core_169
Create Date: 2026-07-13 00:00:00.000000

The QA module emits ``QaFinding(source_type="infra_state")`` for connector,
butler-heartbeat, backup, and external-deadman health findings.  The persisted
source vocabulary still accepted only the four earlier discovery sources, so
the first infra-state finding aborted its patrol with a check violation.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_170"
down_revision = "core_169"
branch_labels = None
depends_on = None

_CONSTRAINT = "ck_qa_findings_source_type"
_TABLE = "public.qa_findings"

_SOURCES = (
    "log_scanner",
    "session_records",
    "butler_reports",
    "tool_call_failures",
    "infra_state",
)


def _source_check_sql(sources: tuple[str, ...]) -> str:
    allowed = ", ".join(f"'{source}'" for source in sources)
    return f"source_type IN ({allowed})"


def _replace_source_constraint(sources: tuple[str, ...]) -> None:
    op.execute(f"ALTER TABLE IF EXISTS {_TABLE} DROP CONSTRAINT IF EXISTS {_CONSTRAINT}")
    op.execute(f"""
        ALTER TABLE IF EXISTS {_TABLE}
        ADD CONSTRAINT {_CONSTRAINT}
        CHECK ({_source_check_sql(sources)})
    """)


def upgrade() -> None:
    _replace_source_constraint(_SOURCES)


def downgrade() -> None:
    # Narrowing the constraint would either reject persisted infra-state
    # findings or require deleting/relabeling operational history.  Keeping the
    # extra accepted value is backward-compatible with the core_169 runtime,
    # which simply never emits it.
    pass
