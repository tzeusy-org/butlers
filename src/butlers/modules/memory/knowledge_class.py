"""Event/durable knowledge class for memory facts, derived from the registry (bu-bagfg).

Profile Facts must hold durable identity/preference knowledge. Event and
transaction facts (purchases, doses, meals, sleep sessions, interactions) are
owner-class when the owner authored them, so content authority cannot keep them
out; the predicate does.

A fact is event-class exactly when its predicate is registered with
``predicate_registry.is_temporal = true``. That flag is seeded for every event
predicate (migrations 002/003) and set automatically at storage time for any
new predicate stored with ``valid_at``, so no second list needs maintaining.
There is no schema column or backfill: legacy rows are classified by the same
join. A predicate absent from the registry is treated as durable, which is the
pre-existing behavior (fail open to the old pack, never silently hide a fact).
Event-class facts remain stored and recallable; only Profile Facts excludes them.
"""

from __future__ import annotations

# SQL condition over the ``facts`` alias ``f``: true when the fact is NOT event-class.
NOT_EVENT_CLASS_SQL = (
    "NOT EXISTS (SELECT 1 FROM predicate_registry pr "
    "WHERE pr.name = f.predicate AND pr.is_temporal)"
)
