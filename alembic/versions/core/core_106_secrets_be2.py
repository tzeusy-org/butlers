"""secrets BE-2: test-state columns on butler_secrets and public.entity_info.

Revision ID: core_106
Revises: core_105
Create Date: 2026-05-25 00:00:00.000000

Implements the Test-State Columns requirement from
``openspec/changes/redesign-secrets-passport/specs/core-credentials/spec.md``
(§ Test-State Columns on Credential Tables).

Four columns are added to:
  1. ``<schema>.butler_secrets`` in every per-butler schema that already has
     the table (dynamically discovered via ``pg_class``).
  2. ``public.entity_info`` — the cross-butler entity metadata table.

New columns
-----------
last_verified     TIMESTAMPTZ  NULL    Timestamp of most recent successful probe.
                                       Set to ``now()`` on probe success;
                                       left unchanged on probe failure.
last_test_ok      BOOLEAN      NULL    Outcome of most recent probe.
                                       NULL = never probed.
last_test_code    INTEGER      NULL    HTTP / provider response code from most
                                       recent probe.
last_test_message TEXT         NULL    Verbatim error tail from most recent probe
                                       (truncated to 512 chars by the application).

All four columns are nullable so existing rows remain valid after migration
(NULL = never probed / no test-state yet).  No backfill is performed.

The columns are ordinary writable columns (not generated/computed), satisfying
the cache-write-on-probe contract: a probe endpoint can UPDATE all four in the
same transaction that inserts the ``public.secret_probe_log`` row.

All DDL uses ``IF NOT EXISTS`` (ADD COLUMN) or ``IF EXISTS`` (DROP COLUMN)
for idempotency.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "core_106"
down_revision = "core_105"
branch_labels = None
depends_on = None

# Ordered list of (column_name, SQL type fragment) to add/drop.
_TEST_STATE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("last_verified", "TIMESTAMPTZ"),
    ("last_test_ok", "BOOLEAN"),
    ("last_test_code", "INTEGER"),
    ("last_test_message", "TEXT"),
)


def _butler_secrets_schemas() -> list[str]:
    """Return schema names that have a ``butler_secrets`` table.

    Uses ``pg_class`` / ``pg_namespace`` to discover schemas dynamically so
    that the migration is correct even when the butler roster grows after this
    revision ships.  Excludes ``pg_catalog`` and ``information_schema``.
    """
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            """
            SELECT DISTINCT n.nspname
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relname = 'butler_secrets'
              AND c.relkind = 'r'
              AND n.nspname NOT IN ('pg_catalog', 'information_schema', 'public')
            ORDER BY n.nspname
            """
        )
    ).fetchall()
    return [row[0] for row in rows]


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Per-butler butler_secrets: add test-state columns
    # ------------------------------------------------------------------
    for schema in _butler_secrets_schemas():
        add_cols = ",\n                ".join(
            f"ADD COLUMN IF NOT EXISTS {col} {sql_type}" for col, sql_type in _TEST_STATE_COLUMNS
        )
        op.execute(
            f"""
            ALTER TABLE {schema}.butler_secrets
                {add_cols}
            """
        )

    # ------------------------------------------------------------------
    # 2. public.entity_info: add test-state columns
    # ------------------------------------------------------------------
    add_cols = ",\n            ".join(
        f"ADD COLUMN IF NOT EXISTS {col} {sql_type}" for col, sql_type in _TEST_STATE_COLUMNS
    )
    op.execute(
        f"""
        ALTER TABLE public.entity_info
            {add_cols}
        """
    )


def downgrade() -> None:
    # ------------------------------------------------------------------
    # 2. public.entity_info: drop test-state columns (reverse order)
    # ------------------------------------------------------------------
    drop_cols = ",\n            ".join(
        f"DROP COLUMN IF EXISTS {col}" for col, _ in reversed(_TEST_STATE_COLUMNS)
    )
    op.execute(
        f"""
        ALTER TABLE public.entity_info
            {drop_cols}
        """
    )

    # ------------------------------------------------------------------
    # 1. Per-butler butler_secrets: drop test-state columns
    # ------------------------------------------------------------------
    for schema in _butler_secrets_schemas():
        drop_cols = ",\n                ".join(
            f"DROP COLUMN IF EXISTS {col}" for col, _ in reversed(_TEST_STATE_COLUMNS)
        )
        op.execute(
            f"""
            ALTER TABLE {schema}.butler_secrets
                {drop_cols}
            """
        )
