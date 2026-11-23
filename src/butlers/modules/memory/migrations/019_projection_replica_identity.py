"""Replica identity over the columns the public MCP projection filters facts on.

Revision ID: mem_019
Revises: mem_018

RFC 0039 D2 and REQ-public-mcp-projection-005 (bu-y66o00.3). A fact entry filters on
``sensitivity``, ``validity`` and ``predicate``, so all three join ``id`` in the replica identity
and a fact that stops matching reaches the projection as a DELETE. Every schema running this chain
gets the same identity, because the manifest decides which of them are published and adds a
schema by editing an entry, not by migrating. Unpublished schemas pay only an index.
"""

from alembic import op
from butlers.migrations.projection import drop_replica_identity, ensure_replica_identity

revision = "mem_019"
down_revision = "mem_018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    ensure_replica_identity("facts", ("id",), ("sensitivity", "validity", "predicate"))


def downgrade() -> None:
    bind = op.get_bind()
    schema = bind.exec_driver_sql(
        "SELECT n.nspname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE c.oid = to_regclass('facts')"
    ).scalar_one()
    drop_replica_identity(f"{schema}.facts")
