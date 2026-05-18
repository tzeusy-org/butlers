"""credentials: credential carve-out table for secured contact_info rows.

Revision ID: rel_016
Revises: rel_015
Create Date: 2026-05-18 00:00:00.000000

Phase: entity-redesign (bu-uj3xv — §10.4 relationship.credentials carve-out).

Background
----------
``public.contact_info`` rows with ``secured = true`` (RFC 0004:54) are
**credentials** — encrypted blobs or secret-store pointers — and MUST NOT be
migrated to ``relationship.entity_facts`` as triples.

Brief §6b Amendment 1.1.A.4 mandates that these rows instead land in a
dedicated ``relationship.credentials`` table, co-located with the relationship
butler (canonical credential writer, per ``roster/relationship/butler.toml``).

This migration creates that table per the Phase 2 decision documented in
``openspec/changes/relationship-tabs-to-entities/specs/relationship-facts/spec.md``
§"Requirement: Credentials carve-out".

Schema
------
id           UUID PK       gen_random_uuid()
entity_id    UUID NOT NULL FK → public.entities(id) ON DELETE CASCADE
type         TEXT NOT NULL Credential type slug (e.g. telegram_session,
                           gmail_oauth, gmail_token, steam_api_key)
value        TEXT NOT NULL Encrypted blob, base64-encoded ciphertext, or
                           pointer to the external secret manager
created_at   TIMESTAMPTZ   NOT NULL DEFAULT now()
updated_at   TIMESTAMPTZ   NOT NULL DEFAULT now()
last_used_at TIMESTAMPTZ   NULL — populated when the credential is exercised
revoked_at   TIMESTAMPTZ   NULL — soft-delete; NULL means active

Uniqueness
----------
A partial unique index on ``(entity_id, type)`` where ``revoked_at IS NULL``
prevents duplicate active credentials of the same type for the same entity.
Revoked rows are excluded from the constraint, allowing a new active credential
of the same type to be inserted after rotation (revoke old → insert new).

Indexes
-------
uq_cred_entity_type_active   UNIQUE (entity_id, type) WHERE revoked_at IS NULL
idx_cred_entity_id           (entity_id)  — lookup by entity

Grants
------
SELECT, INSERT, UPDATE, DELETE on relationship.credentials granted to
butler_relationship_rw only. Other butlers access credentials exclusively
through the relationship butler's MCP tool surface (RFC 0006 schema isolation).

Read-path note
--------------
Callers that previously read ``public.contact_info WHERE secured = true``
(e.g. google_credentials.py, steam_account_registry.py, google_account_registry.py)
continue to write secured rows via ``public.entity_info`` (the non-contact
credential anchor in the public schema) and are out-of-scope for this
migration's read-path cut-over. The reconciler in
``roster/relationship/jobs/relationship_jobs.py::run_contact_info_reconciler``
already explicitly filters ``WHERE ci.secured = false`` in SQL AND has a
defensive Python guard that increments ``rows_skipped_credential`` for any
secured row that slips through — ensuring ``relationship.entity_facts`` NEVER
receives secured rows (verified by existing tests in
``roster/relationship/tests/test_reconciler.py``).
"""

from __future__ import annotations

from alembic import op

revision = "rel_016"
down_revision = "rel_015"
branch_labels = None
depends_on = None

_RELATIONSHIP_ROLE = "butler_relationship_rw"
_TABLE_PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE"
_TABLE_FQN = "relationship.credentials"


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
    # 1. Ensure relationship schema exists (idempotent — earlier rel migrations
    #    may have already created it).
    op.execute("CREATE SCHEMA IF NOT EXISTS relationship")

    # 2. Create the credentials table.
    op.execute("""
        CREATE TABLE IF NOT EXISTS relationship.credentials (
            id           UUID        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
            entity_id    UUID        NOT NULL REFERENCES public.entities(id) ON DELETE CASCADE,
            type         TEXT        NOT NULL,
            value        TEXT        NOT NULL,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_used_at TIMESTAMPTZ,
            revoked_at   TIMESTAMPTZ
        )
    """)

    # 3. Partial unique index: one active credential per (entity, type).
    #    Revoked rows (revoked_at IS NOT NULL) are excluded so rotation is
    #    possible: revoke old → insert new active credential of same type.
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_cred_entity_type_active
            ON relationship.credentials (entity_id, type)
            WHERE revoked_at IS NULL
    """)

    # 4. Lookup index — all credentials for a given entity.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_cred_entity_id
            ON relationship.credentials (entity_id)
    """)

    # 5. Grants — relationship butler only (RFC 0006 schema isolation).
    _grant_best_effort(_TABLE_FQN, _TABLE_PRIVILEGES, _RELATIONSHIP_ROLE)


def downgrade() -> None:
    # Drop indexes explicitly before dropping the table for clarity.
    op.execute("DROP INDEX IF EXISTS relationship.uq_cred_entity_type_active")
    op.execute("DROP INDEX IF EXISTS relationship.idx_cred_entity_id")
    op.execute("DROP TABLE IF EXISTS relationship.credentials")
    # NOTE: we intentionally do NOT drop the relationship schema here.
    # Other relationship-butler tables coexist in the schema, and this migration
    # does not own the schema lifecycle.
