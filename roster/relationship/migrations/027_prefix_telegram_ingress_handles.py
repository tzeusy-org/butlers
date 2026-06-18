"""entity_facts: prefix unprefixed telegram has-handle objects written by ingress.

Revision ID: rel_027
Revises: rel_026
Create Date: 2026-06-18 00:00:00.000000

Background
----------
Phase 5 of the contact-schema retirement epic (bu-oluyt.5). The deterministic
ingress hook ``assert_sender_channel_fact`` (relationship_assert_fact.py) used to
write a telegram sender's ``has-handle`` triple with the RAW, unprefixed value
(e.g. ``"206570151"``). The delivery read path
(``daemon._resolve_contact_channel_identifier``) filters has-handle objects on
``LIKE 'telegram:%'``, so those unprefixed rows were NON-deliverable via
``notify(contact_id)`` — the exact owner-notification class of failure this epic
exists to kill.

The writer is now fixed to store the canonical ``telegram:<bare>`` form. This
migration is the one-time backfill that normalises any unprefixed telegram
has-handle rows that ingress already wrote before the fix.

Disambiguation
--------------
rel_019 disambiguated telegram has-handle rows by joining back to
``public.contact_info``. That table was dropped in core_115, so this migration
cannot reuse that signal. Instead it keys on ``src = 'identity'``: the ingress
hook ``assert_sender_channel_fact`` is the *only* writer that uses that src, and
the only channel types it maps to ``has-handle`` in real switchboard ingress are
the telegram family (email/phone map to has-email/has-phone). So
``src = 'identity' AND predicate = 'has-handle'`` uniquely identifies telegram
ingress handles — linkedin/twitter/other handles are never written via this path.

Collision handling
-------------------
``relationship.entity_facts`` has a partial UNIQUE constraint
``uq_ef_spo_active (subject, predicate, object) WHERE validity = 'active'``. If a
subject already has BOTH an unprefixed ``"206570151"`` and a prefixed
``"telegram:206570151"`` active row, a blind UPDATE-prefix would violate it.
Step 1 therefore tombstones (``validity = 'superseded'``) any unprefixed row whose
prefixed twin already exists active for the same subject; step 2 prefixes the
remainder in place.

Idempotency
-----------
Both steps are guarded by ``object NOT LIKE 'telegram:%'``, so re-running is a
no-op once every telegram ingress handle is prefixed.
"""

from __future__ import annotations

from alembic import op

revision = "rel_027"
down_revision = "rel_026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Normalise unprefixed telegram ingress has-handle objects to 'telegram:<bare>'."""
    # Step 1: tombstone unprefixed rows whose prefixed twin already exists active
    # for the same subject, so step 2's UPDATE cannot violate uq_ef_spo_active.
    op.execute("""
        UPDATE relationship.entity_facts ef
        SET validity   = 'superseded',
            updated_at = now()
        WHERE ef.src         = 'identity'
          AND ef.predicate   = 'has-handle'
          AND ef.object_kind = 'literal'
          AND ef.validity    = 'active'
          AND ef.object NOT LIKE 'telegram:%'
          AND EXISTS (
              SELECT 1
              FROM relationship.entity_facts twin
              WHERE twin.subject   = ef.subject
                AND twin.predicate = 'has-handle'
                AND twin.validity  = 'active'
                AND twin.object    = 'telegram:' || ef.object
          )
    """)

    # Step 2: prefix the remaining unprefixed telegram ingress handles in place.
    op.execute("""
        UPDATE relationship.entity_facts ef
        SET object     = 'telegram:' || ef.object,
            updated_at = now()
        WHERE ef.src         = 'identity'
          AND ef.predicate   = 'has-handle'
          AND ef.object_kind = 'literal'
          AND ef.validity    = 'active'
          AND ef.object NOT LIKE 'telegram:%'
    """)


def downgrade() -> None:
    """Strip the 'telegram:' prefix from ingress-written has-handle objects.

    Best-effort inverse of upgrade(): only rows authored by the ingress hook
    (``src = 'identity'``) are touched. Tombstoned duplicates from step 1 are not
    revived (they were genuine duplicates).
    """
    op.execute("""
        UPDATE relationship.entity_facts ef
        SET object     = substring(ef.object FROM length('telegram:') + 1),
            updated_at = now()
        WHERE ef.src         = 'identity'
          AND ef.predicate   = 'has-handle'
          AND ef.object_kind = 'literal'
          AND ef.validity    = 'active'
          AND ef.object LIKE 'telegram:%'
    """)
