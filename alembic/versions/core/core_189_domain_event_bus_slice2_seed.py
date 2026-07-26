"""domain_events slice 2: seed Health's trip-active subscription (bu-317s5).

Revision ID: core_189
Revises: core_188
Create Date: 2026-07-26 00:00:00.000000

bu-317s5 (follow-up from PR #3585 / bu-ep4ks.10 slice 1). Slice 2 wires the
second concrete consumer: Travel publishes ``travel.trip_active`` when a
trip transitions into its active window (``src/butlers/jobs/
context_producers.py::run_travel_context_producer``, the same deterministic
producer that already lights the ``traveling`` context-bus signal), and
Health -- seeded here as a standing subscriber, mirroring core_186's Finance
seed for ``travel.trip_booked`` -- reacts via the domain-event bus's existing
generic subscriber-local wake reconciliation
(``butlers.core.domain_event_wake``): no new hardcoded business logic, the
same pattern slice 1 established.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_189"
down_revision = "core_188"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        INSERT INTO public.butler_subscriptions (subscriber_butler, event_type, active)
        VALUES ('health', 'travel.trip_active', true)
        ON CONFLICT (subscriber_butler, event_type) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM public.butler_subscriptions
        WHERE subscriber_butler = 'health' AND event_type = 'travel.trip_active'
    """)
