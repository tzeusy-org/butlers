"""Store owner-authored standing-reply template drafts outside active rules.

Revision ID: approvals_019
Revises: approvals_018
Create Date: 2026-11-02 00:00:00.000000

A draft is an inactive, typed representation of a standing reply (bu-jv1moa.10):
the send tool, one canonical typed template per content-bearing argument, and
the resolved recipient entities it is pinned to.  It lives in its own table so
the approval gate, which reads only ``approval_rules``, can never match, claim
or execute it.  The table deliberately has no ``active`` column and no link to
``approval_rules``: activation is a later, separately adopted contract
(bu-jv1moa.28), not a flag flip.

``revision`` is the optimistic-concurrency token for owner edits.
``idempotency_key`` plus the immutable ``create_request_digest`` (SHA-256 of the
canonical create request) make a replayed create return the draft that key
created, even after it was edited or revoked; the same key with a different
request is a conflict.  Revoke
marks a draft unavailable and keeps the row as audit history.

Downgrade drops the table, which would destroy owner-authored drafts, so it
refuses while any draft exists (like approvals_018).
"""

from __future__ import annotations

from alembic import op

revision = "approvals_019"
down_revision = "approvals_018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS standing_reply_template_drafts (
            id UUID PRIMARY KEY,
            tool_name TEXT NOT NULL,
            content_templates JSONB NOT NULL,
            recipient_entity_ids UUID[] NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'draft',
            revision INTEGER NOT NULL DEFAULT 1,
            idempotency_key TEXT NOT NULL,
            create_request_digest TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            revoked_at TIMESTAMPTZ,
            CONSTRAINT standing_reply_template_drafts_status_check
                CHECK (status IN ('draft', 'revoked')),
            CONSTRAINT standing_reply_template_drafts_revision_check
                CHECK (revision >= 1),
            CONSTRAINT standing_reply_template_drafts_recipients_check
                CHECK (cardinality(recipient_entity_ids) >= 1),
            CONSTRAINT standing_reply_template_drafts_revoked_check
                CHECK ((status = 'revoked') = (revoked_at IS NOT NULL)),
            CONSTRAINT standing_reply_template_drafts_idempotency_key_key
                UNIQUE (idempotency_key)
        )
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('standing_reply_template_drafts') IS NOT NULL THEN
                LOCK TABLE standing_reply_template_drafts IN ACCESS EXCLUSIVE MODE;
                IF EXISTS (SELECT 1 FROM standing_reply_template_drafts) THEN
                    RAISE EXCEPTION '%',
                        'Cannot downgrade approvals_019 while standing-reply '
                        || 'template drafts exist';
                END IF;
            END IF;
        END
        $$
        """
    )
    op.execute("DROP TABLE IF EXISTS standing_reply_template_drafts")
