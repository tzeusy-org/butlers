"""Allow promotion suggestions to represent exact connector endpoints.

Revision ID: sw_029
Revises: sw_028
Create Date: 2026-08-01 00:00:00.000000

``routing_verdict_log.sender_key`` is intentionally multi-channel. The early
promotion schema allowed only email-address and domain rule types, which caused
opaque identities such as ``spotify:tzeusii`` to be stored as
``sender_address`` conditions even though the evaluator correctly treats that
matcher as email-only. Add the explicit, exact ``source_endpoint`` shape for
new suggestions. Existing rows are deliberately not rewritten: the runtime
compatibility path recognizes provenance-linked legacy promotion rows while
preserving their audit history.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "sw_029"
down_revision = "sw_028"
branch_labels = None
depends_on = None

_PREVIOUS_RULE_TYPE_CHECK = """
CHECK (
    proposed_rule_type IS NULL
    OR proposed_rule_type IN ('sender_address', 'sender_domain')
)
"""

_CURRENT_RULE_TYPE_CHECK = """
CHECK (
    proposed_rule_type IS NULL
    OR proposed_rule_type IN ('sender_address', 'sender_domain', 'source_endpoint')
)
"""


def _replace_rule_type_check(check: str) -> None:
    op.execute(
        "ALTER TABLE rule_promotion_suggestions "
        "DROP CONSTRAINT chk_rule_promotion_suggestions_proposed_rule_type"
    )
    op.execute(
        "ALTER TABLE rule_promotion_suggestions "
        "ADD CONSTRAINT chk_rule_promotion_suggestions_proposed_rule_type "
        f"{check}"
    )


def upgrade() -> None:
    op.execute("LOCK TABLE rule_promotion_suggestions IN SHARE ROW EXCLUSIVE MODE")
    _replace_rule_type_check(_CURRENT_RULE_TYPE_CHECK)


def downgrade() -> None:
    # Do not rewrite historic suggestions merely to make a binary rollback
    # possible. PostgreSQL will fail this replacement if source_endpoint rows
    # exist, requiring an operator to choose an explicit data-preserving
    # migration/retention procedure before downgrading.
    _replace_rule_type_check(_PREVIOUS_RULE_TYPE_CHECK)
