"""Owner-asserted person posture on ``public.entities``.

Posture is the one axis that tells every butler how to treat a person the owner
has told us about: ``active`` (the default), ``memorial`` (has died),
``quiet`` (leave alone for now) or ``no_contact`` (must not be contacted).
It is asserted only by the owner through the Relationship butler's
``entity_set_posture`` tool and is never inferred. ``entities.listed`` stays the
separate "hide from search" axis.

Producers that nudge the owner about a person (birthday highlights, gift asks,
reconnection insights) filter on ``e.posture = 'active'`` in the same query that
selects the person, so a failed posture read fails the producer instead of
falling back to ``active``. Egress (``notify``) calls :func:`fetch_entity_posture`
and refuses on :data:`EGRESS_BLOCKED_POSTURES` or on any read failure.

Posture values are personal data: callers never log them.
"""

from __future__ import annotations

import uuid
from typing import Final

import asyncpg

ACTIVE: Final = "active"
MEMORIAL: Final = "memorial"
QUIET: Final = "quiet"
NO_CONTACT: Final = "no_contact"

POSTURES: Final = (ACTIVE, MEMORIAL, QUIET, NO_CONTACT)

#: Postures for which no butler may send to the person (``notify(entity_id=...)``).
EGRESS_BLOCKED_POSTURES: Final = frozenset({MEMORIAL, NO_CONTACT})


async def fetch_entity_posture(pool: asyncpg.Pool, entity_id: uuid.UUID) -> str | None:
    """Return the entity's posture, or ``None`` when no such entity exists.

    Raises on any database error; callers decide how to fail closed.
    """
    return await pool.fetchval("SELECT posture FROM public.entities WHERE id = $1", entity_id)
