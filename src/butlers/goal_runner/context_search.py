"""``context.search`` (RFC 0038 D8, D15, S5): grant-scoped, read-pure recall.

One deterministic fan-out over the domains the calling invocation's grant covers,
merged into a single envelope of whole, labelled items.

* **Closed query.** ``parse_query`` accepts only the S5 keys and refuses ``POLICY``
  for anything else, including every value outside its bound.
* **Grant-scoped.** A domain is searched only when the invocation holds an
  unrestricted grant on a ``read`` capability of that domain that the catalog lists
  at its depth. A domain reachable only through resource-restricted grants is not
  searched, because a source cannot honour a per-resource restriction; so a child,
  holding a subset of its lead's grant, can only ever see a subset of its sources.
* **S5 trust rules.** An exposed reader, or one of third-party origin, is refused: it gets
  availability only, never recall. Only owner-authored, provably untainted text is inlined;
  every other hit is a bare reference with its category, authority and taint label. The six
  sensitive categories are excluded unless the query names one and the grant covers it, and a
  hit in an excluded category is dropped whatever the source returned.
* **Read-pure.** The call writes only what every goal-tool call writes: one call on
  the goal budget and the invocation's share (refused ``BUDGET`` when spent), and
  the reader's label join for what it actually read (D6). It creates no effect row,
  and it never touches a source's reference counts or ranking.
* **Honest merge.** Rank-level fusion across sources (never raw scores), displayed in
  the order of credence. A source that times out or fails is marked degraded; every
  source failing is ``UNAVAILABLE``, not an empty success. Budget truncation omits
  whole items and reports the count; it never clips a reference or label away from
  its body.
* **Identifiers in spans.** The span carries the goal id, invocation id and depth.
"""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from opentelemetry import trace

from butlers.capability_broker.contracts import BrokerRefused, Principal, Reason
from butlers.capability_broker.policy import listable
from butlers.capability_broker.service import Broker
from butlers.core.goal_labels import CATEGORIES, Label, output_label

logger = logging.getLogger(__name__)
_tracer = trace.get_tracer(__name__)

TOOL = "context__search"

QUERY_KEYS = frozenset({"query", "anchors", "as_of", "since", "until", "regions", "kinds", "k"})

#: Order of credence (S5), most credible first. It orders display; it grants no trust.
AUTHORITIES = ("owner_correction", "owner", "domain", "third_party", "unknown")
_CREDENCE = {name: rank for rank, name in enumerate(AUTHORITIES)}

_FUSION_K = 60  # reciprocal-rank-fusion constant
_NAME_CHARS = 64
_REF_CHARS = 256


@dataclass(frozen=True, slots=True)
class ContextSearchLimits:
    """Effective limits, snapshotted with the request (S5 version-1 defaults).

    Every value must be finite and positive; anything else refuses at construction,
    so a missing or invalid limit never becomes an unbounded search.
    """

    query_bytes: int = 1024
    anchors: int = 16
    regions_kinds: int = 16
    k_max: int = 20
    per_domain_s: float = 1.0
    total_s: float = 2.0
    packet_tokens: int = 4096
    excerpt_chars: int = 480

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"context search limit {name} must be finite and positive")
        if self.per_domain_s > self.total_s:
            raise ValueError("per-domain deadline cannot exceed the total deadline")


@dataclass(frozen=True, slots=True)
class ContextQuery:
    query: str
    anchors: tuple[str, ...] = ()
    as_of: datetime | None = None
    since: datetime | None = None
    until: datetime | None = None
    regions: tuple[str, ...] = ()
    kinds: tuple[str, ...] = ()
    k: int = 10


def _refuse() -> BrokerRefused:
    return BrokerRefused(Reason.POLICY)


def _names(value: Any, limit: int) -> tuple[str, ...]:
    if type(value) is not list or len(value) > limit:
        raise _refuse()
    if any(type(item) is not str or not item or len(item) > _NAME_CHARS for item in value):
        raise _refuse()
    if len(set(value)) != len(value):
        raise _refuse()
    return tuple(value)


def _instant(value: Any) -> datetime:
    if type(value) is not str:
        raise _refuse()
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        raise _refuse() from None
    if moment.tzinfo is None:  # a clock reading without a zone is a guess
        raise _refuse()
    return moment.astimezone(UTC)


def parse_query(arguments: Any, limits: ContextSearchLimits) -> ContextQuery:
    """The closed S5 query shape; anything outside it refuses ``POLICY``."""
    if type(arguments) is not dict or "query" not in arguments or not set(arguments) <= QUERY_KEYS:
        raise _refuse()
    text = arguments["query"]
    if type(text) is not str or not text.strip():
        raise _refuse()
    try:
        size = len(text.encode("utf-8"))
    except UnicodeEncodeError:  # a lone surrogate has no UTF-8 form
        raise _refuse() from None
    if size > limits.query_bytes:
        raise _refuse()
    anchors = _names(arguments.get("anchors", []), limits.anchors)
    try:
        if any(str(UUID(anchor)) != anchor for anchor in anchors):
            raise ValueError(anchors)
    except ValueError:
        raise _refuse() from None  # anchors are entity ids: opaque to the model, never invented
    as_of, since, until = (
        _instant(arguments[key]) if key in arguments else None
        for key in ("as_of", "since", "until")
    )
    if since is not None and until is not None and since >= until:
        raise _refuse()
    if as_of is not None and (since is not None or until is not None):
        raise _refuse()  # a point in time or a window, not both
    k = arguments.get("k", min(10, limits.k_max))
    if type(k) is not int or not 1 <= k <= limits.k_max:
        raise _refuse()
    return ContextQuery(
        text,
        anchors,
        as_of,
        since,
        until,
        _names(arguments.get("regions", []), limits.regions_kinds),
        _names(arguments.get("kinds", []), limits.regions_kinds),
        k,
    )


@dataclass(frozen=True, slots=True)
class SourceHit:
    """One whole item from one domain, in that domain's own rank order."""

    ref: str
    region: str
    kind: str
    excerpt: str
    observed_at: datetime | None = None
    authority: str = "unknown"
    conflict_set: str | None = None
    category: str | None = None
    """One of the six sensitive categories, or ``None`` for a hit that carries none."""
    tainted: bool | None = None
    """``False`` only when the record is provably untainted; ``None`` is unknown."""


@dataclass(frozen=True, slots=True)
class SourceResult:
    """A domain's hits plus its stamp account, exactly as a stamped read carries it (S1).

    Without ``stamped_owner_untainted`` the output is of unknown provenance and
    sensitivity; each listed ``(taint_goal_id, taint_invocation_id)`` writer's label
    joins the reader as well.
    """

    hits: tuple[SourceHit, ...] = ()
    stamped_owner_untainted: bool = False
    stamped_writers: tuple[tuple[Any, Any], ...] = ()
    categories: frozenset[str] = CATEGORIES


@dataclass(frozen=True, slots=True)
class ReaderContext:
    """What a source may know about its reader: never a token, a goal or a grant.

    ``categories`` are the sensitive categories this search may return from this source:
    explicitly requested and inside the reader's grant. Everything else is excluded, and the
    engine drops it again whatever the source returns. ``exposed`` is always false today,
    because an exposed reader is refused; a source must still honour it, for when an
    availability-only view exists.
    """

    depth: int
    exposed: bool
    categories: frozenset[str]


class ContextSource(Protocol):
    """A domain's read-only memory and fact search; it must not record the read.

    It must also keep private-custody content and raw coordinates out of every hit; the
    engine cannot see inside a source to enforce either.
    """

    async def search(self, query: ContextQuery, reader: ReaderContext) -> SourceResult: ...


@dataclass(slots=True)
class _Outcome:
    status: str  # ok | timeout | failed
    result: SourceResult | None = None


@dataclass(frozen=True, slots=True)
class _Item:
    source: str
    hit: SourceHit
    score: float


def _valid(result: Any) -> bool:
    return (
        isinstance(result, SourceResult)
        and type(result.hits) is tuple
        and all(
            isinstance(hit, SourceHit)
            and all(type(v) is str for v in (hit.ref, hit.region, hit.kind, hit.excerpt))
            and 0 < len(hit.ref) <= _REF_CHARS
            and (hit.category is None or hit.category in CATEGORIES)
            and (hit.tainted is None or type(hit.tainted) is bool)
            for hit in result.hits
        )
    )


def _fused(outcomes: Mapping[str, _Outcome]) -> list[_Item]:
    """Reciprocal-rank fusion: only each source's ranks count, never its raw scores."""
    items: list[_Item] = []
    for source in sorted(outcomes):
        result = outcomes[source].result
        if result is None:
            continue
        for rank, hit in enumerate(result.hits, start=1):
            items.append(_Item(source, hit, 1.0 / (_FUSION_K + rank)))
    # Credence orders display; fused rank orders within a tier; names make it total.
    items.sort(
        key=lambda i: (
            _CREDENCE.get(i.hit.authority, _CREDENCE["unknown"]),
            -i.score,
            i.source,
            i.hit.ref,
        )
    )
    return items


def _tokens(entry: dict[str, Any]) -> int:
    return math.ceil(len(repr(entry).encode("utf-8")) / 4)


_OWNER_AUTHORED = frozenset({"owner_correction", "owner"})


def inlined(hit: SourceHit) -> bool:
    """S5: only owner-authored, provably untainted text may inline; the rest is a bare
    reference. A missing or unknown authority or taint never inlines."""
    return hit.authority in _OWNER_AUTHORED and hit.tainted is False


def _entry(item: _Item, limit: int) -> dict[str, Any]:
    hit = item.hit
    authority = hit.authority if hit.authority in _CREDENCE else "unknown"
    inline = inlined(hit)
    return {
        "ref": f"{item.source}:{hit.ref}",
        "source": item.source,
        "region": hit.region,
        "kind": hit.kind,
        "inline": inline,
        "excerpt": hit.excerpt[:limit] if inline else None,
        "excerpt_truncated": inline and len(hit.excerpt) > limit,
        "category": hit.category,
        "taint": "unknown" if hit.tainted is None else ("tainted" if hit.tainted else "untainted"),
        "observed_at": None
        if hit.observed_at is None
        else hit.observed_at.astimezone(UTC).isoformat(),
        "authority": authority,
        "conflict_set": hit.conflict_set,
    }


def _packet(
    items: list[_Item], k: int, limits: ContextSearchLimits
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Whole items up to ``k`` and the packet budget; the rest are counted, not clipped."""
    kept: list[dict[str, Any]] = []
    spent, reason = 0, None
    for item in items:
        if len(kept) >= k:
            reason = "k"
            break
        entry = _entry(item, limits.excerpt_chars)
        cost = _tokens(entry)
        if spent + cost > limits.packet_tokens:
            reason = "packet_budget"
            break
        kept.append(entry)
        spent += cost
    return kept, {"count": len(items) - len(kept), "reason": reason}


class ContextSearch:
    """The ``context.search`` goal tool's implementation."""

    def __init__(
        self,
        broker: Broker,
        sources: Mapping[str, ContextSource],
        limits: ContextSearchLimits | None = None,
    ):
        self.broker = broker
        self.sources = dict(sources)
        self.limits = limits or ContextSearchLimits()

    async def search(self, goal_id: UUID, token: str, arguments: Any) -> dict[str, Any]:
        query = parse_query(arguments, self.limits)
        with _tracer.start_as_current_span(
            "goal.context_search", record_exception=False, set_status_on_exception=False
        ) as span:
            span.set_attribute("goal_id", str(goal_id))
            principal, exhausted, sources, included = await self._admit(goal_id, token, query)
            span.set_attribute("invocation_id", str(principal.invocation_id))
            span.set_attribute("depth", principal.depth)
            if exhausted is not None:
                raise exhausted
            outcomes = self._within_categories(
                await self._fan_out(sources, query, included, principal.depth), included
            )
            if not any(o.status == "ok" for o in outcomes.values()):
                raise BrokerRefused(Reason.UNAVAILABLE)  # all failed: not an empty success
            await self._join(goal_id, token, outcomes)
            items = _fused(outcomes)
            hits, omitted = _packet(items, query.k, self.limits)
        return {
            "status": "complete"
            if all(o.status == "ok" for o in outcomes.values())
            else "degraded",
            "hits": hits,
            "sources": {name: outcomes[name].status for name in sorted(outcomes)},
            "omitted": omitted,
        }

    async def _admit(
        self, goal_id: UUID, token: str, query: ContextQuery
    ) -> tuple[
        Principal, BrokerRefused | None, dict[str, ContextSource], dict[str, frozenset[str]]
    ]:
        """Authenticate, find the sources this grant covers, and charge the call.

        S5: an exposed reader, or one of third-party origin, gets availability only, never
        recall, so it is refused here before anything is charged. A sensitive category is
        searched only when the query names it and the grant covers it.
        """
        self.broker._require_available()
        async with self.broker._transaction() as ledger:
            principal = await ledger.authenticate(goal_id, token)
            catalog = await self.broker._live_catalog.load(ledger.connection)
            if catalog.digest != principal.catalog_digest:
                raise BrokerRefused(Reason.CATALOG)
            if principal.exposed or principal.origin == "third_party":
                raise BrokerRefused(Reason.POLICY)
            granted: dict[str, frozenset[str]] = {}
            for grant in principal.grants:
                capability = catalog.get(grant.capability)
                if (
                    capability is not None
                    and capability.effect_class == "read"
                    and listable(capability, principal.depth)
                    and not grant.resources  # a restricted grant cannot be honoured by a source
                    and capability.domain in self.sources
                ):
                    covered = capability.categories & CATEGORIES
                    granted[capability.domain] = (
                        granted.get(capability.domain, frozenset()) | covered
                    )
            if not granted:
                raise BrokerRefused(Reason.CAPABILITY)
            asked = frozenset(query.regions + query.kinds) & CATEGORIES
            if asked - frozenset().union(*granted.values()):
                raise BrokerRefused(Reason.RESOURCE)  # a category the grant does not cover
            included = {name: asked & covered for name, covered in granted.items()}
            exhausted = await ledger.charge_call(principal)
        sources = {name: self.sources[name] for name in sorted(granted)}
        return principal, exhausted, sources, included

    async def _fan_out(
        self,
        sources: Mapping[str, ContextSource],
        query: ContextQuery,
        included: Mapping[str, frozenset[str]],
        depth: int,
    ) -> dict[str, _Outcome]:
        async def one(name: str, source: ContextSource) -> SourceResult:
            reader = ReaderContext(depth=depth, exposed=False, categories=included[name])
            async with asyncio.timeout(self.limits.per_domain_s):
                return await source.search(query, reader)

        tasks = {name: asyncio.ensure_future(one(name, source)) for name, source in sources.items()}
        try:
            await asyncio.wait(tasks.values(), timeout=self.limits.total_s)
        finally:
            for task in tasks.values():
                if not task.done():
                    task.cancel()
        outcomes: dict[str, _Outcome] = {}
        for name, task in tasks.items():
            if not task.done() or task.cancelled():
                outcomes[name] = _Outcome("timeout")
                continue
            error = task.exception()
            if isinstance(error, TimeoutError):
                outcomes[name] = _Outcome("timeout")
            elif error is not None:
                logger.info("context search source failed source=%s", name)
                outcomes[name] = _Outcome("failed")
            elif not _valid(task.result()):
                outcomes[name] = _Outcome("failed")
            else:
                outcomes[name] = _Outcome("ok", task.result())
        return outcomes

    @staticmethod
    def _within_categories(
        outcomes: dict[str, _Outcome], included: Mapping[str, frozenset[str]]
    ) -> dict[str, _Outcome]:
        """Drop every hit in a sensitive category this search was not entitled to.

        Whatever a source returned, a hit in an excluded category never leaves the engine,
        and is not counted: its absence must not reveal that it exists.
        """
        for name, outcome in outcomes.items():
            result = outcome.result
            if result is None:
                continue
            kept = tuple(
                h for h in result.hits if h.category is None or h.category in included[name]
            )
            outcome.result = replace(result, hits=kept)
        return outcomes

    async def _join(self, goal_id: UUID, token: str, outcomes: Mapping[str, _Outcome]) -> None:
        """What was read joins the reader (D6); a reader that is no longer current gets nothing."""
        async with self.broker._transaction() as ledger:
            principal = await ledger.authenticate(goal_id, token)
            await ledger._lock_goal(goal_id)
            label: Label | None = None
            for outcome in outcomes.values():
                result = outcome.result
                if result is None or not any(inlined(h) for h in result.hits):
                    continue  # no text came back from this source, so nothing was read
                part = output_label("stamped", result.stamped_owner_untainted, result.categories)
                for pointer in result.stamped_writers:
                    part = part.join(await ledger.writer_label(pointer))
                label = part if label is None else label.join(part)
            if label is not None and not await ledger._join(
                goal_id, principal.invocation_id, label, "read"
            ):
                raise BrokerRefused(Reason.UNAVAILABLE)


__all__ = [
    "AUTHORITIES",
    "TOOL",
    "ContextQuery",
    "ContextSearch",
    "ContextSearchLimits",
    "ContextSource",
    "ReaderContext",
    "SourceHit",
    "SourceResult",
    "inlined",
    "parse_query",
]
