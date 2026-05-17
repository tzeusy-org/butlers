"""webhooks: replace secret_hash with secret_encrypted (AES-256-GCM).

Revision ID: core_100
Revises: core_099
Create Date: 2026-05-16 00:00:00.000000

Phase 4 follow-up (bu-pe9um).  The original ``secret_hash`` column stored a
SHA-256 digest of the webhook signing secret.  That made HMAC signing
impossible because the receiver also needs to hash before comparing, which
deviates from the standard webhook verification pattern (HMAC with the
plaintext secret).

This migration replaces ``secret_hash TEXT`` with ``secret_encrypted BYTEA``
which stores the plaintext secret encrypted with AES-256-GCM (reversible).

Existing rows
-------------
Because SHA-256 is one-way, there is no path from ``secret_hash`` to the
original plaintext.  Existing rows with a non-NULL ``secret_hash`` are left
with ``secret_encrypted = NULL`` after the migration.  The application will
treat NULL as "no secret configured", meaning existing webhooks will no longer
be signed until the user re-supplies their secret via PUT /api/webhooks/{id}.

A warning is logged by the migration script for any rows that lose their
(unusable) hash so that operators can identify webhooks that need
reconfiguration.
"""

from __future__ import annotations

import logging

from alembic import op

logger = logging.getLogger(__name__)

revision = "core_100"
down_revision = "core_099"
branch_labels = None
depends_on = None

_ALL_RUNTIME_ROLES = (
    "butler_chronicler_rw",
    "butler_education_rw",
    "butler_finance_rw",
    "butler_general_rw",
    "butler_health_rw",
    "butler_home_rw",
    "butler_lifestyle_rw",
    "butler_messenger_rw",
    "butler_qa_rw",
    "butler_relationship_rw",
    "butler_switchboard_rw",
    "butler_travel_rw",
    "connector_writer",
)

_TABLE_PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE"


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _grant_best_effort(table_fqn: str, privilege: str, role: str) -> None:
    """GRANT privilege ON table TO role; tolerate older DBs missing roles."""
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('{table_fqn}') IS NOT NULL
               AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}')
            THEN
                EXECUTE 'GRANT {privilege} ON TABLE {table_fqn} TO {_quote_ident(role)}';
            END IF;
        EXCEPTION
            WHEN insufficient_privilege THEN NULL;
            WHEN undefined_object THEN NULL;
            WHEN undefined_table THEN NULL;
            WHEN invalid_schema_name THEN NULL;
        END
        $$;
        """
    )


def upgrade() -> None:
    # Count rows that will lose their (irrecoverable) hash so we can warn.
    # We use op.get_bind() for a lightweight inline query without SQLAlchemy ORM.
    #
    # Guard: the column may already be absent when this migration runs a second
    # time in a schema-scoped context (e.g. running core migrations once
    # unscoped and again with schema="general" on the same database).  Both the
    # DROP and ADD below use IF EXISTS / IF NOT EXISTS so they are idempotent;
    # the count query must also tolerate the column being already removed.
    import sqlalchemy as _sa

    conn = op.get_bind()
    secret_hash_exists = conn.execute(
        _sa.text(
            "SELECT EXISTS ("
            "  SELECT 1 FROM information_schema.columns"
            "  WHERE table_schema = 'public' AND table_name = 'webhooks'"
            "  AND column_name = 'secret_hash'"
            ")"
        )
    ).scalar()

    if secret_hash_exists:
        row = conn.execute(
            # noqa: S608 — not user-supplied SQL
            _sa.text(
                "SELECT COUNT(*) AS n FROM public.webhooks WHERE secret_hash IS NOT NULL"
            )
        ).fetchone()
        affected = row[0] if row else 0
        if affected:
            logger.warning(
                "core_100 upgrade: %d webhook row(s) had a non-NULL secret_hash. "
                "The original plaintext secret cannot be recovered from a SHA-256 hash. "
                "secret_encrypted will be NULL for these rows. "
                "Re-supply the secret via PUT /api/webhooks/{id} to re-enable signing.",
                affected,
            )

    # Drop the old one-way hash column.
    op.execute("ALTER TABLE IF EXISTS public.webhooks DROP COLUMN IF EXISTS secret_hash")

    # Add the new reversible encrypted column (BYTEA stores nonce||ciphertext||tag).
    op.execute("ALTER TABLE public.webhooks ADD COLUMN IF NOT EXISTS secret_encrypted BYTEA")

    # Grants (column-level privileges are not needed; table-level already covers it).
    for role in _ALL_RUNTIME_ROLES:
        _grant_best_effort("public.webhooks", _TABLE_PRIVILEGES, role)


def downgrade() -> None:
    # Restore the old column.  Encrypted values cannot be round-tripped back to
    # hashes so existing data is dropped on rollback (same loss as upgrade).
    op.execute("ALTER TABLE IF EXISTS public.webhooks DROP COLUMN IF EXISTS secret_encrypted")
    op.execute("ALTER TABLE IF EXISTS public.webhooks ADD COLUMN IF NOT EXISTS secret_hash TEXT")
