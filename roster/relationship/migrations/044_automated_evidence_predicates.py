"""entity_predicate_registry: seed ``away-until`` and ``undeliverable-email``.

Revision ID: rel_044
Revises: rel_043

bu-s11n0s.14, slice S2b. The deterministic ``automated_evidence_sync`` job turns Gmail
auto-replies and bounces, already bound to the owner's sent mail and authenticated by the
connector (S2a), into two Relationship facts:

* ``away-until`` (``kind='state'``, ``object_kind='literal'``, ``cardinality='multi'``): the
  object is the civil return date (``YYYY-MM-DD``) one auto-reply stated. Each distinct date is
  its own active row, so replaying an auto-reply is a no-op and a later auto-reply never
  supersedes an earlier one; a reader takes the most recently observed row. The interval stays a
  date literal because the effective-time packet is fenced (``temporal_cutover_pending``) while
  ``uq_ef_spo_active`` exists.
* ``undeliverable-email`` (``kind='state'``, ``object_kind='literal'``, ``cardinality='multi'``):
  the object is an address of the entity that permanently failed (DSN ``failed`` with ``5.x.x``).

``kind='state'`` (rel_015), not ``'contact'``: neither is a reachable identifier, and
``contact``-kind predicates join the memory module's identity-predicate floor.

Idempotent: ``INSERT ... ON CONFLICT (predicate) DO NOTHING``. Downgrade refuses while any fact
uses either predicate, then deletes exactly the two rows.
"""

from __future__ import annotations

from alembic import op

revision = "rel_044"
down_revision = "rel_043"
branch_labels = None
depends_on = None

_PREDICATES: tuple[tuple[str, str], ...] = (
    (
        "away-until",
        "Civil return date (YYYY-MM-DD) stated by one authenticated auto-reply to the owner's "
        "sent mail. Multi-valued; the most recently observed row is current. Written only by "
        "the automated_evidence_sync job.",
    ),
    (
        "undeliverable-email",
        "An address of the entity that permanently failed delivery (DSN Action failed, 5.x.x) "
        "for mail the owner sent. Written only by the automated_evidence_sync job.",
    ),
)


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS relationship")
    for predicate, description in _PREDICATES:
        safe_desc = description.replace("'", "''")
        op.execute(
            f"""
            INSERT INTO relationship.entity_predicate_registry
                (predicate, kind, object_kind, description, cardinality)
            VALUES ('{predicate}', 'state', 'literal', '{safe_desc}', 'multi')
            ON CONFLICT (predicate) DO NOTHING
            """
        )


def downgrade() -> None:
    names = ", ".join(f"'{predicate}'" for predicate, _ in _PREDICATES)
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM relationship.entity_facts WHERE predicate IN ({names})
            ) THEN
                RAISE EXCEPTION 'rel_044 downgrade refused: automated-evidence facts exist';
            END IF;
        END
        $$;
        """
    )
    op.execute(f"DELETE FROM relationship.entity_predicate_registry WHERE predicate IN ({names})")
