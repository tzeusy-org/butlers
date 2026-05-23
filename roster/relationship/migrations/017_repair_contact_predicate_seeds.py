"""predicate_registry: repair contact predicate seed rows.

Revision ID: rel_017
Revises: rel_016
Create Date: 2026-05-23 00:00:00.000000

Some deployed databases may have already run ``rel_014`` before the contact
predicate catalog included the channel-collapsed ``has-handle`` row.  The
contact-info reconciler maps Telegram/LinkedIn/Twitter/other rows to
``has-handle``, so those databases reject the central writer validation until
the predicate registry is repaired.

This forward migration is intentionally idempotent: it upserts the current
contact predicate seed set without relying on the original ``rel_014`` seed
loop being re-run.
"""

from __future__ import annotations

from alembic import op

revision = "rel_017"
down_revision = "rel_016"
branch_labels = None
depends_on = None

_RELATIONSHIP_ROLE = "butler_relationship_rw"
_TABLE_PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE"
_TABLE_FQN = "relationship.entity_predicate_registry"

_CONTACT_PREDICATES: list[tuple[str, str, str, str]] = [
    ("has-email", "contact", "literal", "Email address for the entity."),
    ("has-phone", "contact", "literal", "Phone number for the entity."),
    (
        "has-handle",
        "contact",
        "literal",
        "Channel-scoped handle (e.g. telegram:<id>, discord:<id>).",
    ),
    ("has-address", "contact", "literal", "Physical mailing address for the entity."),
    ("has-birthday", "contact", "literal", "Date of birth in ISO-8601 format (YYYY-MM-DD)."),
    ("has-website", "contact", "literal", "Web URL associated with the entity."),
]


def _grant_best_effort(table_fqn: str, privilege: str, role: str) -> None:
    """GRANT privilege ON table TO role; tolerate older DBs missing roles."""
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('{table_fqn}') IS NOT NULL
               AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}')
            THEN
                EXECUTE 'GRANT {privilege} ON TABLE {table_fqn} TO "{role}"';
            END IF;
        EXCEPTION
            WHEN insufficient_privilege THEN NULL;
            WHEN undefined_object      THEN NULL;
            WHEN undefined_table       THEN NULL;
            WHEN invalid_schema_name   THEN NULL;
        END
        $$;
        """
    )


def upgrade() -> None:
    for predicate, kind, object_kind, description in _CONTACT_PREDICATES:
        safe_desc = description.replace("'", "''")
        op.execute(f"""
            INSERT INTO relationship.entity_predicate_registry
                (predicate, kind, object_kind, description)
            VALUES ('{predicate}', '{kind}', '{object_kind}', '{safe_desc}')
            ON CONFLICT (predicate) DO UPDATE
            SET kind = EXCLUDED.kind,
                object_kind = EXCLUDED.object_kind,
                description = EXCLUDED.description
        """)

    _grant_best_effort(_TABLE_FQN, _TABLE_PRIVILEGES, _RELATIONSHIP_ROLE)


def downgrade() -> None:
    # No-op by design. The repaired rows are part of the current rel_014
    # contract, so downgrading this repair migration must not remove predicates
    # that older migration state also owns.
    return None
