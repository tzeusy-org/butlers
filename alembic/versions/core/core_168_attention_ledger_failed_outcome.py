"""attention_ledger: add 'failed' to the outcome CHECK constraint.

Revision ID: core_168
Revises: core_167
Create Date: 2026-07-12 00:00:00.000000

Numbering note: chain head at authoring time was core_166. PR #3170
(agent/bu-hmdqz.2) was in flight adding core_167 off the same head -- this
revision reserves core_168 rather than colliding on core_167, per the repo's
duplicate-revision-collision lore (core_160's own renumbering saga). PR #3170
merged first (core_167 is now on origin/main), so this revision rechains its
``down_revision`` onto ``core_167`` to keep the ``core`` chain single-headed.

Move 3/15 (2026-07-12 JARVIS pursuit) -- bu-hmdqz.3.

``public.attention_ledger`` (core_160) recorded every notify()/insight-
delivery-cycle egress decision as one of delivered/coalesced/deferred/
suppressed. Several callers (``butlers.jobs.secrets_lifecycle``,
``butlers.jobs.home``, ``butlers.jobs.decision_review``,
``butlers.core.fleet_halt_attention``) stamped genuine terminal failures --
no recipient configured, a delivery/transport error, an unexpected exception
-- as ``outcome='deferred'``, the same value used for a benign quiet-hours
hold that resolves on its own. That conflation let a real outage (e.g.
secrets_lifecycle's 164 delivery attempts / 0 delivered over dashboard-api's
loopback-URL transport bug) read as chosen, retrying discipline in the exact
surface built to prove silence is chosen. This migration widens the CHECK
constraint to accept 'failed'; the application-code caller migration ships in
the same PR (see ``butlers.core.attention_ledger``'s ``Outcome`` literal).
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_168"
down_revision = "core_167"
branch_labels = None
depends_on = None

_OLD_OUTCOMES = ("delivered", "coalesced", "deferred", "suppressed")
_NEW_OUTCOMES = (*_OLD_OUTCOMES, "failed")


def upgrade() -> None:
    op.execute("""
        ALTER TABLE public.attention_ledger
        DROP CONSTRAINT IF EXISTS chk_attention_ledger_outcome
    """)
    op.execute(f"""
        ALTER TABLE public.attention_ledger
        ADD CONSTRAINT chk_attention_ledger_outcome
        CHECK (outcome IN ({", ".join(f"'{o}'" for o in _NEW_OUTCOMES)}))
    """)


def downgrade() -> None:
    # Existing 'failed' rows would violate the narrower constraint -- fold
    # them back into 'deferred' (the pre-migration catch-all) so the
    # downgrade never leaves the table in a state the old constraint rejects.
    op.execute("""
        UPDATE public.attention_ledger
        SET outcome = 'deferred'
        WHERE outcome = 'failed'
    """)
    op.execute("""
        ALTER TABLE public.attention_ledger
        DROP CONSTRAINT IF EXISTS chk_attention_ledger_outcome
    """)
    op.execute(f"""
        ALTER TABLE public.attention_ledger
        ADD CONSTRAINT chk_attention_ledger_outcome
        CHECK (outcome IN ({", ".join(f"'{o}'" for o in _OLD_OUTCOMES)}))
    """)
