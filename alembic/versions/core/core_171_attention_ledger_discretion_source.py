"""attention_ledger: add 'discretion' to the source CHECK constraint.

Revision ID: core_171
Revises: core_170
Create Date: 2026-07-14 00:00:00.000000

Numbering note: chain head at authoring time was core_170 (qa_findings
infra_state source). The ``core`` chain is contended -- if a sibling PR claims
core_171 first, rechain this revision's ``down_revision`` onto the new head and
renumber (git mv), per the repo's duplicate-revision-collision lore (see
core_160's / core_168's renumbering sagas).

bu-5go3y.

``public.attention_ledger`` (core_160) is the fleet's single honesty surface
for "attention that was intended/expected but never reached the owner". Its
``source`` CHECK constraint admitted only the two proactive-EGRESS choke
points -- ``'notify'`` (the ``notify()`` owner-page gate) and ``'insight'``
(the insight-delivery-cycle). But the connector discretion layer
(``butlers.connectors.discretion``) has an INBOUND honesty gap of exactly the
same shape: when same-tier model failover exhausts, ``DiscretionEvaluator``
falls back to the weight-default IGNORE verdict -- a degraded, fabricated
suppression, not a model-judged decision -- and silently drops a message that
would otherwise have been forwarded. Today that is observable only via an
ERROR log + the ``discretion_evaluations_total`` metric; it cannot be recorded
on the ledger because the source CHECK rejects it.

This migration widens the source CHECK to accept ``'discretion'``; the
application-code writer ships in the same PR (see
``butlers.core.attention_ledger``'s ``Source`` literal and
``butlers.connectors.discretion.DiscretionEvaluator``). Only the
failover-exhausted weight-default fallback is recorded (classify-before-
flagging) -- genuine model-evaluated IGNORE verdicts are legitimate decisions,
not honesty gaps, and are never written to the ledger.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_171"
down_revision = "core_170"
branch_labels = None
depends_on = None

_OLD_SOURCES = ("notify", "insight")
_NEW_SOURCES = (*_OLD_SOURCES, "discretion")


def upgrade() -> None:
    op.execute("""
        ALTER TABLE public.attention_ledger
        DROP CONSTRAINT IF EXISTS chk_attention_ledger_source
    """)
    op.execute(f"""
        ALTER TABLE public.attention_ledger
        ADD CONSTRAINT chk_attention_ledger_source
        CHECK (source IN ({", ".join(f"'{s}'" for s in _NEW_SOURCES)}))
    """)


def downgrade() -> None:
    # Existing 'discretion' rows would violate the narrower constraint -- delete
    # them so the downgrade never leaves the table in a state the old
    # constraint rejects. These are audit-only observability rows (a suppressed
    # inbound message that was never forwarded); they carry no downstream FK or
    # delivery state, so dropping them on downgrade is safe.
    op.execute("""
        DELETE FROM public.attention_ledger
        WHERE source = 'discretion'
    """)
    op.execute("""
        ALTER TABLE public.attention_ledger
        DROP CONSTRAINT IF EXISTS chk_attention_ledger_source
    """)
    op.execute(f"""
        ALTER TABLE public.attention_ledger
        ADD CONSTRAINT chk_attention_ledger_source
        CHECK (source IN ({", ".join(f"'{s}'" for s in _OLD_SOURCES)}))
    """)
