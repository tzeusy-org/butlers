"""entity_predicate_registry: seed the ``prefers-channel`` override predicate.

Revision ID: rel_022
Revises: rel_021
Create Date: 2026-06-14 00:00:00.000000

Phase: entity-keyed-preferred-channel (group 1, bu-ctsgh; epic bu-sbdwt).

Seeds a single new predicate into ``relationship.entity_predicate_registry``:

* ``prefers-channel`` (``kind='override'``, ``object_kind='literal'``,
  ``cardinality='single'``) — a contact's preferred outbound channel.

Why ``kind='override'`` (not ``'contact'``)
-------------------------------------------
``prefers-channel`` is NOT a channel-identity (``has-*``) predicate: its object
is a channel *name* (``"telegram"``, ``"email"``, ``"discord"``), not a reachable
identifier. It is an owner-set preference that supersedes the default outbound
precedence — exactly the shape of ``dunbar_tier_override``. ``kind='contact'``
predicates are additionally enrolled into the memory-module identity-predicate
rejection floor (``modules/memory/tools/writing.py`` reads ``WHERE kind =
'contact'``); ``prefers-channel`` must not join that set. ``object_kind='literal'``
because the object is a plain channel-name string.

Why ``cardinality='single'``
-----------------------------
An entity has at most one active preferred channel. ``cardinality='single'``
(added to the registry by rel_021) is the registry-sourced declaration that a
second active value is a divergence — it drives merge-review classification AND
signals the single-valued supersession contract enforced by the write path
(``assert_prefers_channel`` in ``relationship_assert_fact.py``).

Idempotency
-----------
``INSERT ... ON CONFLICT (predicate) DO NOTHING`` for the row, plus an explicit
``UPDATE ... SET cardinality='single'`` so the cardinality is correct even if a
prior run seeded the predicate row before the cardinality column existed.

Downgrade
---------
Deletes exactly the ``prefers-channel`` row. Does NOT drop the registry table,
the ``cardinality`` column, or the relationship schema (all owned by earlier
migrations).
"""

from __future__ import annotations

from alembic import op

revision = "rel_022"
down_revision = "rel_021"
branch_labels = None
depends_on = None

_PREDICATE = "prefers-channel"
_KIND = "override"
_OBJECT_KIND = "literal"
_CARDINALITY = "single"
_DESCRIPTION = (
    "Preferred outbound channel for the entity (channel name literal, e.g. "
    "'telegram', 'email', 'discord'). Single-valued; honored by notify() when "
    "the channel is deliverable, else falls back to default precedence."
)


def upgrade() -> None:
    # Defensive: schema + registry table are created by rel_013/rel_014; this is
    # idempotent and guards against an out-of-order partial DB.
    op.execute("CREATE SCHEMA IF NOT EXISTS relationship")

    safe_desc = _DESCRIPTION.replace("'", "''")
    op.execute(
        f"""
        INSERT INTO relationship.entity_predicate_registry
            (predicate, kind, object_kind, description)
        VALUES ('{_PREDICATE}', '{_KIND}', '{_OBJECT_KIND}', '{safe_desc}')
        ON CONFLICT (predicate) DO NOTHING
        """
    )

    # Ensure cardinality is 'single' even if the row pre-existed from an earlier
    # partial seed (the cardinality column is added by rel_021 with default
    # 'multi', so a DO NOTHING insert would otherwise leave it 'multi').
    op.execute(
        f"""
        UPDATE relationship.entity_predicate_registry
        SET cardinality = '{_CARDINALITY}'
        WHERE predicate = '{_PREDICATE}'
        """
    )


def downgrade() -> None:
    # Remove exactly the row this migration seeded. Other predicates, the
    # cardinality column, the registry table, and the schema are owned by
    # earlier migrations and survive.
    op.execute(
        f"DELETE FROM relationship.entity_predicate_registry WHERE predicate = '{_PREDICATE}'"
    )
