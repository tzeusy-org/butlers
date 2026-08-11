"""chronicler_comms_entity_facts_grant: grant relationship.entity_facts read access.

Revision ID: core_150
Revises: core_149
Create Date: 2026-07-03 00:00:00.000000

Grants ``butler_chronicler_rw`` SELECT on ``relationship.entity_facts`` so the
new comms->Social projection adapter (``src/butlers/chronicler/adapters/comms.py``,
source_name ``comms.message_bursts``) can resolve message-burst participants to
entities, per the same convention CoreSessionsAdapter already relies on for
resolving route-triggered session contacts (bu-hjo3i).

``butler_chronicler_rw`` already has ``USAGE`` on the ``relationship`` schema
(granted per-butler-schema by ``scripts/init-db.sql``) and already reads
``relationship.entity_facts`` cross-schema in production via
``CoreSessionsAdapter._resolve_contacts`` -- but that SELECT grant was never
made explicit as its own RFC 0014 §D8 evidence-surface migration. This
migration closes that gap for both callers and is the explicit grant the new
``comms.message_bursts`` compatibility declaration in
``src/butlers/chronicler/contracts.py`` requires.

Do NOT restore a blanket ``GRANT SELECT ON ALL TABLES`` for
``butler_chronicler_rw`` -- RFC 0014 §D1 requires per-table, explicit grants
only for the evidence surfaces adapters actually declare.
"""

from __future__ import annotations

from alembic import op

revision = "core_150"
down_revision = "core_149"
branch_labels = None
depends_on = None

_CHRONICLER_ROLE = "butler_chronicler_rw"
_TABLE = "relationship.entity_facts"


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _execute_best_effort(statement: str, *, role_name: str) -> None:
    """Execute a DDL statement only when its role and relation exist.

    Core can run before the specialist relationship chain. ``init-db.sql``
    creates the chronicler role independently, so role existence alone does
    not establish that the cross-schema relation is available to grant.
    """
    condition = (
        f"EXISTS (SELECT 1 FROM pg_roles WHERE rolname = {_quote_literal(role_name)}) "
        f"AND to_regclass({_quote_literal(_TABLE)}) IS NOT NULL"
    )
    op.execute(
        f"""
        DO $$
        BEGIN
            IF {condition} THEN
                {statement};
            END IF;
        END;
        $$
        """
    )


def upgrade() -> None:
    _execute_best_effort(
        f"GRANT SELECT ON TABLE {_TABLE} TO {_quote_ident(_CHRONICLER_ROLE)}",
        role_name=_CHRONICLER_ROLE,
    )


def downgrade() -> None:
    _execute_best_effort(
        f"REVOKE SELECT ON TABLE {_TABLE} FROM {_quote_ident(_CHRONICLER_ROLE)}",
        role_name=_CHRONICLER_ROLE,
    )
