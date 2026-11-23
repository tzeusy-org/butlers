"""The read_check feed: the one place a trusted measurement event is made (REQ-owner-watches-002).

A ``read_check`` leaf waits on one registered, owner-granted read capability. Nothing pushes a
reading to a watch: each tick the feed asks the capability's owning butler for the readings newer
than a watch's own cursor, over the Switchboard's route to that butler, as the owner and read-only.
This is not the goal capability broker (a watch is not a goal session); the registry below is the
whole allowlist and names the one tool each capability may call.

Trust has one path. A trusted ``ConditionEvent`` is built in ``reading_event`` and nowhere
else (a guard test enforces it), only from a payload that passed the strict schema check in
``parse_readings``. A reading that is off schema, out of bounds, from a failed call or from the
wrong currency is never an event: the poll fails, the watch stays blind (its leaf unknown, nothing
fires) and the caller reports the feed as unmeasurable.

A watch is polled over the whole realtime horizon past its floor, so a row that commits late with an
earlier recorded time is still read. The event position is the reading's recorded time in
microseconds, so a fire that stops a batch short is replayed to the next generation from its floor
(older evidence is historical and never wakes a watch). Readings recorded in the same instant are
one reading: the owner's tool returns the largest, which is all a ``gt``/``ge`` leaf can use.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from butlers.conditions import ConditionEvent, Instance, State
from butlers.conditions.evaluate import REALTIME_HORIZON
from butlers.conditions.predicate import leaves
from butlers.core.goal_labels import Label
from butlers.watches.model import WatchRefused, WatchState

logger = logging.getLogger(__name__)

SOURCE_PREFIX = "read_check:"
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
MAX_ROWS = 100
CALL_DEADLINE_S = 10.0
MAX_CALLS_PER_TICK = 10
#: Pages per capability per tick; more readings than this in one window reads blind, not partial.
MAX_PAGES = 10
#: Only these comparisons are sound over one reading per instant (the largest one).
ORDERED_UP = frozenset({"gt", "ge"})
#: A bound on a reading, the same one the predicate validator applies to a read_check number.
MAX_AMOUNT = Decimal("1e15")
CURRENCIES = frozenset(
    {"usd", "eur", "gbp", "sgd", "jpy", "aud", "cad", "chf", "cny", "hkd", "myr", "nzd"}
)
_FINANCE = re.compile(r"finance\.transaction_amount\.([a-z]{3})")
_SOURCE = "read_check:finance.transaction_amount."


class FeedUnavailable(RuntimeError):
    """The reading could not be had or did not pass the schema: the watch stays blind."""


@dataclass(frozen=True)
class ReadCapability:
    name: str
    butler: str
    tool: str
    currency: str
    field: str = "amount"
    ops: frozenset[str] = ORDERED_UP

    @property
    def source(self) -> str:
        return SOURCE_PREFIX + self.name


def capability(name: str) -> ReadCapability | None:
    """The registered capability of this name, or None: the allowlist is this function."""
    match = _FINANCE.fullmatch(name)
    if match is None or match.group(1) not in CURRENCIES:
        return None
    return ReadCapability(
        name=name,
        butler="finance",
        tool="transaction_amounts_since",
        currency=match.group(1),
    )


def registered_sources() -> frozenset[str]:
    """Every read source a watch may name: the sources the production grant offers."""
    return frozenset(f"{_SOURCE}{code}" for code in CURRENCIES)


def validate_reads(predicate: Mapping[str, Any]) -> None:
    """Refuse a read_check no feed can measure: unregistered, wrong field or an unsound op."""
    for leaf in leaves(dict(predicate)):
        if leaf.tag != "read_check":
            continue
        cap = capability(leaf.body["capability"])
        if cap is None:
            raise WatchRefused("a read_check names a capability no feed measures")
        if leaf.body["field"] != cap.field:
            raise WatchRefused(f"{cap.name} measures only {cap.field}")
        if leaf.body["op"] not in cap.ops:
            raise WatchRefused(f"{cap.name} supports only {sorted(cap.ops)}")


class ReadTransport(Protocol):
    async def read(self, butler: str, tool: str, args: dict[str, Any]) -> Any:
        """Call one read-only tool of one butler as the owner; raise on any failure."""


class SwitchboardReadTransport:
    """Calls one registered read tool of one butler over the Switchboard's own route.

    The owner is the principal and there is no goal session: the caller names only a registered
    capability's tool, so the allowlist is ``capability``. Any error, refusal or missing result
    raises, which the feed reports as a blind poll.
    """

    def __init__(self, pool: Any, route: Any = None) -> None:
        self._pool = pool
        self._route = route

    async def read(self, butler: str, tool: str, args: dict[str, Any]) -> Any:
        route = self._route
        if route is None:
            route = importlib.import_module("butlers.tools.switchboard.routing.route").route
        async with asyncio.timeout(CALL_DEADLINE_S):
            response = await route(self._pool, butler, tool, args, source_butler="switchboard")
        if type(response) is not dict or response.get("error") or "result" not in response:
            raise FeedUnavailable("the route refused or returned no result")
        result = response["result"]
        if type(result) is dict and "data" in result and "is_error" in result:
            if result["is_error"]:
                raise FeedUnavailable("the tool returned an error")
            result = result["data"]
        return result


@dataclass(frozen=True)
class Reading:
    position: int  # recorded time, microseconds since the epoch
    value: float
    ref: str  # the source row's id, never shown


def micros(moment: datetime) -> int:
    return (moment - EPOCH) // timedelta(microseconds=1)


def parse_readings(cap: ReadCapability, raw: object, after: int) -> list[Reading]:
    """The tool's payload, strictly: exactly ``rows`` of ``{id, amount, currency, recorded_us}``."""
    if type(raw) is not dict or set(raw) != {"rows"} or type(raw["rows"]) is not list:
        raise FeedUnavailable("the reading is not the declared shape")
    if len(raw["rows"]) > MAX_ROWS:
        raise FeedUnavailable("the reading is larger than its bound")
    readings: list[Reading] = []
    last = after
    for row in raw["rows"]:
        if type(row) is not dict or set(row) != {"id", "amount", "currency", "recorded_us"}:
            raise FeedUnavailable("a row is not the declared shape")
        ident, amount, currency, recorded = (
            row["id"],
            row["amount"],
            row["currency"],
            row["recorded_us"],
        )
        try:
            if type(ident) is not str or str(UUID(ident)) != ident:
                raise ValueError
            if type(amount) is not str or type(currency) is not str:
                raise ValueError
            if type(recorded) is not int or type(recorded) is bool or recorded <= last:
                raise ValueError  # strictly ascending and past the cursor
            value = Decimal(amount)
        except (ValueError, InvalidOperation):
            raise FeedUnavailable("a row is not the declared shape") from None
        if currency.lower() != cap.currency or not value.is_finite():
            raise FeedUnavailable("a row is not the declared currency or number")
        if value < 0 or value > MAX_AMOUNT or (value and value < Decimal("1e-6")):
            raise FeedUnavailable("a reading is outside its bounds")
        last = recorded
        readings.append(Reading(recorded, float(value), ident))
    return readings


def reading_event(
    cap: ReadCapability, reading: Reading, *, measurable: bool = True
) -> ConditionEvent:
    """The only constructor of a trusted event: it takes only a parsed ``Reading``."""
    return ConditionEvent(
        id=str(uuid5(NAMESPACE_URL, f"butlers:read-check:{cap.name}:{reading.position}")),
        source=cap.source,
        position=reading.position,
        at=EPOCH + timedelta(microseconds=reading.position),
        tag="read_check",
        data={"capability": cap.name, "field": cap.field, "value": reading.value},
        measurable=measurable,
        label=Label.unknown(),  # third-party-derived values keep the widest label
        trusted=True,
    )


@dataclass
class FeedReport:
    polled: int = 0
    events: int = 0
    failed: int = 0
    skipped: int = 0
    healthy: frozenset[str] = frozenset()


def _window_start(instance: Instance, source: str, now: datetime) -> int:
    """Where a poll starts: the window of evidence that can still wake, past the floor.

    It deliberately ignores the watch's cursor. A row of a long or concurrent finance transaction
    can commit after a later row was read yet carry an earlier ``created_at`` (``now()`` is the
    transaction start), so every poll re-reads the whole realtime horizon. The overlap is
    harmless: a non-firing reading is idempotent on its leaf, a firing one closes the instance,
    and the floor excludes what an earlier generation consumed. Older rows are historical and
    never wake a watch, whatever the cursor.
    """
    return max(instance.floor.get(source, -1), micros(now - REALTIME_HORIZON))


class ReadFeed:
    def __init__(self, transport: ReadTransport) -> None:
        self.transport = transport

    async def poll(self, service: Any, now: datetime) -> FeedReport:
        """One poll of every capability an open watch reads; failures leave watches blind."""
        report = FeedReport()
        healthy: set[str] = set()
        calls: dict[tuple[str, int], list[Reading] | None] = {}
        for watch in await service.store.list_all():
            if watch.state is not WatchState.ACTIVE:
                continue
            instance = await service.instance(watch)
            if instance is None or instance.state is not State.OPEN:
                continue
            for source in instance.source_boundary:
                cap = (
                    capability(source[len(SOURCE_PREFIX) :])
                    if source.startswith(SOURCE_PREFIX)
                    else None
                )
                if cap is None:
                    continue
                after = _window_start(instance, source, now)
                key = (cap.name, after)
                if key not in calls:
                    if len(calls) >= MAX_CALLS_PER_TICK:
                        report.skipped += 1
                        continue
                    calls[key] = await self._read(cap, after)
                    report.polled += 1
                readings = calls[key]
                if readings is None:
                    report.failed += 1
                    continue
                healthy.add(cap.name)
                if not readings:
                    continue
                sound = _sound_for(cap, instance)
                events = [reading_event(cap, r, measurable=sound) for r in readings]
                await service.evaluator.evaluate(instance.id, events, now=now)
                report.events += len(events)
        report.healthy = frozenset(healthy)
        return report

    async def _read(self, cap: ReadCapability, after: int) -> list[Reading] | None:
        """Every reading after ``after``, paged; None (blind) on any failure or an overflow."""
        readings: list[Reading] = []
        try:
            for _ in range(MAX_PAGES):
                args = {"currency": cap.currency, "after_us": after, "limit": MAX_ROWS}
                raw = await self.transport.read(cap.butler, cap.tool, args)
                page = parse_readings(cap, raw, after)
                readings += page
                if len(page) < MAX_ROWS:
                    return readings
                after = page[-1].position
            logger.warning("owner_watch_feed_overflow capability=%s", cap.name)
        except FeedUnavailable:
            logger.warning("owner_watch_feed_off_schema capability=%s", cap.name)
        except Exception:
            logger.warning("owner_watch_feed_unavailable capability=%s", cap.name, exc_info=True)
        return None


def _sound_for(cap: ReadCapability, instance: Instance) -> bool:
    """False when a leaf of this capability asks what one reading per instant cannot answer."""
    return all(
        leaf.body["op"] in cap.ops
        for leaf in leaves(instance.predicate)
        if leaf.tag == "read_check" and leaf.body["capability"] == cap.name
    )
