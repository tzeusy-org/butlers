"""``guidance.search`` and ``guidance.load`` (RFC 0038 D2, D8, D15; M4): domain guidance.

A domain's ``CLAUDE.md`` and skills become guidance documents: versioned, searchable, and
attached by reference to child briefs (``guidance_refs``). These two goal tools are the
only way a session reads them. They follow ``context.search`` (#5688) throughout:

* **Admission.** Authenticate, check the live catalog, refuse an exposed reader or a goal of
  third-party origin (``POLICY``), find the domains the grant covers, then charge one call on
  the goal budget and the invocation's share (``BUDGET`` when spent), all in one transaction
  and before any source is asked.
* **Grant-scoped.** A domain's guidance is reachable only when the invocation holds a grant
  on a capability of that domain that the catalog lists at its depth. A child, holding a
  subset of its lead's grant, reaches a subset of its guidance: a brief cannot name its way
  past that.
* **Closed arguments.** ``guidance.search`` takes ``query`` and an optional ``k``;
  ``guidance.load`` takes one ``ref`` of the form ``<domain>:<name>``, as search returns it
  and as a brief's ``guidance_refs`` name it. A reference outside the grant, or one its
  domain does not know, is refused ``RESOURCE``.
* **S5 trust rules.** Guidance steers a model, so only owner-authored (``owner`` or
  ``owner_correction``), provably untainted text is inlined. Everything else is a bare
  reference with its authority and taint, never its text.
* **Read-only.** No effect row; what was inlined joins the reader's label (D6).
* **Sources see no caller.** A source gets the closed query or name and a
  ``GuidanceReader`` (depth only): never a token, a goal or a grant.
"""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from opentelemetry import trace

from butlers.capability_broker.contracts import BrokerRefused, Principal, Reason
from butlers.capability_broker.policy import listable
from butlers.capability_broker.service import Broker
from butlers.core.goal_labels import Label, output_label

logger = logging.getLogger(__name__)
_tracer = trace.get_tracer(__name__)

SEARCH_TOOL = "guidance__search"
LOAD_TOOL = "guidance__load"
TOOLS = (SEARCH_TOOL, LOAD_TOOL)

_OWNER_AUTHORED = frozenset({"owner", "owner_correction"})
_AUTHORITIES = ("owner_correction", "owner", "domain", "third_party", "unknown")
_CREDENCE = {name: rank for rank, name in enumerate(_AUTHORITIES)}
_NAME_CHARS = 128
_SHORT_CHARS = 200


@dataclass(frozen=True, slots=True)
class GuidanceLimits:
    """Effective limits; every value finite and positive, or construction refuses."""

    query_bytes: int = 1024
    k_max: int = 20
    per_domain_s: float = 1.0
    total_s: float = 2.0
    excerpt_chars: int = 480
    text_chars: int = 16_000

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"guidance limit {name} must be finite and positive")
        if self.per_domain_s > self.total_s:
            raise ValueError("per-domain deadline cannot exceed the total deadline")


@dataclass(frozen=True, slots=True)
class GuidanceItem:
    """One guidance document as its domain holds it."""

    name: str
    title: str
    version: str
    text: str
    authority: str = "unknown"
    tainted: bool | None = None
    """``False`` only when the document is provably untainted; ``None`` is unknown."""


@dataclass(frozen=True, slots=True)
class GuidanceReader:
    """What a source may know about its reader: its depth, nothing else."""

    depth: int


class GuidanceSource(Protocol):
    """A domain's read-only guidance store; it must not record the read."""

    async def search(
        self, query: str, k: int, reader: GuidanceReader
    ) -> tuple[GuidanceItem, ...]: ...

    async def load(self, name: str, reader: GuidanceReader) -> GuidanceItem | None: ...


def _refuse() -> BrokerRefused:
    return BrokerRefused(Reason.POLICY)


def inlined(item: GuidanceItem) -> bool:
    """S5: only owner-authored, provably untainted guidance text may reach a model."""
    return item.authority in _OWNER_AUTHORED and item.tainted is False


def _valid(item: Any) -> bool:
    return (
        isinstance(item, GuidanceItem)
        and all(type(v) is str for v in (item.name, item.title, item.version, item.text))
        and 0 < len(item.name) <= _NAME_CHARS
        and (item.tainted is None or type(item.tainted) is bool)
    )


def _view(domain: str, item: GuidanceItem, field: str, limit: int) -> dict[str, Any]:
    inline = inlined(item)
    return {
        "ref": f"{domain}:{item.name}",
        "domain": domain,
        # Title and version are the source's text too: shown only with the body.
        "title": item.title[:_SHORT_CHARS] if inline else None,
        "version": item.version[:_SHORT_CHARS] if inline else None,
        "authority": item.authority if item.authority in _CREDENCE else "unknown",
        "taint": "unknown"
        if item.tainted is None
        else ("tainted" if item.tainted else "untainted"),
        "inline": inline,
        field: item.text[:limit] if inline else None,
        "truncated": inline and len(item.text) > limit,
    }


def parse_search(arguments: Any, limits: GuidanceLimits) -> tuple[str, int]:
    if type(arguments) is not dict or "query" not in arguments:
        raise _refuse()
    if not set(arguments) <= {"query", "k"}:
        raise _refuse()
    text = arguments["query"]
    if type(text) is not str or not text.strip():
        raise _refuse()
    try:
        size = len(text.encode("utf-8"))
    except UnicodeEncodeError:
        raise _refuse() from None
    if size > limits.query_bytes:
        raise _refuse()
    k = arguments.get("k", min(10, limits.k_max))
    if type(k) is not int or not 1 <= k <= limits.k_max:
        raise _refuse()
    return text, k


def parse_ref(arguments: Any) -> tuple[str, str]:
    """``{"ref": "<domain>:<name>"}``; a malformed reference names nothing, so it is unknown."""
    if type(arguments) is not dict or set(arguments) != {"ref"}:
        raise _refuse()
    ref = arguments["ref"]
    if type(ref) is not str or len(ref) > _NAME_CHARS + 65:
        raise _refuse()
    domain, sep, name = ref.partition(":")
    if not sep or not domain or not name:
        raise BrokerRefused(Reason.RESOURCE)
    return domain, name


class Guidance:
    """The ``guidance.search`` and ``guidance.load`` goal tools."""

    def __init__(
        self,
        broker: Broker,
        sources: Mapping[str, GuidanceSource],
        limits: GuidanceLimits | None = None,
    ):
        self.broker = broker
        self.sources = dict(sources)
        self.limits = limits or GuidanceLimits()

    async def search(self, goal_id: UUID, token: str, arguments: Any) -> dict[str, Any]:
        query, k = parse_search(arguments, self.limits)
        with _tracer.start_as_current_span(
            "goal.guidance_search", record_exception=False, set_status_on_exception=False
        ) as span:
            principal, domains = await self._admit(goal_id, token, span)
            reader = GuidanceReader(principal.depth)
            outcomes = await self._fan_out(domains, query, k, reader)
            if not any(status == "ok" for status, _ in outcomes.values()):
                raise BrokerRefused(Reason.UNAVAILABLE)
            items = [(domain, item) for domain in sorted(outcomes) for item in outcomes[domain][1]]
            # Credence orders display; each domain's own rank orders within it.
            items.sort(key=lambda pair: _CREDENCE.get(pair[1].authority, _CREDENCE["unknown"]))
            items = items[:k]
            await self._join(goal_id, token, [item for _, item in items])
        return {
            "status": "complete"
            if all(status == "ok" for status, _ in outcomes.values())
            else "degraded",
            "hits": [_view(d, i, "excerpt", self.limits.excerpt_chars) for d, i in items],
            "sources": {name: outcomes[name][0] for name in sorted(outcomes)},
        }

    async def load(self, goal_id: UUID, token: str, arguments: Any) -> dict[str, Any]:
        domain, name = parse_ref(arguments)
        with _tracer.start_as_current_span(
            "goal.guidance_load", record_exception=False, set_status_on_exception=False
        ) as span:
            principal, domains = await self._admit(goal_id, token, span, domain)
            source = domains[domain]
            try:
                async with asyncio.timeout(self.limits.total_s):
                    item = await source.load(name, GuidanceReader(principal.depth))
            except Exception:
                logger.info("guidance source failed source=%s", domain)
                raise BrokerRefused(Reason.UNAVAILABLE) from None
            if item is None:
                raise BrokerRefused(Reason.RESOURCE)  # the domain does not know it
            if not _valid(item) or item.name != name:
                raise BrokerRefused(Reason.UNAVAILABLE)
            await self._join(goal_id, token, [item])
        return _view(domain, item, "text", self.limits.text_chars)

    async def _admit(
        self, goal_id: UUID, token: str, span: Any, wanted: str | None = None
    ) -> tuple[Principal, dict[str, GuidanceSource]]:
        """Authenticate, find the domains this grant covers, and charge the call.

        An exposed reader, or one of third-party origin, is refused before anything is
        charged. A load of a domain outside the grant is refused ``RESOURCE``, also
        before the charge; one the domain does not know is charged (it reached a source).
        """
        span.set_attribute("goal_id", str(goal_id))
        self.broker._require_available()
        async with self.broker._transaction() as ledger:
            principal = await ledger.authenticate(goal_id, token)
            span.set_attribute("invocation_id", str(principal.invocation_id))
            span.set_attribute("depth", principal.depth)
            catalog = await self.broker._live_catalog.load(ledger.connection)
            if catalog.digest != principal.catalog_digest:
                raise BrokerRefused(Reason.CATALOG)
            if principal.exposed or principal.origin == "third_party":
                raise BrokerRefused(Reason.POLICY)
            covered = set()
            for grant in principal.grants:
                capability = catalog.get(grant.capability)
                if (
                    capability is not None
                    and listable(capability, principal.depth)
                    and capability.domain in self.sources
                ):
                    covered.add(capability.domain)
            if wanted is not None and wanted not in covered:
                raise BrokerRefused(Reason.RESOURCE)
            if not covered:
                raise BrokerRefused(Reason.CAPABILITY)
            exhausted = await ledger.charge_call(principal)
        if exhausted is not None:
            raise exhausted
        domains = [wanted] if wanted is not None else sorted(covered)
        return principal, {name: self.sources[name] for name in domains}

    async def _fan_out(
        self,
        sources: Mapping[str, GuidanceSource],
        query: str,
        k: int,
        reader: GuidanceReader,
    ) -> dict[str, tuple[str, tuple[GuidanceItem, ...]]]:
        async def one(source: GuidanceSource) -> tuple[GuidanceItem, ...]:
            async with asyncio.timeout(self.limits.per_domain_s):
                return await source.search(query, k, reader)

        tasks = {name: asyncio.ensure_future(one(source)) for name, source in sources.items()}
        try:
            await asyncio.wait(tasks.values(), timeout=self.limits.total_s)
        finally:
            for task in tasks.values():
                if not task.done():
                    task.cancel()
        outcomes: dict[str, tuple[str, tuple[GuidanceItem, ...]]] = {}
        for name, task in tasks.items():
            if not task.done() or task.cancelled():
                outcomes[name] = ("timeout", ())
                continue
            error = task.exception()
            if isinstance(error, TimeoutError):
                outcomes[name] = ("timeout", ())
            elif error is not None:
                logger.info("guidance source failed source=%s", name)
                outcomes[name] = ("failed", ())
            else:
                result = task.result()
                if type(result) is not tuple or not all(_valid(item) for item in result):
                    outcomes[name] = ("failed", ())
                else:
                    outcomes[name] = ("ok", result[:k])
        return outcomes

    async def _join(self, goal_id: UUID, token: str, items: list[GuidanceItem]) -> None:
        """Inlined guidance is owner data; it joins the reader (D6). Nothing inlined, no join."""
        if not any(inlined(item) for item in items):
            return
        label: Label = output_label("trusted", None, frozenset())
        async with self.broker._transaction() as ledger:
            principal = await ledger.authenticate(goal_id, token)
            await ledger._lock_goal(goal_id)
            if not await ledger._join(goal_id, principal.invocation_id, label, "read"):
                raise BrokerRefused(Reason.UNAVAILABLE)


__all__ = [
    "LOAD_TOOL",
    "SEARCH_TOOL",
    "TOOLS",
    "Guidance",
    "GuidanceItem",
    "GuidanceLimits",
    "GuidanceReader",
    "GuidanceSource",
    "inlined",
    "parse_ref",
    "parse_search",
]
