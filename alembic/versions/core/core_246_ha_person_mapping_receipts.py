"""Durable content-blind receipts for dashboard HA person mapping batches.

Revision ID: core_246
Revises: core_245
Create Date: 2026-09-22 00:00:00.000000

The receipt table deliberately stores only digests, an opaque receipt, aggregate
counts, and fixed outcome categories.  Home Assistant IDs, entity UUIDs, raw
idempotency keys, and request bodies have no column in this representation.
"""

from __future__ import annotations

from alembic import op

revision = "core_246"
down_revision = "core_245"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE public.ha_person_mapping_receipts (
            key_digest                 BYTEA PRIMARY KEY,
            request_digest             BYTEA NOT NULL,
            receipt                    UUID NOT NULL UNIQUE,
            complete                   BOOLEAN NOT NULL,
            received_count             SMALLINT NOT NULL,
            created_count              SMALLINT NOT NULL,
            unchanged_count            SMALLINT NOT NULL,
            conflict_count             SMALLINT NOT NULL,
            invalid_reference_count    SMALLINT NOT NULL,
            outcome                    TEXT NOT NULL,
            failure_category           TEXT,
            created_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_ha_person_mapping_receipts_outcome
                CHECK (outcome IN ('success', 'refused')),
            CONSTRAINT ck_ha_person_mapping_receipts_failure
                CHECK (failure_category IS NULL OR failure_category IN (
                    'reference_invalid', 'mapping_conflict'
                )),
            CONSTRAINT ck_ha_person_mapping_receipts_counts
                CHECK (
                    received_count BETWEEN 1 AND 50
                    AND created_count BETWEEN 0 AND received_count
                    AND unchanged_count BETWEEN 0 AND received_count
                    AND conflict_count BETWEEN 0 AND received_count
                    AND invalid_reference_count BETWEEN 0 AND received_count
                )
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public.ha_person_mapping_receipts")
