"""qa_findings: allow tool_call_failures source type.

Revision ID: core_139
Revises: core_138
Create Date: 2026-06-21 00:00:00.000000

``core_125`` added ``public.v_qa_tool_call_failures`` for QA patrol discovery,
and the QA module now emits ``QaFinding(source_type="tool_call_failures")`` from
that source.  The original ``public.qa_findings`` source-type constraint still
accepted only the three earlier sources, so patrols failed when the new source
produced a finding.  This migration reconciles the persisted source vocabulary.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_139"
down_revision = "core_138"
branch_labels = None
depends_on = None

_CONSTRAINT = "ck_qa_findings_source_type"
_TABLE = "public.qa_findings"

_SOURCES = (
    "log_scanner",
    "session_records",
    "butler_reports",
    "tool_call_failures",
)
_DOWNGRADE_SOURCES = (
    "log_scanner",
    "session_records",
    "butler_reports",
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
    _replace_source_constraint(_DOWNGRADE_SOURCES)
