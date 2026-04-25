"""owntracks_points: durable OwnTracks location evidence table.

Revision ID: core_081
Revises: core_080
Create Date: 2026-04-25 00:00:00.000000

Creates ``connectors.owntracks_points`` — the long-retention evidence table
that Chronicler's OwnTracks projection adapter reads.

Unlike ``public.ingestion_events`` (audit record, no raw payload) and
``connectors.filtered_events`` (monthly-partitioned, short retention),
this table:

  - Persists indefinitely (no partitioning / TTL by default).
  - Carries the full GPS coordinate triple (ts, lat, lon).
  - Carries a stable, deterministic idempotency key.
  - Carries source references needed for Chronicler provenance.
  - Is written by the OwnTracks connector via connector_writer role.
  - Is readable by ``butler_chronicler_rw`` for Tier-0 projection.

Schema design notes
-------------------
``idempotency_key`` is the canonical replay-safety handle.  It follows
the same format as the ingest-envelope's ``control.idempotency_key``:
  ``owntracks:<endpoint_identity>:<tst>:location``

``accuracy`` (acc) is nullable — OwnTracks includes it on most payloads
but it is optional in the OwnTracks protocol.

``trigger`` maps to the OwnTracks ``t`` field (e.g. ``p``, ``c``, ``r``,
``u``, ``t``, ``a``) and is nullable since older app versions omit it.

``event`` is relevant for transition events; for location payloads it is
always NULL.

``raw_payload`` is a JSONB snapshot of the full webhook body for archival
and forensic use.  Nullable so connectors that cannot reconstruct the
payload can omit it.

RFC 0014 §D8 requires explicit grant inclusion here.
"""

from __future__ import annotations

from alembic import op

revision = "core_081"
down_revision = "core_080"
branch_labels = None
depends_on = None

_CONNECTOR_ROLE = "connector_writer"
_CHRONICLER_ROLE = "butler_chronicler_rw"
_SCHEMA = "connectors"
_TABLE = "owntracks_points"
_FULL_TABLE = f"{_SCHEMA}.{_TABLE}"


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _execute_best_effort(statement: str, *, role_name: str | None = None) -> None:
    """Execute a DDL statement only when the prerequisite role exists.

    Silently skips if the role is missing (non-prod DB without all roles).
    """
    if role_name is not None:
        condition = f"EXISTS (SELECT 1 FROM pg_roles WHERE rolname = {_quote_literal(role_name)})"
    else:
        condition = "TRUE"
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
    # -------------------------------------------------------------------------
    # 1. Create connectors.owntracks_points
    # -------------------------------------------------------------------------
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS {_FULL_TABLE} (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            idempotency_key     TEXT NOT NULL UNIQUE,
            ts                  TIMESTAMPTZ NOT NULL,
            lat                 DOUBLE PRECISION NOT NULL,
            lon                 DOUBLE PRECISION NOT NULL,
            accuracy            DOUBLE PRECISION,
            trigger             TEXT,
            event               TEXT,
            endpoint_identity   TEXT NOT NULL,
            raw_payload         JSONB,
            recorded_at         TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # ── Indexes ──────────────────────────────────────────────────────────────
    op.execute(f"""
        CREATE INDEX IF NOT EXISTS ix_owntracks_points_endpoint_ts
            ON {_FULL_TABLE} (endpoint_identity, ts DESC)
    """)
    op.execute(f"""
        CREATE INDEX IF NOT EXISTS ix_owntracks_points_ts
            ON {_FULL_TABLE} (ts DESC)
    """)

    # -------------------------------------------------------------------------
    # 2. Grants
    # -------------------------------------------------------------------------

    # connector_writer: full DML on the table (write path).
    _execute_best_effort(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {_FULL_TABLE}"
        f" TO {_quote_ident(_CONNECTOR_ROLE)}",
        role_name=_CONNECTOR_ROLE,
    )

    # butler_chronicler_rw: read-only (projection path, RFC 0014 §D1).
    # The schema USAGE grant is already in place from init-db.sql; the
    # per-table grant is what RFC 0014 §D8 requires here.
    _execute_best_effort(
        f"GRANT SELECT ON TABLE {_FULL_TABLE} TO {_quote_ident(_CHRONICLER_ROLE)}",
        role_name=_CHRONICLER_ROLE,
    )


def downgrade() -> None:
    op.execute(f"DROP TABLE IF EXISTS {_FULL_TABLE} CASCADE")
