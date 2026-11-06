"""Domain resource aliases, resolved before owner law (RFC 0038 S1, bu-dhloi1.30).

A capability's descriptor names the resource params its domain resolves
(``alias_resolvers``: param -> a closed kind). For calendar, ``'primary'`` is the
owner's calendar and an id is used after trimming. The broker asks the trusted
``AliasResolver`` seam for each value's canonical identifier before the owner-law
stage, then:

* matches owner terms on canonical identifiers: the call's, and every id a
  term narrows on, so a term written with an alias holds its canonical id;
* dispatches, digests, signs and parks the canonical arguments, so the domain
  receives an identifier it resolves to itself and cannot re-resolve the call
  to another resource after the check.

It fails closed. A value without an answer, an answer that is not a fixed point
(the canonical id must resolve to itself), a resolver error or the deadline
leaves that param unresolved: the call keeps its arguments as given, and an
owner term narrowed on the param selects the call, as for any aliasable param.
A term id that does not resolve drops that term's narrowing on the param, so
the term holds every call on its selector. An omitted param is never resolved
(the domain's default is not visible here), so a narrowed term selects it.

The ``entity`` kind (bu-dhloi1.34) matches but never rewrites. A merged-away id
aliases its survivor, so owner law compares both on the survivor, while the call
keeps the id it named: a merge tool must keep naming its source, and a grant on
the source must not reach what the survivor already held.

The seam is a trusted constructor input, like ``Domains``. Nothing constructs
the broker until the M6 switch.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Collection, Mapping
from dataclasses import dataclass, replace
from typing import Any, Protocol

from butlers.core.owner_policy import Policy, Term, canonical_resource

from .contracts import Capability

# Kinds owner law matches on the canonical id without changing the dispatched call.
_MATCH_ONLY = frozenset({"entity"})


class AliasResolver(Protocol):
    async def resolve(self, domain: str, kind: str, values: Collection[str]) -> Mapping[str, str]:
        """Each value's canonical identifier; a value it cannot resolve is absent."""
        ...


async def _canonical(
    resolver: AliasResolver | None,
    domain: str,
    kind: str,
    values: set[str],
    deadline: float,
) -> dict[str, str]:
    """value -> canonical id, only for values whose answer is a fixed point."""
    if resolver is None or not values:
        return {}
    try:
        async with asyncio.timeout(deadline):
            first = await resolver.resolve(domain, kind, frozenset(values))
            answers = {
                value: canonical
                for value in values
                if type(canonical := first.get(value)) is str and canonical
            }
            fixed = await resolver.resolve(domain, kind, frozenset(answers.values()))
    except asyncio.CancelledError:
        raise
    except Exception:
        return {}
    return {value: canon for value, canon in answers.items() if fixed.get(canon) == canon}


def _values(value: Any) -> list[str] | None:
    values = [value] if type(value) is str else value
    if type(values) is not list or not values or any(type(item) is not str for item in values):
        return None
    return values


@dataclass(frozen=True, slots=True)
class Resolution:
    """A call's arguments with every resolvable alias replaced by its canonical id."""

    # What the call runs with: canonical, except for a match-only kind.
    args: dict[str, Any]
    # The declared params whose every value resolved to a fixed point.
    resolved: frozenset[str]
    # What owner law compares: canonical for every resolved param.
    matched: dict[str, Any]


async def resolve_args(
    resolver: AliasResolver | None,
    capability: Capability,
    args: dict[str, Any],
    deadline: float,
) -> Resolution:
    declared = dict(capability.alias_resolvers)
    present = {name: _values(args[name]) for name in declared if name in args}
    wanted: dict[str, set[str]] = {}
    for name, values in present.items():
        if values is not None:
            wanted.setdefault(declared[name], set()).update(values)
    answers = {
        kind: await _canonical(resolver, capability.domain, kind, values, deadline)
        for kind, values in wanted.items()
    }
    pinned, matched, resolved = dict(args), dict(args), set()
    for name, values in present.items():
        canonical = answers.get(declared[name], {})
        if values is None or any(value not in canonical for value in values):
            continue
        mapped = [canonical[value] for value in values]
        matched[name] = mapped[0] if type(args[name]) is str else mapped
        if declared[name] not in _MATCH_ONLY:
            pinned[name] = matched[name]
        resolved.add(name)
    return Resolution(pinned, frozenset(resolved), matched)


async def canonical_policy(
    resolver: AliasResolver | None,
    capability: Capability,
    policy: Policy,
    deadline: float,
) -> Policy:
    """The policy with every term's narrowing on a resolved param in canonical ids.

    A narrowing with an id that does not resolve is dropped from its term, which
    then holds every call on its selector (a narrowing only ever releases).
    """
    declared = dict(capability.alias_resolvers)
    wanted: dict[str, set[str]] = {}
    for term in policy.terms:
        for name, ids in term.resources:
            if name in declared:
                wanted.setdefault(declared[name], set()).update(ids)
    if not wanted:
        return policy
    answers = {
        kind: await _canonical(resolver, capability.domain, kind, values, deadline)
        for kind, values in wanted.items()
    }

    def canonical_term(term: Term) -> Term:
        narrowed = []
        for name, ids in term.resources:
            if name not in declared:
                narrowed.append((name, ids))
                continue
            canonical = answers.get(declared[name], {})
            if all(item in canonical for item in ids):
                mapped = frozenset(canonical_resource(canonical[item]) for item in ids)
                narrowed.append((name, frozenset(m for m in mapped if m is not None)))
        return replace(term, resources=tuple(narrowed))

    return replace(policy, terms=tuple(canonical_term(term) for term in policy.terms))


_PRIMARY = "primary"


class CalendarAliases:
    """Calendar ids from each domain's discovered provider calendars.

    ``pools`` maps a domain to a pool on its schema (like ``ApprovalsParking``).
    An id resolves, after the trim the calendar module applies, only if it is a
    discovered provider calendar; ``'primary'`` resolves to the calendar whose
    id is the single discovered account's address (Google's primary calendar),
    and to nothing when no account, or more than one, has been discovered.
    """

    KIND = "calendar"

    def __init__(self, pools: Mapping[str, Any]):
        self._pools = dict(pools)

    async def resolve(self, domain: str, kind: str, values: Collection[str]) -> Mapping[str, str]:
        pool = self._pools.get(domain)
        if kind != self.KIND or pool is None:
            return {}
        rows = await pool.fetch(
            "SELECT calendar_id, metadata->>'account_email' AS account FROM calendar_sources "
            "WHERE source_kind = 'provider_event' AND calendar_id IS NOT NULL"
        )
        known = {row["calendar_id"] for row in rows}
        accounts = {row["account"] for row in rows if row["account"]}
        primary = next(iter(accounts)) if len(accounts) == 1 else None
        resolved = {}
        for value in values:
            trimmed = value.strip()
            if trimmed == _PRIMARY:
                if primary in known:
                    resolved[value] = primary
            elif trimmed in known:
                resolved[value] = trimmed
        return resolved


class EntityAliases:
    """An entity id's live identity: the end of its ``merged_into`` chain.

    Every merge records its survivor on ``public.entities``, and one statement
    walks each chain, so one snapshot answers. An id with no row, a chain that
    breaks or cycles, and a value that is not a UUID get no answer. A deleted
    entity keeps its own id: deletion makes it an alias of nothing.
    """

    KIND = "entity"

    def __init__(self, pool: Any):
        self._pool = pool

    async def resolve(self, domain: str, kind: str, values: Collection[str]) -> Mapping[str, str]:
        if kind != self.KIND:
            return {}
        parsed: dict[str, uuid.UUID] = {}
        for value in values:
            try:
                parsed[value] = uuid.UUID(value.strip())
            except ValueError:
                continue
        rows = await self._pool.fetch(
            """
            WITH RECURSIVE walk AS (
                SELECT e.id AS origin, e.id, e.metadata ->> 'merged_into' AS next,
                       ARRAY[e.id] AS visited
                FROM public.entities e
                WHERE e.id = ANY($1::uuid[])
                UNION ALL
                SELECT walk.origin, s.id, s.metadata ->> 'merged_into', walk.visited || s.id
                FROM walk
                JOIN public.entities s ON s.id::text = walk.next
                WHERE NOT s.id = ANY(walk.visited)
            )
            SELECT origin, id FROM walk WHERE next IS NULL
            """,
            list(set(parsed.values())),
        )
        survivors = {row["origin"]: str(row["id"]) for row in rows}
        return {value: survivors[item] for value, item in parsed.items() if item in survivors}


class AliasesByKind:
    """The broker's one seam over each kind's resolver; an unknown kind has no answer."""

    def __init__(self, *resolvers: Any):
        self._resolvers = {resolver.KIND: resolver for resolver in resolvers}

    async def resolve(self, domain: str, kind: str, values: Collection[str]) -> Mapping[str, str]:
        resolver = self._resolvers.get(kind)
        return {} if resolver is None else await resolver.resolve(domain, kind, values)
