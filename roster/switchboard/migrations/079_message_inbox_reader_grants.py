"""Grant the three message_inbox reader roles SELECT on message_inbox (bu-svqpzo.13).

Revision ID: sw_079
Revises: sw_078

core_339 narrows the relationship, messenger and travel roles from every Switchboard table
to ``message_inbox`` alone.  On a fresh database the core chain runs before this chain has
created the table, so the grant is made here, in the chain that owns the table.  On an
existing database it repeats the grant core_339 already made.  Roles that do not exist are
skipped.  The downgrade changes nothing: core_339 made the same grant, so revoking it here
would strand an upgraded database at core_339 without the read; core_339's downgrade owns the
restore of the prior grants.
"""

from alembic import op

revision = "sw_079"
down_revision = "sw_078"
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
    # Intentionally empty: see the module docstring.
    pass
