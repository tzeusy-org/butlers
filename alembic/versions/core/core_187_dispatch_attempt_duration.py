"""model_dispatch_attempts: add duration_ms column (JARVIS run-07 move #13).

Revision ID: core_187
Revises: core_186
Create Date: 2026-07-26 00:00:00.000000

bu-ep4ks.13 (2026-07-25 JARVIS pursuit dossier, ranked move #13, slice 1).

WHY: The spawner already computes per-attempt wall-clock duration (used for
the ledger and audit log) but discards it before writing
``public.model_dispatch_attempts`` -- so a working-but-slow model is
invisible to any evidence-based routing decision (cf. the 436s opencode
incident: the model succeeded, so nothing in the dispatch trail ever
recorded how long it took). This adds a nullable ``duration_ms`` column
populated on every attempt that actually invoked a runtime (``success``,
``runtime_failure``, ``suppressed``, ``exhausted``). Pre-invocation gate
denials (``quota_skip``, ``breaker_open_override``) leave it NULL --
no runtime call happened, so a duration would be fabricated, not measured.

See ``src/butlers/core/model_routing.py`` (``RoutingEvidence``,
``get_routing_evidence``, ``compute_routing_score``) for the slice-2
evidence-based tie-break consumer.
"""

from __future__ import annotations

from alembic import op

revision = "core_187"
down_revision = "core_186"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE public.model_dispatch_attempts
        ADD COLUMN IF NOT EXISTS duration_ms INT
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE public.model_dispatch_attempts
        DROP COLUMN IF EXISTS duration_ms
    """)
