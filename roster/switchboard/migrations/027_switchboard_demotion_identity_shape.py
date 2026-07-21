"""Pin demotion suggestion identity fields to NULL.

Revision ID: sw_027
Revises: sw_026
Create Date: 2026-07-19 00:00:00.000000

``target_rule_id`` is the authoritative scope and display reference for a
demotion suggestion. A sender/channel from one spot-check event would be
sample-specific and can misrepresent a rule that is scoped by domain, header,
or another condition. Clear any legacy sampled identity values before tightening
the kind-shape CHECK so demotion rows retain only their target-rule reference
and evidence.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "sw_027"
down_revision = "sw_026"
branch_labels = None
depends_on = None

_CURRENT_KIND_SHAPE = """
CHECK (
    (
        suggestion_kind = 'promotion'
        AND sender_key IS NOT NULL AND sender_key <> ''
        AND source_channel IS NOT NULL AND source_channel <> ''
        AND proposed_rule_type IS NOT NULL
        AND proposed_condition IS NOT NULL
        AND proposed_action IS NOT NULL AND proposed_action <> ''
        AND target_rule_id IS NULL
    )
    OR
    (
        suggestion_kind = 'demotion'
        AND target_rule_id IS NOT NULL
        AND proposed_rule_type IS NULL
        AND proposed_condition IS NULL
        AND proposed_action IS NULL
    )
)
"""

_DEMOTION_IDENTITY_NULL_KIND_SHAPE = """
CHECK (
    (
        suggestion_kind = 'promotion'
        AND sender_key IS NOT NULL AND sender_key <> ''
        AND source_channel IS NOT NULL AND source_channel <> ''
        AND proposed_rule_type IS NOT NULL
        AND proposed_condition IS NOT NULL
        AND proposed_action IS NOT NULL AND proposed_action <> ''
        AND target_rule_id IS NULL
    )
    OR
    (
        suggestion_kind = 'demotion'
        AND target_rule_id IS NOT NULL
        AND sender_key IS NULL
        AND source_channel IS NULL
        AND proposed_rule_type IS NULL
        AND proposed_condition IS NULL
        AND proposed_action IS NULL
    )
)
"""


def _replace_kind_shape(shape: str) -> None:
    op.execute(
        "ALTER TABLE rule_promotion_suggestions "
        "DROP CONSTRAINT chk_rule_promotion_suggestions_kind_shape"
    )
    op.execute(
        "ALTER TABLE rule_promotion_suggestions "
        "ADD CONSTRAINT chk_rule_promotion_suggestions_kind_shape "
        f"{shape}"
    )


def upgrade() -> None:
    # sw_020 allowed these fields on demotion rows. They have no stable
    # semantics: target_rule_id, not a single spot-check event, defines scope.
    op.execute("LOCK TABLE rule_promotion_suggestions IN SHARE ROW EXCLUSIVE MODE")
    op.execute(
        """
        UPDATE rule_promotion_suggestions
        SET sender_key = NULL,
            source_channel = NULL
        WHERE suggestion_kind = 'demotion'
          AND (sender_key IS NOT NULL OR source_channel IS NOT NULL)
        """
    )
    _replace_kind_shape(_DEMOTION_IDENTITY_NULL_KIND_SHAPE)


def downgrade() -> None:
    # The discarded sampled values are intentionally not reconstructable; only
    # restore the prior permissive constraint for callers rolling back code.
    _replace_kind_shape(_CURRENT_KIND_SHAPE)
