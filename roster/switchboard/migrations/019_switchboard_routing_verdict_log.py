"""Routing verdict mining substrate: switchboard.routing_verdict_log.

Revision ID: sw_019
Revises: sw_018
Create Date: 2026-07-06 00:00:00.000000

bu-aga08 (rule-promotion bead 1 of 7). Per
``docs/plans/2026-07-06-switchboard-rule-promotion-design.md`` section 1 and
the merged openspec change ``switchboard-rule-promotion``
(``openspec/changes/switchboard-rule-promotion/specs/switchboard-rule-promotion/spec.md``,
"Requirement: Routing Verdict Log").

Creates the first-class, queryable record of "the triage layer decided X for
sender Y, via mechanism Z" — durable per-sender history for both rule-bypassed
and LLM-classified traffic, independent of excavating per-butler
``sessions.tool_calls`` JSONB. This is the mining substrate only: no
promotion-trigger logic, no suggestions table, no approvals surface (those are
beads 2-4). Write hooks live in ``src/butlers/modules/pipeline.py`` (rule
bypass sites + LLM verdict resolution site) via
``roster/switchboard/tools/routing/verdict_log.py::record_routing_verdict``,
which is best-effort/never-raising per the ``public.attention_ledger``
degraded-honesty pattern (``src/butlers/core/attention_ledger.py``).

Table design notes:
  - ``ingestion_event_id`` FKs to ``public.ingestion_events`` (cross-schema;
    that table lives in the shared ``public`` schema created by the core
    migration chain, hence the explicit ``public.`` qualification — every
    other reference in this file is intra-schema/unqualified, matching this
    chain's existing convention, e.g. ``roster/switchboard/migrations/
    003_switchboard_routing.py``).
  - ``matched_rule_id`` FKs to this schema's own ``ingestion_rules`` table;
    set for ``verdict_source='rule'`` for an actual rule match, NULL for a
    rule-shaped bypass with no backing rule row (thread-affinity — see the
    write-hook module docstring), and always NULL for ``verdict_source in
    ('llm', 'pinned')``.
  - ``session_id`` FKs to this schema's own ``sessions`` table; set only for
    ``verdict_source in ('llm', 'spot_check')`` (spot-check lands in a later
    bead) since only those verdicts come from a spawned classification
    session.
  - No FK ``ON DELETE`` action is specified for either FK — this mirrors the
    sibling ``rule_promotion_suggestions`` design (bead 2) and keeps history
    intact even if a referenced rule/session/event is later pruned; nothing
    in this bead deletes rows from those tables.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "sw_019"
down_revision = "sw_018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS routing_verdict_log (
            id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            ingestion_event_id UUID NOT NULL REFERENCES public.ingestion_events(id),
            sender_key         TEXT NOT NULL,
            source_channel     TEXT NOT NULL,
            verdict_source     TEXT NOT NULL,
            verdict_action     TEXT NOT NULL,
            verdict_target     TEXT,
            matched_rule_id    UUID REFERENCES ingestion_rules(id),
            session_id         UUID REFERENCES sessions(id),
            decided_at         TIMESTAMPTZ NOT NULL DEFAULT now(),

            CONSTRAINT chk_routing_verdict_log_verdict_source
                CHECK (verdict_source IN ('llm', 'rule', 'pinned', 'spot_check')),

            CONSTRAINT chk_routing_verdict_log_verdict_action
                CHECK (verdict_action IN
                    ('route_to', 'skip', 'metadata_only', 'pass_through', 'block'))
        )
        """
    )

    # Promotion-trigger scan (bead 3): "give me the last N LLM verdicts for
    # this sender/channel, most recent first".
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_routing_verdict_log_sender_channel_decided
        ON routing_verdict_log (sender_key, source_channel, decided_at DESC)
        """
    )

    # Cheap filter for "LLM-only" scans without touching rule/pinned/spot-check
    # rows, which vastly outnumber LLM rows once rules cover most traffic.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_routing_verdict_log_llm_only
        ON routing_verdict_log (verdict_source)
        WHERE verdict_source = 'llm'
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_routing_verdict_log_llm_only")
    op.execute("DROP INDEX IF EXISTS ix_routing_verdict_log_sender_channel_decided")
    op.execute("DROP TABLE IF EXISTS routing_verdict_log")
