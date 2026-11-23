"""The read_check feed: one trusted path, per-watch cursors, blind on any failure."""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from butlers.conditions import ConditionEvaluator, Grant, Snapshot, State
from butlers.conditions.memory import InMemoryConditionStore
from butlers.watches import OwnerApproval, WatchRefused, WatchService, WatchState
from butlers.watches import feed as feedmod
from butlers.watches.feed import (
    EPOCH,
    FeedUnavailable,
    ReadCapability,
    ReadFeed,
    capability,
    micros,
    parse_readings,
    validate_reads,
)
from butlers.watches.memory import InMemoryWatchStore
from butlers.watches.runner import WatchRunner

NOW = datetime(2026, 11, 24, 9, 0, tzinfo=UTC)
CAP = "finance.transaction_amount.usd"
SOURCE = f"read_check:{CAP}"


def _cap() -> ReadCapability:
    cap = capability(CAP)
    assert cap is not None
    return cap


def predicate(op="gt", value=500, field="amount", capability=CAP):
    return {
        "tag": "read_check",
        "capability": capability,
        "field": field,
        "op": op,
        "value": value,
    }


def row(at: datetime, amount="900.00", currency="USD", ident=None):
    return {
        "id": ident or str(uuid4()),
        "amount": amount,
        "currency": currency,
        "recorded_us": micros(at),
    }


class Transport:
    """Serves the rows after the asked cursor; records every call."""

    def __init__(self, rows=None, error=None, raw=None):
        self.rows, self.error, self.raw, self.calls = list(rows or []), error, raw, []

    async def read(self, butler, tool, args):
        self.calls.append((butler, tool, dict(args)))
        if self.error is not None:
            raise self.error
        if self.raw is not None:
            return self.raw
        rows = [r for r in self.rows if r["recorded_us"] > args["after_us"]]
        return {"rows": rows[: args["limit"]]}


def grant_for(watch_id):
    return Grant(goal_id=watch_id, sources=frozenset({SOURCE}))


@pytest.fixture
def service():
    return WatchService(
        InMemoryWatchStore(), ConditionEvaluator(InMemoryConditionStore()), grant_for
    )


async def make_watch(service, spec_predicate=None):
    card = service.propose_create({"predicate": spec_predicate or predicate()}, now=NOW)
    return await service.approve(
        card, OwnerApproval(card_digest=card.digest, origin="owner"), Snapshot(now=NOW)
    )


class Recorder:
    def __init__(self):
        self.keys = []

    async def __call__(self, notice):
        self.keys.append(notice.dedup_key)


async def state_of(service, watch):
    instance = await service.instance(await service.store.get(watch.id))
    return instance.state


class TestFeedFires:
    async def test_a_large_reading_fires_the_watch_once(self, service):
        watch = await make_watch(service)
        transport = Transport([row(NOW - timedelta(minutes=1))])
        report = await ReadFeed(transport).poll(service, NOW)
        assert (report.polled, report.events, report.failed) == (1, 1, 0)
        assert await state_of(service, watch) is State.FIRED
        assert report.healthy == {CAP}

    async def test_a_small_reading_does_not_fire(self, service):
        watch = await make_watch(service)
        transport = Transport([row(NOW - timedelta(minutes=1), amount="12.00")])
        await ReadFeed(transport).poll(service, NOW)
        assert await state_of(service, watch) is State.OPEN

    async def test_the_first_poll_starts_no_earlier_than_the_realtime_horizon(self, service):
        await make_watch(service)
        transport = Transport()
        await ReadFeed(transport).poll(service, NOW)
        ((_, _, args),) = transport.calls
        assert args["after_us"] == micros(NOW - feedmod.REALTIME_HORIZON)
        assert args == {"currency": "usd", "after_us": args["after_us"], "limit": 100}

    async def test_a_reading_older_than_the_horizon_never_wakes_a_watch(self, service):
        watch = await make_watch(service)
        old = row(NOW - timedelta(hours=3))
        await ReadFeed(Transport([old], raw={"rows": [old]})).poll(service, NOW)
        assert await state_of(service, watch) is State.OPEN

    async def test_a_poll_is_made_once_per_capability_and_cursor_not_per_watch(self, service):
        first = await make_watch(service)
        second = await make_watch(service)
        transport = Transport([row(NOW - timedelta(minutes=1))])
        report = await ReadFeed(transport).poll(service, NOW)
        assert len(transport.calls) == 1 and report.polled == 1
        assert await state_of(service, first) is State.FIRED
        assert await state_of(service, second) is State.FIRED

    async def test_every_poll_re_reads_the_horizon_and_a_replay_is_harmless(self, service):
        watch = await make_watch(service)
        first, second = NOW - timedelta(minutes=4), NOW - timedelta(minutes=2)
        transport = Transport([row(first, "10.00"), row(second, "11.00")])
        feed = ReadFeed(transport)
        await feed.poll(service, NOW)
        await feed.poll(service, NOW)  # the same readings again
        horizon = micros(NOW - feedmod.REALTIME_HORIZON)
        assert [c[2]["after_us"] for c in transport.calls] == [horizon, horizon]
        assert await state_of(service, watch) is State.OPEN

    async def test_a_row_that_commits_late_with_an_earlier_time_still_fires_once(self, service):
        watch = await make_watch(service)
        small = row(NOW - timedelta(minutes=2), "10.00")
        transport = Transport([small])
        feed = ReadFeed(transport)
        await feed.poll(service, NOW)  # a later, small row is read and the cursor moves on
        late = row(NOW - timedelta(minutes=5), "900.00")  # committed after, created_at earlier
        transport.rows.append(late)
        transport.rows.sort(key=lambda r: r["recorded_us"])
        await feed.poll(service, NOW)
        assert await state_of(service, watch) is State.FIRED
        recorder = Recorder()
        runner = WatchRunner(service, recorder, lambda now: Snapshot(now=now))
        await runner.tick(NOW)
        await feed.poll(service, NOW)  # and the late row is not fired a second time
        await runner.tick(NOW)
        assert recorder.keys == [f"owner_watch:{watch.id}:g1"]

    async def test_a_window_over_one_page_is_paged_through(self, service):
        watch = await make_watch(service)
        base = NOW - timedelta(minutes=9)
        rows = [row(base + timedelta(seconds=i), "1.00") for i in range(150)]
        rows.append(row(base + timedelta(seconds=200), "900.00"))
        transport = Transport(rows)
        report = await ReadFeed(transport).poll(service, NOW)
        assert len(transport.calls) == 2 and report.events == 151
        assert await state_of(service, watch) is State.FIRED

    async def test_a_window_past_the_page_cap_reads_blind_not_partial(self, service, monkeypatch):
        monkeypatch.setattr(feedmod, "MAX_PAGES", 2)
        monkeypatch.setattr(feedmod, "MAX_ROWS", 2)
        watch = await make_watch(service)
        base = NOW - timedelta(minutes=9)
        rows = [row(base + timedelta(seconds=i), "1.00") for i in range(5)]
        rows.append(row(base + timedelta(seconds=9), "900.00"))
        report = await ReadFeed(Transport(rows)).poll(service, NOW)
        assert report.failed == 1 and report.healthy == frozenset()
        assert await state_of(service, watch) is State.OPEN

    async def test_a_crash_after_the_read_re_polls_without_a_second_fire(self, service):
        watch = await make_watch(service)
        transport = Transport([row(NOW - timedelta(minutes=1))])
        feed = ReadFeed(transport)
        await feed.poll(service, NOW)
        instance = await service.instance(await service.store.get(watch.id))
        # The same event id again (a poll whose commit was lost) is a no-op on a fired instance.
        await feed.poll(service, NOW)
        again = await service.instance(await service.store.get(watch.id))
        assert again.version == instance.version and again.state is State.FIRED

    async def test_the_rest_of_a_batch_after_a_fire_reaches_the_next_generation(self, service):
        watch = await make_watch(service)
        first, second = NOW - timedelta(minutes=3), NOW - timedelta(minutes=2)
        transport = Transport([row(first), row(second)])
        recorder = Recorder()
        feed = ReadFeed(transport)
        await feed.poll(service, NOW)  # fires on the first; the second is not consumed
        runner = WatchRunner(service, recorder, lambda now: Snapshot(now=now))
        assert (await runner.tick(NOW)).rearmed == 1  # g2 floors at the first reading
        await feed.poll(service, NOW)
        assert transport.calls[-1][2]["after_us"] == micros(first)
        assert await state_of(service, watch) is State.FIRED  # the second fires g2
        await runner.tick(NOW)
        assert recorder.keys == [f"owner_watch:{watch.id}:g1", f"owner_watch:{watch.id}:g2"]


class TestBlind:
    @pytest.mark.parametrize(
        "transport",
        [
            Transport(error=ConnectionError("down")),
            Transport(error=TimeoutError()),
            Transport(raw={"rows": [], "extra": 1}),
            Transport(raw="not json"),
            Transport(raw={"rows": "x"}),
        ],
        ids=["connection", "timeout", "extra key", "not a mapping", "rows not a list"],
    )
    async def test_a_failed_or_off_schema_poll_leaves_the_watch_blind_never_fired(
        self, service, transport
    ):
        watch = await make_watch(service)
        report = await ReadFeed(transport).poll(service, NOW)
        assert report.failed == 1 and report.healthy == frozenset()
        assert await state_of(service, watch) is State.OPEN

    async def test_one_bad_row_fails_the_whole_poll(self, service):
        watch = await make_watch(service)
        good = row(NOW - timedelta(minutes=3))
        bad = {**row(NOW - timedelta(minutes=2)), "amount": "NaN"}
        report = await ReadFeed(Transport(raw={"rows": [good, bad]})).poll(service, NOW)
        assert report.failed == 1
        assert await state_of(service, watch) is State.OPEN  # not even the good row counted

    async def test_a_paused_watch_is_not_polled(self, service):
        watch = await make_watch(service)
        await service.pause(watch.id, origin="owner", now=NOW)
        transport = Transport()
        report = await ReadFeed(transport).poll(service, NOW)
        assert report.polled == 0 and transport.calls == []
        assert (await service.store.get(watch.id)).state is WatchState.PAUSED

    async def test_calls_per_tick_are_bounded(self, service, monkeypatch):
        monkeypatch.setattr(feedmod, "MAX_CALLS_PER_TICK", 1)
        for _ in range(2):
            await make_watch(service)
        transport = Transport()
        # Two watches, one cursor: one call. A second cursor would be the second call.
        report = await ReadFeed(transport).poll(service, NOW)
        assert report.polled == 1 and report.skipped == 0


def _wide_service():
    sources = frozenset(
        f"{feedmod.SOURCE_PREFIX}finance.transaction_amount.{c}" for c in sorted(feedmod.CURRENCIES)
    )
    return WatchService(
        InMemoryWatchStore(),
        ConditionEvaluator(InMemoryConditionStore()),
        lambda watch_id: Grant(goal_id=watch_id, sources=sources),
    )


class TestCallBudget:
    async def _twelve(self):
        service = _wide_service()
        for code in sorted(feedmod.CURRENCIES):
            await make_watch(service, predicate(capability=f"finance.transaction_amount.{code}"))
        return service

    async def test_twelve_currencies_under_a_ten_call_budget_skip_two_and_never_cover_them(self):
        service = await self._twelve()
        assert len(feedmod.CURRENCIES) == 12 and feedmod.MAX_CALLS_PER_TICK == 10
        transport = Transport()
        report = await ReadFeed(transport).poll(service, NOW)
        assert (report.polled, report.skipped, report.failed) == (10, 2, 0)
        polled = {args["currency"] for _, _, args in transport.calls}
        assert len(report.healthy) == 10 and len(report.unhealthy) == 2
        assert {c.rsplit(".", 1)[1] for c in report.unhealthy}.isdisjoint(polled)
        assert report.healthy.isdisjoint(report.unhealthy)

    async def test_a_skipped_key_degrades_the_tick(self):
        from butlers.watches import tick

        service = await self._twelve()
        providers = SimpleNamespace(feed_for=lambda: ReadFeed(Transport()))
        assert await tick._poll_feed(providers, service, NOW, HealthPool()) is False

    async def test_the_start_rotates_so_every_key_is_reached_within_twelve_ticks(self):
        service = await self._twelve()
        reached: set[str] = set()
        for minute in range(12):
            transport = Transport()
            await ReadFeed(transport).poll(service, NOW + timedelta(minutes=minute))
            reached |= {args["currency"] for _, _, args in transport.calls}
        assert reached == set(feedmod.CURRENCIES)

    async def test_no_key_is_skipped_on_more_than_two_of_twelve_ticks(self):
        service = await self._twelve()
        skipped: dict[str, int] = {}
        for minute in range(12):
            report = await ReadFeed(Transport()).poll(service, NOW + timedelta(minutes=minute))
            for name in report.unhealthy:
                skipped[name] = skipped.get(name, 0) + 1
        assert max(skipped.values()) == 2

    async def test_a_skipped_watch_is_not_evaluated(self):
        service = await self._twelve()

        class Echo(Transport):
            async def read(self, butler, tool, args):
                self.calls.append((butler, tool, dict(args)))
                return {
                    "rows": [row(NOW - timedelta(minutes=2), currency=args["currency"].upper())]
                }

        await ReadFeed(Echo()).poll(service, NOW)
        fired = [
            w
            for w in await service.store.list_all()
            if (await service.instance(w)).state is not State.OPEN
        ]
        assert len(fired) == 10  # the ten polled currencies fire, the two skipped stay open


class TestSchema:
    cap = _cap()

    @pytest.mark.parametrize(
        "mutation",
        [
            {"amount": "-1.00"},
            {"amount": "1e16"},
            {"amount": "0.0000001"},
            {"amount": 5},
            {"currency": "EUR"},
            {"id": "not-a-uuid"},
            {"recorded_us": True},
            {"recorded_us": "1"},
            {"recorded_us": 0},
        ],
        ids=lambda m: str(m),
    )
    def test_a_row_off_schema_is_refused(self, mutation):
        bad = {**row(NOW), **mutation}
        with pytest.raises(FeedUnavailable):
            parse_readings(self.cap, {"rows": [bad]}, 1)

    def test_a_row_with_an_extra_field_is_refused(self):
        with pytest.raises(FeedUnavailable):
            parse_readings(self.cap, {"rows": [{**row(NOW), "merchant": "x"}]}, 1)

    def test_rows_must_ascend_and_stay_within_the_bound(self):
        later, earlier = row(NOW), row(NOW - timedelta(minutes=1))
        with pytest.raises(FeedUnavailable):
            parse_readings(self.cap, {"rows": [later, earlier]}, 1)
        with pytest.raises(FeedUnavailable):
            parse_readings(
                self.cap, {"rows": [row(EPOCH + timedelta(seconds=i + 1)) for i in range(101)]}, 0
            )

    def test_the_decimal_reading_becomes_a_float(self):
        (reading,) = parse_readings(self.cap, {"rows": [row(NOW, "500.01")]}, 1)
        assert reading.value == 500.01


class TestRegistry:
    def test_only_the_finance_amount_capability_per_known_currency_is_registered(self):
        assert capability("finance.transaction_amount.usd") is not None
        for name in ("finance.transaction_amount.xxx", "finance.merchant.usd", "x", ""):
            assert capability(name) is None

    def test_the_capability_calls_one_finance_read_tool(self):
        cap = _cap()
        assert (cap.butler, cap.tool) == ("finance", "transaction_amounts_since")

    @pytest.mark.parametrize(
        "spec, message",
        [
            (predicate(capability="finance.merchant.usd"), "no feed measures"),
            (predicate(field="merchant"), "measures only amount"),
            (predicate(op="lt"), "supports only"),
            (predicate(op="eq"), "supports only"),
        ],
    )
    def test_a_read_check_no_feed_can_measure_is_refused(self, spec, message):
        with pytest.raises(WatchRefused, match=message):
            validate_reads(spec)

    def test_a_sound_read_check_passes_even_inside_a_rule(self):
        validate_reads({"tag": "all", "children": [predicate(), predicate(op="ge", value=1)]})

    async def test_an_unsound_op_is_not_measurable_for_a_leaf_already_registered(self, service):
        watch = await make_watch(service, predicate(op="ge", value=1))
        instance = await service.instance(await service.store.get(watch.id))
        assert instance is not None
        assert feedmod._sound_for(_cap(), instance)


def _trusted_sites(source: str) -> list[str]:
    """Every way a source can set ``trusted`` to anything but a literal False."""
    sites = []
    mints = {"ConditionEvent", "replace", "evolve"}
    names_event = "ConditionEvent" in source
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            callee = ast.unparse(node.func).rsplit(".", 1)[-1]
            for keyword in node.keywords:
                if keyword.arg == "trusted" and (callee in mints or names_event):
                    if not (
                        isinstance(keyword.value, ast.Constant) and keyword.value.value is False
                    ):
                        sites.append(f"{callee}(trusted=...)")
                elif keyword.arg is None and callee in mints:
                    sites.append(f"{callee}(**...)")  # an unpacked mapping can carry trusted
            if callee == "setattr" and len(node.args) >= 2:
                name = node.args[1]
                if isinstance(name, ast.Constant) and name.value == "trusted":
                    sites.append("setattr(..., 'trusted', ...)")
        elif isinstance(node, ast.Dict) and names_event:
            for key, value in zip(node.keys, node.values, strict=True):
                if isinstance(key, ast.Constant) and key.value == "trusted":
                    if not (isinstance(value, ast.Constant) and value.value is False):
                        sites.append("{'trusted': ...}")
    return sites


def test_trusted_events_are_constructed_in_exactly_one_place():
    """No other module may mint a trusted event: one trust path (REQ-owner-watches-002).

    The scan reads every source file's AST, not only files that name ``ConditionEvent``, so
    ``dataclasses.replace(event, trusted=True)``, a ``**kwargs`` construction, ``setattr`` and a
    dict carrying the key are all findings.
    """
    root = Path(__file__).resolve().parents[2]
    found = {}
    for base in ("src", "roster"):
        for path in (root / base).rglob("*.py"):
            if "/tests/" in path.as_posix():
                continue
            sites = _trusted_sites(path.read_text(encoding="utf-8"))
            if sites:
                found[path.relative_to(root).as_posix()] = sites
    assert found == {"src/butlers/watches/feed.py": ["ConditionEvent(trusted=...)"]}


@pytest.mark.parametrize(
    "source",
    [
        "dataclasses.replace(ev, trusted=True)",
        "replace(ev, trusted=flag)",
        "ConditionEvent(**fields)",
        "replace(ev, **fields)",
        "setattr(ev, 'trusted', True)",
        "ConditionEvent\nfields = {'trusted': True}",
    ],
)
def test_the_trust_scan_catches_every_construction_shape(source):
    assert _trusted_sites(source)


@pytest.mark.parametrize(
    "source", ["ConditionEvent(trusted=False)", "fields = {'trusted': False}", "f(x=1)"]
)
def test_the_trust_scan_ignores_an_untrusted_event(source):
    assert _trusted_sites(source) == []


class Reporter:
    def __init__(self):
        self.reports = []

    async def report(self, dependency, state, reason):
        self.reports.append((state, reason))


class Pool:
    async def execute(self, sql, *args):
        return "DELETE 0"


def providers_with(service, feed):
    from butlers.watches.approval import Providers

    async def prepare(now):
        return True

    return Providers(
        service_for=lambda: service,
        snapshot_for=lambda now: Snapshot(now=now),
        prepare=prepare,
        feed_for=lambda: feed,
    )


class TestTick:
    async def test_a_reading_fires_and_the_same_tick_delivers_one_notice(self, service):
        from butlers.watches import tick

        watch = await make_watch(service)
        feed = ReadFeed(Transport([row(NOW - timedelta(minutes=1))]))
        recorder, reporter = Recorder(), Reporter()
        result = await tick.run_tick(
            Pool(),
            now=NOW,
            live=True,
            providers=providers_with(service, feed),
            reporter=reporter,
            deliver_for=lambda pool: recorder,
        )
        assert result["delivered"] == 1 and result["feed_ok"] is True
        assert recorder.keys == [f"owner_watch:{watch.id}:g1"]
        assert reporter.reports == [("up", "tick_ok")]

    @pytest.mark.parametrize("failure", ["poll", "broken"])
    async def test_a_failed_feed_fires_nothing_reports_degraded_and_the_tick_continues(
        self, service, failure
    ):
        from butlers.watches import tick

        watch = await make_watch(service)
        if failure == "poll":
            feed = ReadFeed(Transport(error=ConnectionError("down")))
        else:

            class Broken:
                async def poll(self, service, now):
                    raise RuntimeError("boom")

            feed = Broken()
        recorder, reporter = Recorder(), Reporter()
        result = await tick.run_tick(
            Pool(),
            now=NOW,
            live=True,
            providers=providers_with(service, feed),
            reporter=reporter,
            deliver_for=lambda pool: recorder,
        )
        assert result["status"] == "ok" and result["feed_ok"] is False
        assert recorder.keys == [] and await state_of(service, watch) is State.OPEN
        assert reporter.reports == [("degraded", "feed_unreadable")]

    async def test_no_feed_is_a_healthy_tick(self, service):
        from butlers.watches import tick

        reporter = Reporter()
        result = await tick.run_tick(
            Pool(),
            now=NOW,
            live=True,
            providers=providers_with(service, None),
            reporter=reporter,
            deliver_for=lambda pool: Recorder(),
        )
        assert result["feed_ok"] is True and reporter.reports == [("up", "tick_ok")]


class TestRegistrationRefusal:
    async def test_a_service_with_the_feed_validator_refuses_what_no_feed_measures(self):
        guarded = WatchService(
            InMemoryWatchStore(),
            ConditionEvaluator(InMemoryConditionStore()),
            grant_for,
            validate_reads=validate_reads,
        )
        with pytest.raises(WatchRefused, match="supports only"):
            guarded.propose_create({"predicate": predicate(op="lt")}, now=NOW)
        card = guarded.propose_create({"predicate": predicate()}, now=NOW)
        assert card.sources == (SOURCE,)


class TestHealthSets:
    async def test_a_failed_poll_marks_the_capability_unhealthy_never_healthy(self, service):
        await make_watch(service)
        report = await ReadFeed(Transport(error=ConnectionError("down"))).poll(service, NOW)
        assert report.healthy == frozenset() and report.unhealthy == {CAP}


class HealthPool(Pool):
    def __init__(self):
        self.calls = []

    async def execute(self, sql, *args):
        self.calls.append((" ".join(sql.split())[:40], args))
        return "OK"


class TestFeedHealthPublish:
    async def test_the_tick_publishes_healthy_and_clears_unhealthy_capabilities(self, service):
        from butlers.watches import tick

        await make_watch(service)
        pool = HealthPool()
        feed = ReadFeed(Transport())
        await tick.run_tick(
            pool,
            now=NOW,
            live=True,
            providers=providers_with(service, feed),
            deliver_for=lambda p: Recorder(),
        )
        assert any(a == ("owner_watch:feed_health", [CAP]) for _, a in pool.calls)
        bad = HealthPool()
        await tick.run_tick(
            bad,
            now=NOW,
            live=True,
            providers=providers_with(service, ReadFeed(Transport(error=OSError("x")))),
            deliver_for=lambda p: Recorder(),
        )
        cleared = [c for c in bad.calls if c[0].startswith("UPDATE state SET value")]
        assert cleared and cleared[0][1] == ("owner_watch:feed_health", [CAP])
        assert not any(c[0].startswith("INSERT INTO state") for c in bad.calls)

    async def test_a_budget_skipped_capability_is_cleared_never_published_healthy(self):
        from butlers.watches import tick

        service = _wide_service()
        for code in sorted(feedmod.CURRENCIES):
            await make_watch(service, predicate(capability=f"finance.transaction_amount.{code}"))
        pool = HealthPool()
        result = await tick.run_tick(
            pool,
            now=NOW,
            live=True,
            providers=providers_with(service, ReadFeed(Transport())),
            deliver_for=lambda p: Recorder(),
        )
        published = next(a[1] for sql, a in pool.calls if sql.startswith("INSERT INTO state"))
        cleared = next(a[1] for sql, a in pool.calls if sql.startswith("UPDATE state SET value"))
        assert len(published) == 10 and len(cleared) == 2
        assert set(published).isdisjoint(cleared)
        assert result["feed_ok"] is False  # a skipped key degrades the tick

    async def test_a_health_publish_failure_never_stops_the_tick(self, service):
        from butlers.watches import tick

        class Broken(Pool):
            async def execute(self, sql, *args):
                raise OSError("state unavailable")

        await make_watch(service)
        result = await tick.run_tick(
            Broken(),
            now=NOW,
            live=True,
            providers=providers_with(service, ReadFeed(Transport())),
            deliver_for=lambda p: Recorder(),
        )
        assert result["status"] == "ok"
