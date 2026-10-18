"""Grant the three message_inbox reader roles SELECT on message_inbox (bu-svqpzo.13).

Revision ID: sw_047
Revises: sw_046

core_269 narrows the relationship, messenger and travel roles from every Switchboard table
to ``message_inbox`` alone.  On a fresh database the core chain runs before this chain has
created the table, so the grant is made here, in the chain that owns the table.  On an
existing database it repeats the grant core_269 already made.  Roles that do not exist are
skipped.  The downgrade revokes only this grant.
"""

from alembic import op

revision = "sw_047"
down_revision = "sw_046"
branch_labels = None
depends_on = None

_ROLES = ("butler_relationship_rw", "butler_messenger_rw", "butler_travel_rw")


def _each_role(verb_sql: str) -> None:
    for role in _ROLES:
        op.execute(
            f"""
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                    EXECUTE '{verb_sql.format(role=role)}';
                END IF;
            END
            $$;
            """
        )


def upgrade() -> None:
    _each_role('GRANT SELECT ON TABLE message_inbox TO "{role}"')


def downgrade() -> None:
    _each_role('REVOKE SELECT ON TABLE message_inbox FROM "{role}"')
