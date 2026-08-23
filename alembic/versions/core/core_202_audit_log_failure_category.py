"""audit_log: persist the credential failure category as its own column.

Revision ID: core_202
Revises: core_201
Create Date: 2026-08-23 00:00:00.000000

Adds ``public.audit_log.failure_category`` so a credential-target audit-error
group can be identified by its **cause** without any reader touching free text.

Why a column and not a parse
----------------------------
bu-uqipv made credential-target audit groups content-blind by building the
group title from ``action`` + ``target`` alone, which folds every cause on one
credential into a single group. The cause was never a column: for the two probe
endpoints it survived only inside the ``note`` free text as
``probe_status=<token>``, and the category itself was derived at *response*
time from that token plus the provider's HTTP status code, which is never
persisted. Recovering it on read would mean substring-parsing exactly the free
text owner Option C withholds. Writing it at INSERT time removes the question.

What may be stored
------------------
Only a member of ``PROBE_FAILURE_VOCABULARY``
(``butlers.api.models.audit``): ``not_set``, ``expired``, ``rejected``,
``rate_limited``, ``provider_error``, ``malformed``, ``unverified``, ``other``.
Never a raw ``probe_status`` token (``live_failed:403``), never the provider's
HTTP status code, never any provider or audit free text.

The CHECK constraint below is the structural half of that guarantee: the
application clamps to the vocabulary on the way in
(``butlers.api.models.audit.clamp_failure_category``), and the database refuses
anything else even from a writer that bypasses ``audit.append()``. The
vocabulary is inlined here rather than imported, because a migration is a frozen
snapshot of the schema at one revision; ``tests/api/test_audit_grouping_
credential_blind_db.py`` asserts the constraint's allowed set still equals the
live ``PROBE_FAILURE_VOCABULARY``, so widening the vocabulary without a
follow-up migration fails a test rather than silently rejecting rows in prod.

Historic rows
-------------
The column is nullable with no default and is **not** backfilled. Pre-existing
rows keep ``NULL`` and keep grouping exactly as they do today, under the
uncategorised title bu-uqipv established. Backfilling would mean parsing the
withheld ``note`` text, which is the inversion this change exists to prevent.
"""

from __future__ import annotations

from alembic import op

revision = "core_202"
down_revision = "core_201"
branch_labels = None
depends_on = None

#: Mirror of ``butlers.api.models.audit.PROBE_FAILURE_VOCABULARY`` frozen at
#: this revision. Kept in sync by a test, not by an import (see the module
#: docstring).
_VOCABULARY = (
    "not_set",
    "expired",
    "rejected",
    "rate_limited",
    "provider_error",
    "malformed",
    "unverified",
    "other",
)

_ALLOWED_SQL = ", ".join(f"'{member}'" for member in _VOCABULARY)


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE public.audit_log
            ADD COLUMN IF NOT EXISTS failure_category TEXT
        """
    )
    # Idempotent: ADD CONSTRAINT has no IF NOT EXISTS, so guard on the catalog.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'audit_log_failure_category_vocabulary'
                  AND conrelid = 'public.audit_log'::regclass
            ) THEN
                ALTER TABLE public.audit_log
                    ADD CONSTRAINT audit_log_failure_category_vocabulary
                    CHECK (failure_category IS NULL OR failure_category IN ({_ALLOWED_SQL}));
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE public.audit_log
            DROP CONSTRAINT IF EXISTS audit_log_failure_category_vocabulary
        """
    )
    op.execute(
        """
        ALTER TABLE public.audit_log
            DROP COLUMN IF EXISTS failure_category
        """
    )
