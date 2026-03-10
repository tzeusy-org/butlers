"""memory_policies — mem_017

Create the memory_policies table and seed it with 8 retention classes.

The memory_policies table drives policy-driven lifecycle management for all
memory types.  The retention_class column on episodes, facts, and rules
(added in mem_014) references these classes.

Table: memory_policies
  retention_class  TEXT PRIMARY KEY   — matches episodes.retention_class etc.
  default_ttl_days INT                — NULL means no expiry (permanent)
  max_importance   FLOAT              — ceiling on importance for this class
  auto_archive     BOOLEAN            — archive instead of delete on expiry

Seeded retention classes (8):
  transient    — short-lived session scratchpad (default for episodes)
  operational  — working facts with standard decay (default for facts)
  rule         — behavioral rules (default for rules)
  health_log   — health/fitness tracking data
  financial    — financial records (longer retention)
  relationship — contact and relationship data
  preference   — user preferences and settings
  permanent    — permanent records, never expire

Revision ID: mem_017
Revises: mem_016
Create Date: 2026-03-10 00:00:00.000000
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "mem_017"
down_revision = "mem_016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---------------------------------------------------------------------------
    # Create memory_policies table
    # ---------------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS memory_policies (
            retention_class  TEXT    PRIMARY KEY,
            default_ttl_days INT,
            max_importance   FLOAT   NOT NULL DEFAULT 10.0,
            auto_archive     BOOLEAN NOT NULL DEFAULT FALSE
        )
    """)

    # ---------------------------------------------------------------------------
    # Seed with 8 retention classes
    # Use INSERT ... ON CONFLICT DO NOTHING so reruns are safe.
    # ---------------------------------------------------------------------------
    op.execute("""
        INSERT INTO memory_policies
            (retention_class, default_ttl_days, max_importance, auto_archive)
        VALUES
            ('transient',    7,    10.0, FALSE),
            ('operational',  90,   10.0, FALSE),
            ('rule',         365,  10.0, FALSE),
            ('health_log',   730,  10.0, TRUE),
            ('financial',    2555, 10.0, TRUE),
            ('relationship', 1825, 10.0, FALSE),
            ('preference',   365,  10.0, FALSE),
            ('permanent',    NULL, 10.0, FALSE)
        ON CONFLICT (retention_class) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS memory_policies")
