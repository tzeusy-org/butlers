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


def upgrade() -> None:
    op.execute(f"""
        ALTER TABLE {_TABLE}
            ADD COLUMN IF NOT EXISTS sub TEXT,
            ADD COLUMN IF NOT EXISTS cid TEXT,
            ADD COLUMN IF NOT EXISTS idempotency_key TEXT,
            ADD COLUMN IF NOT EXISTS subject_entity_id UUID,
            ADD COLUMN IF NOT EXISTS predicate TEXT,
            ADD COLUMN IF NOT EXISTS object_entity_id UUID,
            ADD COLUMN IF NOT EXISTS received_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS imported_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS pending_action_id UUID
    """)
    op.execute(f"""
        ALTER TABLE {_TABLE}
            ADD CONSTRAINT external_fact_proposals_sub_length
                CHECK (length(sub) BETWEEN 1 AND 256),
            ADD CONSTRAINT external_fact_proposals_cid_length
                CHECK (length(cid) BETWEEN 1 AND 256),
            ADD CONSTRAINT external_fact_proposals_idempotency_key_length
                CHECK (length(idempotency_key) BETWEEN 1 AND 64),
            ADD CONSTRAINT external_fact_proposals_predicate_length
                CHECK (length(predicate) BETWEEN 1 AND 128),
            ADD CONSTRAINT external_fact_proposals_evidence_url_length
                CHECK (length(evidence_url) <= 2048),
            ADD CONSTRAINT external_fact_proposals_payload_complete
                CHECK (num_nulls({", ".join(_PAYLOAD)}) IN (0, {len(_PAYLOAD)})),
            ADD CONSTRAINT external_fact_proposals_parked_action
                CHECK (state <> 'parked' OR pending_action_id IS NOT NULL)
    """)
    op.execute(f"""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_external_fact_proposals_action
            ON {_TABLE} (pending_action_id) WHERE pending_action_id IS NOT NULL
    """)
    op.execute(f"""
        CREATE INDEX IF NOT EXISTS ix_external_fact_proposals_key_order
            ON {_TABLE} (sub, cid, idempotency_key, received_at, proposal_id)
    """)
    op.execute(f"""
        CREATE INDEX IF NOT EXISTS ix_external_fact_proposals_client_day
            ON {_TABLE} (sub, cid, received_at)
    """)
    op.execute(f"""
        CREATE INDEX IF NOT EXISTS ix_external_fact_proposals_client_parked
            ON {_TABLE} (sub, cid) WHERE state = 'parked'
    """)
    op.execute(f"""
        CREATE INDEX IF NOT EXISTS ix_external_fact_proposals_imported
            ON {_TABLE} (received_at, proposal_id) WHERE state = 'imported'
    """)
    op.execute(f"""
        CREATE INDEX IF NOT EXISTS ix_external_fact_proposals_decidable
            ON {_TABLE} (pending_action_id) WHERE state IN ('parked', 'apply_failed')
    """)
    op.execute(f"""
        CREATE INDEX IF NOT EXISTS ix_external_fact_proposals_retention
            ON {_TABLE} (decided_at)
            WHERE note IS NOT NULL OR evidence_url IS NOT NULL
    """)


def downgrade() -> None:
    """Refuse to drop imported payloads or action links; otherwise remove the columns."""
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
    for index in (
        "ix_external_fact_proposals_retention",
        "ix_external_fact_proposals_decidable",
        "ix_external_fact_proposals_imported",
        "ix_external_fact_proposals_client_parked",
        "ix_external_fact_proposals_client_day",
        "ix_external_fact_proposals_key_order",
        "ux_external_fact_proposals_action",
    ):
        op.execute(f"DROP INDEX IF EXISTS relationship.{index}")
    op.execute(f"""
        ALTER TABLE {_TABLE}
            DROP CONSTRAINT IF EXISTS external_fact_proposals_parked_action,
            DROP CONSTRAINT IF EXISTS external_fact_proposals_payload_complete,
            DROP CONSTRAINT IF EXISTS external_fact_proposals_evidence_url_length,
            DROP CONSTRAINT IF EXISTS external_fact_proposals_predicate_length,
            DROP CONSTRAINT IF EXISTS external_fact_proposals_idempotency_key_length,
            DROP CONSTRAINT IF EXISTS external_fact_proposals_cid_length,
            DROP CONSTRAINT IF EXISTS external_fact_proposals_sub_length,
            DROP COLUMN IF EXISTS pending_action_id,
            DROP COLUMN IF EXISTS imported_at,
            DROP COLUMN IF EXISTS received_at,
            DROP COLUMN IF EXISTS object_entity_id,
            DROP COLUMN IF EXISTS predicate,
            DROP COLUMN IF EXISTS subject_entity_id,
            DROP COLUMN IF EXISTS idempotency_key,
            DROP COLUMN IF EXISTS cid,
            DROP COLUMN IF EXISTS sub
    """)
