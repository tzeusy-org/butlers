"""public.entities: admit the 'animal' entity type (bu-s11n0s.13 S1).

Revision ID: core_354
Revises: core_353
Create Date: 2026-11-21 00:00:00.000000

Owner decision relationship-duty-of-care (2026-11-21, option A): the entity taxonomy gains
``animal`` so a pet is a first-class dependent entity rather than an ``other`` with a role
convention. ``chk_entities_entity_type`` (renamed by core_047) widens from
person/organization/place/other to include animal. Metadata-only for existing rows: every
current value stays valid.

Replays for each per-schema core chain against the shared ``public.entities`` table, so the
swap is DROP IF EXISTS then ADD with the cumulative vocabulary. Downgrade keeps the widened
CHECK: another schema's chain may still hold ``animal`` rows, and narrowing would fail on them
or silently rewrite identity.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_354"
down_revision = "core_353"
branch_labels = None
depends_on = None

_TYPES = ("person", "organization", "place", "animal", "other")


def _swap() -> None:
    op.execute("""
        ALTER TABLE public.entities
        DROP CONSTRAINT IF EXISTS chk_entities_entity_type
    """)
    op.execute(f"""
        ALTER TABLE public.entities
        ADD CONSTRAINT chk_entities_entity_type
        CHECK (entity_type IN ({", ".join(f"'{t}'" for t in _TYPES)}))
    """)


def upgrade() -> None:
    _swap()


def downgrade() -> None:
    # Cumulative on purpose: see the module docstring.
    _swap()
