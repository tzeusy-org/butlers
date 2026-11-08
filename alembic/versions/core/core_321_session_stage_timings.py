"""session_stage_timings: content-free per-stage timing on sessions (bu-dhloi1.180).

Revision ID: core_321
Revises: core_320

A session row carried only total ``duration_ms``, so spawn, MCP handshake, model and
tool time could not be told apart. This adds one nullable JSONB column holding
millisecond offsets from the spawner's start plus counts and sizes: the stage
offsets, whitelisted adapter integers and the MCP tool-surface size. It never holds
prompts, messages, tool arguments or results (RFC 0038 D17).

``sessions`` is a per-butler-schema table (unqualified name resolves through the
migration's search_path). Additive and nullable: existing rows and writers keep
working, and a session whose clock the process never ran stays NULL.
"""

from alembic import op

revision = "core_321"
down_revision = "core_320"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE sessions ADD COLUMN IF NOT EXISTS stage_timings JSONB NULL")


def downgrade() -> None:
    op.execute("ALTER TABLE sessions DROP COLUMN IF EXISTS stage_timings")
