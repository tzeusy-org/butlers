"""Drop verified-dead memory module table: embedding_versions.

Revision ID: mem_005
Revises: mem_004
Create Date: 2026-06-12 00:00:00.000000

Table has 0 runtime code references (only created in 001_memory_schema.py).
Contains 1 seed row per schema, all with 0 active embeddings.
The column "embedding_model_version" (added by mem_004) is a separate string field
on memory entities and is NOT related to this table.

CREATE location: 001_memory_schema.py (mem_001)

Guards:
  - DROP TABLE IF EXISTS is idempotent and schema-safe.
  - Applied per butler schema; IF EXISTS ensures safety across schemas.

Downgrade recreates the original embedding_versions schema (mem_001); the seed row
is not restored (it was analytics scaffolding with 0 active embeddings).
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "mem_005"
down_revision = "mem_004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS embedding_versions")


def downgrade() -> None:
    # Recreate the original schema from mem_001 (001_memory_schema.py).
    # Seed row not restored (it was analytics scaffolding with 0 active embeddings).
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS embedding_versions (
            id SERIAL PRIMARY KEY,
            model_name TEXT NOT NULL,
            dimension INTEGER NOT NULL,
            description TEXT,
            active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_embedding_versions_model UNIQUE (model_name)
        )
        """
    )
