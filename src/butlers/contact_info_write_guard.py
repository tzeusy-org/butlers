"""App-level write-block guard for the deprecated ``public.contact_info`` table.

Migration bead 8 — write-path cut-over (entity-redesign contacts → triples,
bu-k9ylx).

After the write-path cut-over, ALL channel-identity writes go through the
central writer ``relationship_assert_fact()`` ONLY.  ``public.contact_info`` is
read-only: SELECT stays allowed, but INSERT / UPDATE / DELETE are blocked.

Two enforcement layers (defence in depth):

1. **Database layer (authoritative)** — Alembic migration
   ``core_110_contact_info_write_block`` REVOKEs INSERT/UPDATE/DELETE on
   ``public.contact_info`` from every butler runtime role.  PostgreSQL rejects
   any write at the role-permission boundary.

2. **App layer (this module, fail-fast)** — a clear, early error so a stray
   writer fails with an actionable message *before* hitting the DB, instead of a
   raw asyncpg ``InsufficientPrivilegeError``.  Production writers SHOULD call
   :func:`assert_contact_info_writes_blocked` at the top of any function that
   would otherwise write ``public.contact_info``.

The guard is intentionally simple and side-effect free: it raises
:class:`ContactInfoWriteBlockedError`.  It does not inspect SQL — it is a
programmatic assertion that the calling code path is supposed to be dead after
cut-over.
"""

from __future__ import annotations

# The table that is now read-only after the write-path cut-over.
CONTACT_INFO_TABLE = "public.contact_info"


class ContactInfoWriteBlockedError(RuntimeError):
    """Raised when production code attempts to write ``public.contact_info``.

    ``public.contact_info`` became read-only at the entity-redesign write-path
    cut-over (Migration bead 8, bu-k9ylx).  Channel-identity facts MUST be
    written via the central writer ``relationship_assert_fact()`` into
    ``relationship.entity_facts`` instead.  Reads from ``public.contact_info``
    remain allowed until the table is dropped at Migration bead 10.
    """


def assert_contact_info_writes_blocked(operation: str = "write") -> None:
    """Fail fast if a caller is about to write the read-only ``contact_info`` table.

    Call this at the top of any code path that would issue an
    ``INSERT`` / ``UPDATE`` / ``DELETE`` against ``public.contact_info``.

    Parameters
    ----------
    operation:
        Short label for the attempted operation (e.g. ``"insert"``,
        ``"update"``, ``"delete"``).  Surfaced in the error message to aid
        debugging.

    Raises
    ------
    ContactInfoWriteBlockedError
        Always — this guard marks a code path that is dead after the cut-over.
    """
    raise ContactInfoWriteBlockedError(
        f"Attempted {operation} on read-only table {CONTACT_INFO_TABLE}. "
        "public.contact_info became read-only at the entity-redesign write-path "
        "cut-over (Migration bead 8). Channel-identity facts MUST be written via "
        "relationship_assert_fact() into relationship.entity_facts instead. "
        "Reads from public.contact_info are still allowed."
    )


__all__ = [
    "CONTACT_INFO_TABLE",
    "ContactInfoWriteBlockedError",
    "assert_contact_info_writes_blocked",
]
