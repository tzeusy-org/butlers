"""Add test-state columns to public.butler_secrets (shared credential pool).

Revision ID: core_117
Revises: core_116
Create Date: 2026-06-11 00:00:00.000000

Motivation (bu-urcwx)
---------------------
core_106 added the four test-state columns (last_verified, last_test_ok,
last_test_code, last_test_message) to ``<schema>.butler_secrets`` in every
per-butler schema and to ``public.entity_info`` — but its schema discovery
explicitly excluded ``public``, so the shared credential pool
(``public.butler_secrets``, created at daemon boot by
``ensure_secrets_schema``) never received them.

The /secrets inventory shared-pool scan (PR #2135) reuses
``_fetch_system_secrets``, which SELECTs all four columns.  On databases
where the shared pool predates this revision the query fails with
UndefinedColumnError and the scan silently returns nothing, hiding every
shared-pool secret (Google OAuth app credentials, email/telegram tokens,
S3, …) from the passport's System family.

This revision brings ``public.butler_secrets`` in line with the per-butler
tables.  The companion code change adds the columns to
``_SECRETS_TABLE_DDL`` in ``butlers/credential_store.py`` so fresh databases
get them at creation time.

The table is created first with ``CREATE TABLE IF NOT EXISTS`` (matching the
``ensure_secrets_schema`` DDL) so the migration is correct on fresh databases
where alembic runs before any daemon boot.  All column DDL uses
``IF NOT EXISTS`` / ``IF EXISTS`` for idempotency, mirroring core_106.
"""

from __future__ import annotations

from alembic import op

revision = "core_117"
down_revision = "core_116"
branch_labels = None
depends_on = None

# Ordered list of (column_name, SQL type fragment) to add/drop — identical to
# the set core_106 applied to the per-butler schemas.
_TEST_STATE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("last_verified", "TIMESTAMPTZ"),
    ("last_test_ok", "BOOLEAN"),
    ("last_test_code", "INTEGER"),
    ("last_test_message", "TEXT"),
)

# Base table DDL kept in sync with ensure_secrets_schema's _SECRETS_TABLE_DDL
# (pre-this-revision shape; the columns are added separately below so the
# migration also repairs existing tables).
_CREATE_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS public.butler_secrets (
    secret_key   TEXT PRIMARY KEY,
    secret_value TEXT NOT NULL,
    category     TEXT NOT NULL DEFAULT 'general',
    description  TEXT,
    is_sensitive BOOLEAN NOT NULL DEFAULT true,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at   TIMESTAMPTZ
)
"""


def upgrade() -> None:
    op.execute(_CREATE_TABLE_DDL)
    add_cols = ",\n            ".join(
        f"ADD COLUMN IF NOT EXISTS {col} {sql_type}" for col, sql_type in _TEST_STATE_COLUMNS
    )
    op.execute(
        f"""
        ALTER TABLE public.butler_secrets
            {add_cols}
        """
    )


def downgrade() -> None:
    drop_cols = ",\n            ".join(
        f"DROP COLUMN IF EXISTS {col}" for col, _ in reversed(_TEST_STATE_COLUMNS)
    )
    op.execute(
        f"""
        ALTER TABLE public.butler_secrets
            {drop_cols}
        """
    )
