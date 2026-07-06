"""Demotion spot-check index on switchboard.routing_verdict_log.

Revision ID: sw_021
Revises: sw_020
Create Date: 2026-07-06 00:00:00.000000

bu-x55k3 (rule-promotion bead 5 of 7). Per
``docs/plans/2026-07-06-switchboard-rule-promotion-design.md`` section 4 and
the merged openspec change ``switchboard-rule-promotion``
(``openspec/changes/switchboard-rule-promotion/specs/switchboard-rule-promotion/spec.md``,
"Requirement: Demotion via Spot-Check Sampling"). Beads 1-4 shipped the
mining substrate, suggestion schema (including the ``suggestion_kind``
discriminator and the pending-demotion unique index, both already added by
sw_020), and the promotion trigger. This bead needs no new table and no new
mutable state — the rolling per-rule agreement score is computed on demand
from ``routing_verdict_log`` rows the spot-check hook already writes
(``verdict_source='spot_check'``, ``matched_rule_id`` set to the sampled
rule) rather than maintained as a separately-updated counter, per the
dispatch's "prefer minimal storage" guidance.

The one gap: sw_019 indexed ``routing_verdict_log`` for the promotion
trigger's access pattern (``sender_key, source_channel, decided_at DESC``,
plus a ``verdict_source='llm'`` partial index) but not for this bead's
access pattern — "give me the last N spot-check verdicts for rule X,
most recent first" — which filters by ``matched_rule_id`` under
``verdict_source='spot_check'``, a column/value combination sw_019's
indexes do not cover. Without this index, every spot-check's post-write
demotion evaluation (``roster/switchboard/tools/routing/rule_demotion.py::
maybe_create_demotion_suggestion``) would force a sequential scan of the
whole table, which is unacceptable given the table is written on every
routing decision (rule bypass, LLM verdict, and now spot-check), not just
the comparatively rare spot-check subset.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "sw_021"
down_revision = "sw_020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Demotion spot-check scoring (bead 5): "give me the last N spot-check
    # verdicts for this rule, most recent first."
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_routing_verdict_log_spot_check_rule
        ON routing_verdict_log (matched_rule_id, decided_at DESC)
        WHERE verdict_source = 'spot_check'
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_routing_verdict_log_spot_check_rule")
