"""``broker_refusals.reason`` admits ``capability_unavailable``.

Revision ID: sw_067
Revises: sw_066

RFC 0038 S6 (bu-dhloi1.14). When published health says a dependency a capability
declares is known down, unconfigured or held, the broker refuses the call before it
reserves a budget slot or records an effect, and the refusal is durable like every
other (D7). Its closed reason is ``capability_unavailable``, which the reason CHECK
did not admit. The refusal row keeps only that word and the capability identity;
the dependency, cause and age live in published health, not in the refusal record.

Downgrade refuses while any refusal with this reason exists (the ledger is
append-only history), then restores the previous list.
"""

from alembic import op

revision = "sw_067"
down_revision = "sw_066"
branch_labels = None
depends_on = None

_BEFORE = (
    "'authentication_refused','goal_not_running','lead_epoch_revoked',"
    "'capability_not_granted','catalog_changed','resource_outside_grant',"
    "'policy_refused','budget_exhausted','effect_already_recorded','broker_unavailable',"
    "'owner_policy_ask_on_read'"
)
_NEW = "'capability_unavailable'"


def _reasons(reasons: str) -> str:
    return (
        "ALTER TABLE switchboard.broker_refusals "
        "DROP CONSTRAINT broker_refusals_reason_check, "
        f"ADD CONSTRAINT broker_refusals_reason_check CHECK (reason IN ({reasons}))"
    )


def upgrade() -> None:
    op.execute("LOCK TABLE switchboard.broker_refusals IN ACCESS EXCLUSIVE MODE")
    op.execute(_reasons(f"{_BEFORE},{_NEW}"))


def downgrade() -> None:
    op.execute("LOCK TABLE switchboard.broker_refusals IN ACCESS EXCLUSIVE MODE")
    op.execute(f"""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM switchboard.broker_refusals WHERE reason = {_NEW}) THEN
                RAISE EXCEPTION 'Cannot discard durable capability_unavailable refusals';
            END IF;
        END $$;
        {_reasons(_BEFORE)}
    """)
