"""dead_letter_queue: add 'unanswerable' to the failure_category vocabulary.

Revision ID: sw_033
Revises: sw_032
Create Date: 2026-09-04 00:00:00.000000

bu-0ynlk.2 (question lane). ``cannot_answer`` — the dashboard classifier's new
terminal tool for a question with no identifiable owning butler/scope — dead-
letters through the same ``dead_letter_queue`` capture path ``file_bug_report``
and the pipeline's unroutable-message net already use, but none of the
existing categories (``timeout``, ``retry_exhausted``, ``circuit_open``,
``policy_violation``, ``validation_error``, ``downstream_failure``,
``unknown``) describe "the classifier understood the question but has no
grounded answer or owner" — that is a distinct terminal outcome, not a
transport/policy/validation failure, and needs its own reason code so a
reviewer scanning the dead-letter queue can tell an unanswerable question
apart from every other row without reading ``failure_reason`` prose.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "sw_033"
down_revision = "sw_032"
branch_labels = None
depends_on = None

_CATEGORIES = (
    "timeout",
    "retry_exhausted",
    "circuit_open",
    "policy_violation",
    "validation_error",
    "downstream_failure",
    "unanswerable",
    "unknown",
)


def upgrade() -> None:
    quoted = ", ".join(f"'{category}'" for category in _CATEGORIES)
    op.execute("ALTER TABLE dead_letter_queue DROP CONSTRAINT IF EXISTS valid_failure_category")
    op.execute(
        "ALTER TABLE dead_letter_queue ADD CONSTRAINT valid_failure_category CHECK ("
        f"failure_category IN ({quoted})"
        ")"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE dead_letter_queue DROP CONSTRAINT IF EXISTS valid_failure_category")
    # Preserve downgradeability after the new category has been used. The
    # original payload and failure_reason retain the exact decline evidence;
    # only the vocabulary value must map back to the predecessor's catch-all.
    op.execute(
        "UPDATE dead_letter_queue SET failure_category = 'unknown' "
        "WHERE failure_category = 'unanswerable'"
    )
    op.execute(
        "ALTER TABLE dead_letter_queue ADD CONSTRAINT valid_failure_category CHECK ("
        "failure_category IN ("
        "'timeout', 'retry_exhausted', 'circuit_open', 'policy_violation', "
        "'validation_error', 'downstream_failure', 'unknown'"
        "))"
    )
