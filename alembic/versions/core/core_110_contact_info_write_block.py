"""contact_info write-block: REVOKE INSERT/UPDATE/DELETE for the cut-over.

Revision ID: core_110
Revises: core_109
Create Date: 2026-05-30 00:00:00.000000

Migration bead 8 — write-path cut-over (entity-redesign contacts → triples,
bu-k9ylx).

After the contacts → ``relationship.entity_facts`` write-path cut-over, all
channel-identity writes go through the central writer
``relationship_assert_fact()`` ONLY.  ``public.contact_info`` becomes
**read-only**: SELECT is retained (reads stay allowed — contact_info is the
authoritative legacy store until the 30-day soak + operator sign-off gate the
final DROP in Migration bead 10), but INSERT / UPDATE / DELETE are revoked from
every butler runtime role and the connector_writer role.

Scope note — ``public.contacts`` is intentionally NOT write-blocked here
--------------------------------------------------------------------------
The binding spec (openspec/specs/relationship-facts/spec.md §"Requirement:
Migration safety", step 5) cuts over the **write path** by making
``public.contact_info`` read-only.  ``public.contacts`` is dropped only at
Migration bead 10 *together with* ``public.contact_info`` after the 30-day soak.

``relationship.entity_facts`` stores channel FACTS (``has-email``, ``has-phone``,
``has-handle``, ``has-website``).  It does NOT store the contact RECORD (name,
``entity_id`` link, ``archived_at``, ``metadata``, ``stay_in_touch_days``).
``contact_create`` / ``contact_update`` / ``contact_archive`` / ``contact_merge``
and the entity-linking endpoints still legitimately write ``public.contacts``;
there is no triple replacement for the contact record.  Revoking writes on
``public.contacts`` would break contact creation, entity-linking, and merge with
no replacement path, so it is out of scope for bead 8.

Reversibility
-------------
``downgrade()`` re-GRANTs INSERT/UPDATE/DELETE on ``public.contact_info`` to the
same roles, restoring the pre-cut-over grant state from
``core_065_public_schema_write_grants.py`` (which granted
``INSERT, UPDATE, DELETE`` on ``contact_info``).  This makes the cut-over fully
reversible at the database layer.

Idempotency
-----------
REVOKE/GRANT are tolerant of missing roles/tables via the best-effort DO blocks
(mirroring core_065/core_008), so the migration is safe to re-run and safe on
DBs (e.g. dev without CREATEROLE) where the runtime roles do not exist.
"""

from __future__ import annotations

from alembic import op

revision = "core_110"
down_revision = "core_109"
branch_labels = None
depends_on = None

# Mirror the role set used by core_065_public_schema_write_grants.py so the
# revoke/re-grant covers exactly the roles that were granted contact_info writes.
_ROLE_SCHEMAS = (
    "education",
    "finance",
    "general",
    "health",
    "home",
    "lifestyle",
    "messenger",
    "relationship",
    "switchboard",
    "travel",
)
_RUNTIME_ROLES = [f"butler_{schema}_rw" for schema in _ROLE_SCHEMAS]
_CONNECTOR_ROLE = "connector_writer"
_ALL_ROLES = [*_RUNTIME_ROLES, _CONNECTOR_ROLE]

# contact_info becomes read-only: revoke write DML, keep SELECT.
_WRITE_PRIVILEGES = "INSERT, UPDATE, DELETE"
_TABLE_FQN = "public.contact_info"


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _execute_best_effort(statement: str, *, role_name: str | None = None) -> None:
    """Execute SQL while tolerating privilege/role/table availability differences."""
    condition = "TRUE"
    if role_name is not None:
        condition = f"EXISTS (SELECT 1 FROM pg_roles WHERE rolname = {_quote_literal(role_name)})"
    op.execute(
        f"""
        DO $$
        BEGIN
            IF {condition} THEN
                EXECUTE {_quote_literal(statement)};
            END IF;
        EXCEPTION
            WHEN insufficient_privilege THEN NULL;
            WHEN undefined_object       THEN NULL;
            WHEN undefined_table        THEN NULL;
            WHEN invalid_schema_name    THEN NULL;
        END
        $$;
        """
    )


def upgrade() -> None:
    # Write-block public.contact_info: revoke INSERT/UPDATE/DELETE, keep SELECT.
    for role_name in _ALL_ROLES:
        quoted_role = _quote_ident(role_name)
        _execute_best_effort(
            f"REVOKE {_WRITE_PRIVILEGES} ON {_TABLE_FQN} FROM {quoted_role}",
            role_name=role_name,
        )


def downgrade() -> None:
    # Restore the pre-cut-over grant state (re-grant write DML on contact_info).
    for role_name in _ALL_ROLES:
        quoted_role = _quote_ident(role_name)
        _execute_best_effort(
            f"GRANT {_WRITE_PRIVILEGES} ON {_TABLE_FQN} TO {quoted_role}",
            role_name=role_name,
        )
