"""Payload and bookkeeping columns for the public MCP proposal importer.

Revision ID: rel_043
Revises: rel_042

RFC 0039 D13 step 2; REQ-public-mcp-proposals-002, -004, -005 and -006 (bu-y66o00.8).
The ``public_mcp_proposal_import`` job copies each inbox row from
``butlers_public.mcp_inbox.proposals`` into ``relationship.external_fact_proposals``
and validates it here, on the primary. The copied payload columns mirror the inbox
columns and their length bounds; ``imported_at`` and ``pending_action_id`` are the
importer's own bookkeeping.

Additive only; downgrade refuses while any imported payload or action link exists. Every
new column is nullable, so a status-only row (rel_041's shape)
stays valid; a CHECK keeps the seven payload columns all set or all NULL, and the
importer treats an all-NULL payload as ``invalid``. A ``parked`` row names its pending
action, and one action backs at most one proposal. The public MCP publication's
column list (``proposal_id``, ``state``, ``decided_at``) and the replica identity
(the primary key) are unchanged, so nothing new replicates to the projection.

The indexes serve the importer's lookups: the earliest proposal under one
``(sub, cid, idempotency_key)`` (conflict), the per-``(sub, cid)`` day count and the
parked count (rate limits), the ``imported`` and decidable work queues, and the
30-day clearing of ``note`` and ``evidence_url``.
"""

from __future__ import annotations

from alembic import op

revision = "rel_043"
down_revision = "rel_042"
branch_labels = None
depends_on = None

_TABLE = "relationship.external_fact_proposals"

_PAYLOAD = (
    "sub",
    "cid",
    "idempotency_key",
    "subject_entity_id",
    "predicate",
    "object_entity_id",
    "received_at",
)

# Every object this revision creates, in creation order; downgrade drops them in reverse.
_COLUMNS = (
    ("sub", "TEXT"),
    ("cid", "TEXT"),
    ("idempotency_key", "TEXT"),
    ("subject_entity_id", "UUID"),
    ("predicate", "TEXT"),
    ("object_entity_id", "UUID"),
    ("received_at", "TIMESTAMPTZ"),
    ("imported_at", "TIMESTAMPTZ"),
    ("pending_action_id", "UUID"),
)
_CHECKS = (
    ("external_fact_proposals_sub_length", "length(sub) BETWEEN 1 AND 256"),
    ("external_fact_proposals_cid_length", "length(cid) BETWEEN 1 AND 256"),
    ("external_fact_proposals_idempotency_key_length", "length(idempotency_key) BETWEEN 1 AND 64"),
    ("external_fact_proposals_predicate_length", "length(predicate) BETWEEN 1 AND 128"),
    ("external_fact_proposals_evidence_url_length", "length(evidence_url) <= 2048"),
    (
        "external_fact_proposals_payload_complete",
        f"num_nulls({', '.join(_PAYLOAD)}) IN (0, {len(_PAYLOAD)})",
    ),
    ("external_fact_proposals_parked_action", "state <> 'parked' OR pending_action_id IS NOT NULL"),
)
_INDEXES = (
    (
        "ux_external_fact_proposals_action",
        "CREATE UNIQUE INDEX IF NOT EXISTS {name} ON {table} (pending_action_id) "
        "WHERE pending_action_id IS NOT NULL",
    ),
    (
        "ix_external_fact_proposals_key_order",
        "CREATE INDEX IF NOT EXISTS {name} "
        "ON {table} (sub, cid, idempotency_key, received_at, proposal_id)",
    ),
    (
        "ix_external_fact_proposals_client_day",
        "CREATE INDEX IF NOT EXISTS {name} ON {table} (sub, cid, received_at)",
    ),
    (
        "ix_external_fact_proposals_client_parked",
        "CREATE INDEX IF NOT EXISTS {name} ON {table} (sub, cid) WHERE state = 'parked'",
    ),
    (
        "ix_external_fact_proposals_imported",
        "CREATE INDEX IF NOT EXISTS {name} ON {table} (received_at, proposal_id) "
        "WHERE state = 'imported'",
    ),
    (
        "ix_external_fact_proposals_decidable",
        "CREATE INDEX IF NOT EXISTS {name} ON {table} (pending_action_id) "
        "WHERE state IN ('parked', 'apply_failed')",
    ),
    (
        "ix_external_fact_proposals_retention",
        "CREATE INDEX IF NOT EXISTS {name} ON {table} (decided_at) "
        "WHERE note IS NOT NULL OR evidence_url IS NOT NULL",
    ),
)


def upgrade() -> None:
    """Re-run safe: each object is created if absent, and each CHECK is replaced in place."""
    op.execute(
        f"ALTER TABLE {_TABLE} "
        + ", ".join(f"ADD COLUMN IF NOT EXISTS {name} {kind}" for name, kind in _COLUMNS)
    )
    # Dropping and re-adding in one statement keeps a re-run from failing on an existing
    # constraint and from keeping a stale definition under the same name.
    op.execute(
        f"ALTER TABLE {_TABLE} "
        + ", ".join(
            f"DROP CONSTRAINT IF EXISTS {name}, ADD CONSTRAINT {name} CHECK ({check})"
            for name, check in _CHECKS
        )
    )
    for name, statement in _INDEXES:
        op.execute(statement.format(name=name, table=_TABLE))


def downgrade() -> None:
    """Refuse to drop imported payloads or action links; otherwise remove all, in reverse."""
    op.execute(f"""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM {_TABLE}
                 WHERE sub IS NOT NULL OR pending_action_id IS NOT NULL
            ) THEN
                RAISE EXCEPTION
                    'Cannot downgrade rel_043 while imported proposals exist';
            END IF;
        END $$
    """)
    for name, _statement in reversed(_INDEXES):
        op.execute(f"DROP INDEX IF EXISTS relationship.{name}")
    op.execute(
        f"ALTER TABLE {_TABLE} "
        + ", ".join(
            [f"DROP CONSTRAINT IF EXISTS {name}" for name, _check in reversed(_CHECKS)]
            + [f"DROP COLUMN IF EXISTS {name}" for name, _kind in reversed(_COLUMNS)]
        )
    )
