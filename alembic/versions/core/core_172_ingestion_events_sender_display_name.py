"""Add source_sender_display_name to public.ingestion_events.

Revision ID: core_172
Revises: core_171
Create Date: 2026-07-14 00:00:00.000000

Numbering note: chain head at authoring time was core_171 (attention_ledger
discretion source). The ``core`` chain is contended -- if a sibling PR claims
core_172 first, rechain this revision's ``down_revision`` onto the new head and
renumber (git mv), per the repo's duplicate-revision-collision lore (see
core_160's / core_168's renumbering sagas).

bu-vs9cr.

Motivation
----------
``public.ingestion_events.source_sender_identity`` is normalized to a bare
lowercased address at ingest (bu-qeaou), discarding the raw ``From:`` display
name. Downstream identity enrichment
(``relationship_jobs.run_email_identity_enrichment``) therefore *guesses* a
display name from the address local-part
(``derive_display_name_from_address``) -- ``hsbc.bank.singapore.limited@`` ->
``'Hsbc Bank Singapore Limited'``. This nullable, additive column captures the
real display name at ingest so enrichment can prefer it and only fall back to
the local-part heuristic for historical rows.

``public.ingestion_events`` is a cross-butler public table read by multiple
butler roles; the column is nullable and additive so existing rows and every
non-email ingress path stay valid with a NULL value. Historical rows cannot be
backfilled (the name was already discarded at ingest) and remain NULL.

Reversibility
-------------
The downgrade drops the column cleanly. No data migration is needed on
downgrade since source_sender_display_name is a nullable, additive field with
no downstream FK or constraint.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_172"
down_revision = "core_171"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE public.ingestion_events
        ADD COLUMN IF NOT EXISTS source_sender_display_name TEXT
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE public.ingestion_events
        DROP COLUMN IF EXISTS source_sender_display_name
        """
    )
