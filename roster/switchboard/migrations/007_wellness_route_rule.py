"""Seed a global ingestion rule that routes wellness envelopes to the Health butler.

Revision ID: sw_007
Revises: sw_006
Create Date: 2026-04-25 00:00:00.000000

Wellness envelopes (e.g. from the google_health connector) carry
``source_channel='wellness'``.  Before this migration, each envelope
triggered a full Switchboard LLM classification session before being
forwarded to the Health butler — despite the routing decision being
entirely deterministic (all wellness traffic goes to health).

This migration inserts a ``scope='global'`` rule with
``rule_type='source_channel'`` and ``action='route_to:health'``.  When the
IngestionPolicy evaluator sees an event with ``source_channel='wellness'``,
it sets ``triage_decision='route_to'`` and ``triage_target='health'`` in
``request_context``, activating the pipeline's existing policy-bypass path
(``pipeline.py`` lines ~1340–1466) which dispatches directly to the Health
butler's ``route.execute`` tool **without** spawning a Switchboard-side LLM
session.

The Health butler still spawns its own LLM session via its existing
``route.execute`` handler — this migration only eliminates the Switchboard
half.

The rule is disabled via:
  ``UPDATE switchboard.ingestion_rules SET enabled=false
    WHERE id='00000000-0000-0000-0001-000000000080'``
if the full Switchboard LLM classification session is ever needed back for
wellness envelopes.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "sw_007"
down_revision = "sw_006"
branch_labels = None
depends_on = None


_RULE_ID = "00000000-0000-0000-0001-000000000080"


def upgrade() -> None:
    op.execute(
        f"""
        INSERT INTO ingestion_rules
          (id, scope, rule_type, condition, action, priority, enabled,
           name, description, created_by)
        VALUES
          (
            '{_RULE_ID}',
            'global',
            'source_channel',
            '{{"source_channel": "wellness"}}',
            'route_to:health',
            10,
            TRUE,
            'Route wellness envelopes to Health butler',
            'Wellness connector events bypass Switchboard LLM classification and route directly to the Health butler via the policy-bypass path. The Health butler still spawns its own LLM session.',
            'seed'
          )
        ON CONFLICT (id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(f"DELETE FROM ingestion_rules WHERE id = '{_RULE_ID}'")
