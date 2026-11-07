"""Persisted operational roles for ``switchboard.connector_registry`` rows.

``connector_registry`` is written by two independent producers:

- the ``connector.heartbeat`` MCP tool, which registers the **process** that is
  actually executing a connector, and
- :mod:`butlers.connectors.cursor_store`, which persists restart-safe
  **checkpoints**. Connectors whose streams advance independently (Google
  Health: one cursor per account *and* per resource) encode that extra
  dimension into the cursor key, so ``save_cursor`` creates registry rows that
  never receive a heartbeat.

Before ``sw_031`` the two were indistinguishable at the schema level, so every
read path had to *infer* which kind of row it was looking at — either from
column nullability (``sw_028``'s ``v_qa_connector_state`` predicate) or, worse,
from the shape of the opaque ``endpoint_identity`` string. The fleet roster did
neither and simply presented checkpoint rows as connectors that had never
checked in: Google Health's activity/sleep/HRV cursors showed up as separate
OFFLINE listening connectors beside the one genuinely-online account.

``operational_role`` makes the distinction an explicit, persisted fact written
by whichever producer created the row, and ``parent_endpoint_identity`` records
the runtime instance a checkpoint belongs to. Read paths select on the column;
they never re-derive the role.

Who may write which role
------------------------

Only a heartbeat writes ``runtime_instance``: it is the one piece of evidence
that a process actually owns an identity. The ``connector.heartbeat`` tool does
this on every check-in; Google Drive's manager, which heartbeats its per-account
rows by direct SQL rather than through the tool, stamps the role in that same
UPDATE for the same reason.

``cursor_store.save_cursor`` writes the other two, chosen by the caller's
required ownership declaration — ``checkpoint`` when the caller names a parent,
``unknown`` when the cursor key IS the connector's own runtime identity and the
row is therefore simply waiting to be claimed. It writes ``checkpoint`` only
*with* a parent, which is what keeps a NULL ``parent_endpoint_identity`` from
meaning two different things: on a freshly written row it can no longer mean
"nobody set one" (bu-ogs8x). A NULL parent on a ``checkpoint`` row now only
arises from ``sw_031``'s one-shot backfill failing to resolve an owner, which is
a genuine orphan and belongs in the unparented list.

Role semantics
--------------

``runtime_instance``
    An executable connector process identity. This is the ONLY role that
    carries runtime-health authority: it is what the roster lists, what the
    attention strip and KPI band count, and what the fleet
    online/stale/offline rollups aggregate.

``checkpoint``
    Storage state — a persisted cursor for a stream belonging to a
    ``runtime_instance`` parent. It has no heartbeat, so it has no liveness and
    no health authority. It stays inspectable under its parent (and, when the
    parent cannot be resolved, in an explicitly-named unparented list) but it
    never appears in the roster or any rollup.

``unknown``
    The row's role has not been established — neither producer has claimed it.
    This is NOT a synonym for healthy or for active: it is a named unavailable
    state (:data:`UNCLASSIFIED_LIVENESS`) that read paths surface as such,
    precisely so an unclassified row can never be silently counted as a
    working connector.
"""

from __future__ import annotations

#: An executable connector process identity — the only runtime-health authority.
RUNTIME_INSTANCE = "runtime_instance"

#: Storage-only checkpoint/cursor state belonging to a ``RUNTIME_INSTANCE`` parent.
CHECKPOINT = "checkpoint"

#: Role not yet established by any producer. Never treated as active or healthy.
UNKNOWN = "unknown"

#: Every value the ``connector_registry.operational_role`` CHECK constraint allows.
OPERATIONAL_ROLES: frozenset[str] = frozenset({RUNTIME_INSTANCE, CHECKPOINT, UNKNOWN})

#: Liveness reported for an :data:`UNKNOWN` row. Deliberately NOT one of
#: ``online``/``stale``/``offline``: a row whose role is unestablished has no
#: heartbeat contract to measure against, so deriving any of those three would
#: be an inference. Consumers render this as a named degraded state.
UNCLASSIFIED_LIVENESS = "unclassified"


def normalize_operational_role(value: str | None) -> str:
    """Return a known role, mapping anything unrecognised to :data:`UNKNOWN`.

    Read paths use this so a NULL column (a row written by a pre-``sw_031``
    process against a migrated database) or a value from a newer writer degrades
    to the explicitly-unavailable state rather than to a healthy-looking one.
    """
    if value in OPERATIONAL_ROLES:
        return value  # type: ignore[return-value]
    return UNKNOWN
