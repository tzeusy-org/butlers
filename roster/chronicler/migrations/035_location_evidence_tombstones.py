"""Forgotten-row receipts and location evidence tombstones (bu-s11n0s.7, slice 3).

Revision ID: chronicler_035
Revises: chronicler_034

When raw expiry lands (a later slice), each OwnTracks location point event it removes leaves a
minimal permanent record, and each run leaves one receipt. This revision creates both tables.
It deletes nothing and nothing writes them yet: ``location_retention.ENFORCEMENT_STATUS`` stays
``not_enforced``.

``location_forgetting_receipts`` holds one row per forgetting run: the policy version and cutoff
it ran under, the spatial scheme summaries were coarsened to and its nominal precision, and the
counts it committed (``point_events_forgotten`` counts tombstones the run itself added, so a
re-run never doubles it). Tombstones are written first and the receipt last, in one
transaction, so the tombstone's receipt key is checked at commit (``DEFERRABLE INITIALLY
DEFERRED``): a receipt never exists without its tombstones, nor a tombstone without its receipt.

``location_evidence_tombstones`` holds one row per forgotten point event: its former id, its
``source_ref`` (``connectors.owntracks_points:<idempotency key>``, an opaque source identity),
the receipt that forgot it, and the episodes it was evidence for. It stores no coordinates,
title, network name or payload. ``source_ref`` is unique, so the OwnTracks point projection can
refuse to write a forgotten source again: a replayed webhook, reset cursor or wider horizon
never restores a forgotten point.

Both tables are append-only. A trigger refuses ``UPDATE`` and ``DELETE`` (an init-db replay
re-grants schema-wide DML, so a grant alone would not hold). Grants follow chronicler_032:
``REVOKE ALL ... FROM PUBLIC`` and from ``butler_chronicler_rw``, then ``SELECT, INSERT`` to that
role. Tables and the index are ``IF NOT EXISTS`` and the guard is ``CREATE OR REPLACE``; the
triggers and grants are reapplied. So a re-run keeps every record and the guard.

Downgrade drops both tables. It cannot restore anything a later slice forgot.
"""

from __future__ import annotations

from alembic import op

revision = "chronicler_035"
down_revision = "chronicler_034"
branch_labels = None
depends_on = None

_ROLE = "butler_chronicler_rw"
_RECEIPTS = "location_forgetting_receipts"
_TOMBSTONES = "location_evidence_tombstones"
_GUARD = "chronicler.location_forgetting_append_only()"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_RECEIPTS} (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            policy_version INTEGER NOT NULL CHECK (policy_version > 0),
            cutoff TIMESTAMPTZ NOT NULL,
            spatial_scheme TEXT NOT NULL CHECK (spatial_scheme ~ '^[a-z0-9-]{{1,64}}$'),
            spatial_precision_m INTEGER NOT NULL CHECK (spatial_precision_m > 0),
            point_events_forgotten INTEGER NOT NULL CHECK (point_events_forgotten >= 0),
            episodes_coarsened INTEGER NOT NULL CHECK (episodes_coarsened >= 0),
            committed_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_TOMBSTONES} (
            point_event_id UUID PRIMARY KEY,
            source_ref TEXT NOT NULL UNIQUE
                CHECK (source_ref LIKE 'connectors.owntracks_points:%'),
            receipt_id UUID NOT NULL
                REFERENCES {_RECEIPTS} (id) DEFERRABLE INITIALLY DEFERRED,
            episode_ids UUID[] NOT NULL DEFAULT '{{}}',
            forgotten_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS {_TOMBSTONES}_receipt_idx ON {_TOMBSTONES} (receipt_id)"
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {_GUARD}
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, pg_temp
        AS $fn$
        BEGIN
            RAISE EXCEPTION 'location forgetting records are append-only'
                USING ERRCODE = '55000';
        END;
        $fn$
        """
    )
    for table in (_RECEIPTS, _TOMBSTONES):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table}")
        op.execute(
            f"""
            CREATE TRIGGER {table}_append_only
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION {_GUARD}
            """
        )
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC, {_ROLE}")
        op.execute(f"GRANT SELECT, INSERT ON {table} TO {_ROLE}")


def downgrade() -> None:
    op.execute(f"DROP TABLE IF EXISTS {_TOMBSTONES}")
    op.execute(f"DROP TABLE IF EXISTS {_RECEIPTS}")
    op.execute(f"DROP FUNCTION IF EXISTS {_GUARD}")
