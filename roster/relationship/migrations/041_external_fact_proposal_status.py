"""Primary proposal status foundation, without an importer or approval authority.

Revision ID: rel_041
Revises: rel_040

RFC 0039 D4/D13; REQ-public-mcp-projection-012 and REQ-public-mcp-proposals-006.
Only proposal_id/state/decided_at are public. The future importer owns intake,
validation, action reconciliation and clearing private text after 30 days; no
job, tool or trigger here receives or applies proposals. IDs are caller-assigned,
not random defaults, and received is an inbox state rather than a primary state.
"""

from alembic import op

revision = "rel_041"
down_revision = "rel_040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS relationship.external_fact_proposals (
            proposal_id UUID PRIMARY KEY,
            state TEXT NOT NULL DEFAULT 'imported'
                CHECK (state IN ('imported', 'parked', 'invalid', 'duplicate', 'conflict',
                    'rate_limited', 'applied', 'owner_rejected', 'expired', 'apply_failed')),
            decided_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            note TEXT CHECK (length(note) <= 500),
            evidence_url TEXT,
            CONSTRAINT external_fact_proposals_decision_consistent CHECK (
                (state IN ('imported', 'parked') AND decided_at IS NULL)
                OR (state NOT IN ('imported', 'parked') AND decided_at IS NOT NULL)
            )
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS relationship.external_fact_proposals")
