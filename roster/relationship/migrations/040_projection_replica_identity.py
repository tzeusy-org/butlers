"""Replica identity over the columns the public MCP projection filters entity facts on.

Revision ID: rel_040
Revises: rel_039

RFC 0039 D2 and REQ-public-mcp-projection-005 (bu-y66o00.3). The ``relationship.entity_facts``
entry filters on ``validity`` and ``predicate``, so both join ``id`` in the replica identity and a
retracted fact reaches the projection as a DELETE.
"""

from __future__ import annotations

from butlers.migrations.projection import drop_replica_identity, ensure_replica_identity

revision = "rel_040"
down_revision = "rel_039"
branch_labels = None
depends_on = None

_TABLE = "relationship.entity_facts"


def upgrade() -> None:
    ensure_replica_identity(_TABLE, ("id",), ("validity", "predicate"))


def downgrade() -> None:
    drop_replica_identity(_TABLE)
