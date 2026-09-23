"""Repair legacy TTL transitions misclassified by sw_035.

Revision ID: sw_036
Revises: sw_035
Create Date: 2026-09-23 00:00:00.000000

The legacy registry read path recorded stale transitions as ``ttl_expired``.
sw_035 recognized only the sweep writer's ``liveness_ttl_expired`` reason,
placing those rows under review.  A matching saved receipt and transition log
prove that these are automatic liveness transitions, not owner holds.
"""

from __future__ import annotations

from alembic import op

revision = "sw_036"
down_revision = "sw_035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The receipt captured the legacy row before sw_035 projected review into
    # butler_registry.  Match its exact transition identity and timestamp to
    # the durable log; never classify from reason text or current health alone.
    op.execute("""
        WITH proven_ttl AS (
            SELECT c.name
            FROM butler_registry_control_plane AS c
            JOIN butler_registry_eligibility_log AS l
              ON l.butler_name = c.name
             AND c.legacy_evidence ->> 'transition_id' = l.id::text
            JOIN butler_registry AS r ON r.name = c.name
            WHERE c.policy_state = 'review_required'
              AND c.policy_provenance = 'legacy_ambiguous'
              AND c.policy_actor IS NULL
              AND c.policy_reason = 'legacy_ineligible'
              AND c.legacy_evidence ->> 'eligibility_state' = 'stale'
              AND c.legacy_evidence ->> 'transition_state' = 'stale'
              AND c.legacy_evidence ->> 'transition_reason' = 'ttl_expired'
              AND c.legacy_evidence -> 'quarantined_at' = 'null'::jsonb
              AND r.eligibility_state = 'quarantined'
              AND r.quarantine_reason = 'protected_policy:review_required'
              AND l.previous_state = 'active'
              AND l.new_state = 'stale'
              AND l.reason = 'ttl_expired'
              AND l.observed_at = c.policy_changed_at
              AND c.legacy_evidence -> 'transition_at' = to_jsonb(l.observed_at)
              AND c.legacy_evidence -> 'eligibility_updated_at' =
                  to_jsonb(l.observed_at)
        ), reclassified AS (
            UPDATE butler_registry_control_plane AS c
               SET policy_state = 'active',
                   policy_provenance = 'legacy_ttl',
                   observed_state = CASE
                       WHEN c.observed_state = 'observer_unknown' THEN 'stale'
                       ELSE c.observed_state END,
                   updated_at = clock_timestamp()
              FROM proven_ttl AS p
             WHERE c.name = p.name
               AND c.policy_state = 'review_required'
               AND c.policy_provenance = 'legacy_ambiguous'
            RETURNING c.name
        )
        UPDATE butler_registry AS r
           SET eligibility_state = 'stale',
               quarantined_at = NULL,
               quarantine_reason = NULL
          FROM reclassified AS c
         WHERE r.name = c.name
    """)


def downgrade() -> None:
    # Classification is an authority correction.  Reversing it would
    # quarantine proven automatic TTL transitions again.
    pass
