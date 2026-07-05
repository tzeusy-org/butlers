"""token_usage_ledger_purpose: add a purpose dimension to spend attribution.

Revision ID: core_156
Revises: core_155
Create Date: 2026-07-05 00:00:00.000000

Part of bu-qvnce.12 (2026-07-04 JARVIS pursuit, move 12 — API-direct
inference lane + purpose-tagged spend attribution).

``public.token_usage_ledger`` already carries ``butler_name`` (who spent) and
(after core_155) cache-aware token buckets (what was spent), but nothing
records *why* the call happened. The highest-volume, cheapest-per-call
sources (switchboard classification, connector discretion screening) write
either an operationally-meaningless ``butler_name`` ("__discretion__") or a
misleading one (the routing-decision session shares ``butler_name`` with the
butler being routed *to*), so ``/spend`` cannot separate "this butler did
real work" from "the switchboard decided where to send a message" or "a
connector screened an inbound message for discretion".

``purpose`` is an additive, nullable TEXT column: existing rows keep NULL
(unknown/pre-migration), no CHECK constraint is added (the vocabulary is an
evolving, code-owned set — see ``butlers.core.model_routing.record_token_usage``
callers) mirroring how ``trigger_source`` itself has no DB-level enum, only
an application-level allow-list (``butlers.core.sessions.TRIGGER_SOURCES``).

Write paths updated to stamp ``purpose`` in the same change:
- ``core.spawner._run()`` — stamps ``purpose=trigger_source`` (the same
  granular category already used for spend-rule ``trigger`` conditions:
  route/schedule/tick/classification/healing/dashboard/qa/external/trigger).
- ``connectors.discretion_dispatcher.DiscretionDispatcher.call()`` — stamps
  ``purpose="discretion"`` and additionally replaces the fixed
  ``butler_name="__discretion__"`` identity with the per-connector identity
  the evaluator already tracks (``DiscretionEvaluator._source``, e.g.
  ``"tg:<chat_id>"``) when one is supplied.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_156"
down_revision = "core_155"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE public.token_usage_ledger
            ADD COLUMN IF NOT EXISTS purpose TEXT NULL
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE public.token_usage_ledger
            DROP COLUMN IF EXISTS purpose
        """
    )
