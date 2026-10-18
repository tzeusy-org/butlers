"""Home safety sentinel: leak, smoke, CO and gas alerts with no model (bu-jv1moa.6).

Unit tests pin the pure rules (debounce, identity, wording) and the orchestration around the
condition ledger.  The integration tests run the real ledger SQL against Postgres.
"""

from __future__ import annotations

import asyncio
import inspect
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import asyncpg
import httpx
import pytest

from butlers.core.condition_ledger import ConditionTransition
from butlers.core.owner_conditions import (
    create_owner_conditions_table,
    get_active_condition,
    list_conditions,
)
from butlers.jobs import home_safety
from butlers.jobs.home_safety import (
    DEBOUNCE_SECONDS,
    SAFETY_SOURCE,
    SensorReading,
    WatchEntry,
    alert_message,
    classify,
    reconcile_safety,
    run_safety_sentinel,
    safety_fingerprint,
)

_NOW = datetime(2026, 10, 18, 12, 0, 0, tzinfo=UTC)
_LEAK = WatchEntry("binary_sensor.kitchen_leak", "leak", "Kitchen leak sensor")


def _since(seconds: float) -> datetime:
    return _NOW - timedelta(seconds=seconds)


# ---------------------------------------------------------------------------
# Pure rules
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_a_sensor_must_hold_on_for_the_debounce_window_before_it_alarms() -> None:
    held = SensorReading("on", _since(DEBOUNCE_SECONDS))
    flapping = SensorReading("on", _since(DEBOUNCE_SECONDS - 1))

    assert classify(_LEAK, held, _NOW) == "alarm"
    assert classify(_LEAK, flapping, _NOW) == "pending"
    assert classify(_LEAK, SensorReading("on", None), _NOW) == "pending"


@pytest.mark.unit
@pytest.mark.parametrize("state", ["unavailable", "unknown", "", None])
def test_an_unreadable_sensor_is_unknown_never_clear(state) -> None:
    assert classify(_LEAK, SensorReading(state, _since(600)), _NOW) == "unknown"
    assert classify(_LEAK, None, _NOW) == "unknown"


@pytest.mark.unit
def test_only_an_explicit_off_is_clear() -> None:
    assert classify(_LEAK, SensorReading("off", _since(5)), _NOW) == "clear"
    assert classify(_LEAK, SensorReading("OFF", _since(5)), _NOW) == "clear"


@pytest.mark.unit
def test_fingerprint_is_the_entity_and_role_and_nothing_mutable() -> None:
    base = safety_fingerprint("binary_sensor.kitchen_leak", "leak")

    assert base == safety_fingerprint("binary_sensor.kitchen_leak", "leak")
    assert base != safety_fingerprint("binary_sensor.kitchen_leak", "smoke")
    assert base != safety_fingerprint("binary_sensor.bath_leak", "leak")


@pytest.mark.unit
def test_alert_text_uses_the_owner_label_and_a_fixed_hazard_phrase() -> None:
    text = alert_message(_LEAK, SensorReading("on", _since(150)), _NOW)

    assert text == "ALERT: Kitchen leak sensor: water leak detected (2 min ago)"


@pytest.mark.unit
def test_alert_text_never_carries_ha_naming_or_markdown_from_the_label() -> None:
    hostile = WatchEntry("binary_sensor.x", "co", "**Boiler** `room`_[x]")
    text = alert_message(hostile, SensorReading("on", _since(30)), _NOW)

    assert text == "ALERT: Boiler roomx: carbon monoxide detected (just now)"
    # The renderer takes no friendly_name argument at all.
    assert "friendly" not in " ".join(inspect.signature(alert_message).parameters)


@pytest.mark.unit
def test_the_module_has_no_actuation_path() -> None:
    source = inspect.getsource(home_safety)
    for marker in ("call_service", "/api/services", "ha_call_service", "httpx"):
        assert marker not in source


# ---------------------------------------------------------------------------
# Orchestration around the ledger (ledger calls stubbed)
# ---------------------------------------------------------------------------


def _transition(fingerprint: str, kind: str) -> ConditionTransition:
    import uuid

    return ConditionTransition(
        condition_id=uuid.uuid4(),
        source=SAFETY_SOURCE,
        fingerprint=fingerprint,
        episode=1,
        state="open",
        transition=kind,  # type: ignore[arg-type]
        escalation_level="L0",
        next_reescalate_at=None,
    )


@pytest.fixture
def ledger(monkeypatch: pytest.MonkeyPatch) -> dict[str, AsyncMock]:
    stubs = {
        "reconcile": AsyncMock(return_value=[]),
        "active": AsyncMock(return_value=None),
        "resolve": AsyncMock(return_value=None),
    }
    monkeypatch.setattr(home_safety, "reconcile_snapshot", stubs["reconcile"])
    monkeypatch.setattr(home_safety, "get_active_condition", stubs["active"])
    monkeypatch.setattr(home_safety, "resolve_condition", stubs["resolve"])
    return stubs


@pytest.mark.unit
async def test_a_debouncing_sensor_opens_nothing_and_is_carried_not_resolved(ledger) -> None:
    notify = AsyncMock(return_value=True)

    result = await reconcile_safety(
        object(),
        [_LEAK],
        {_LEAK.entity_id: SensorReading("on", _since(10))},
        now=_NOW,
        snapshot_complete=True,
        notify=notify,
    )

    observations = ledger["reconcile"].await_args.kwargs["observations"]
    assert [(o.confirm_only, o.summary) for o in observations] == [(True, None)]
    assert result.pending == 1 and result.alarms == 0
    notify.assert_not_awaited()


@pytest.mark.unit
async def test_unreadable_sensor_is_carried_so_a_complete_snapshot_cannot_resolve_it(
    ledger,
) -> None:
    await reconcile_safety(
        object(),
        [_LEAK],
        {_LEAK.entity_id: SensorReading("unavailable", _since(900))},
        now=_NOW,
        snapshot_complete=True,
        notify=AsyncMock(return_value=True),
    )

    assert [o.confirm_only for o in ledger["reconcile"].await_args.kwargs["observations"]] == [True]


@pytest.mark.unit
async def test_an_opened_alarm_notifies_once_and_records_the_delivery(ledger) -> None:
    fingerprint = safety_fingerprint(_LEAK.entity_id, "leak")
    ledger["reconcile"].side_effect = [[_transition(fingerprint, "opened")], []]
    notify = AsyncMock(return_value=True)

    result = await reconcile_safety(
        object(),
        [_LEAK],
        {_LEAK.entity_id: SensorReading("on", _since(120))},
        now=_NOW,
        snapshot_complete=True,
        notify=notify,
    )

    notify.assert_awaited_once_with("ALERT: Kitchen leak sensor: water leak detected (2 min ago)")
    assert result.notified == 1
    marker = ledger["reconcile"].await_args_list[1].kwargs["observations"][0]
    assert marker.confirm_only and "notified_at" in marker.metadata


@pytest.mark.unit
async def test_a_failed_notify_leaves_the_condition_undelivered_for_the_next_run(ledger) -> None:
    fingerprint = safety_fingerprint(_LEAK.entity_id, "leak")
    ledger["reconcile"].side_effect = [[_transition(fingerprint, "opened")]]
    notify = AsyncMock(side_effect=RuntimeError("telegram down"))

    result = await reconcile_safety(
        object(),
        [_LEAK],
        {_LEAK.entity_id: SensorReading("on", _since(120))},
        now=_NOW,
        snapshot_complete=True,
        notify=notify,
    )

    assert result.notify_failed == 1 and result.notified == 0
    assert ledger["reconcile"].await_count == 1  # no delivery marker written


@pytest.mark.unit
async def test_an_undelivered_condition_is_retried_once_it_is_old_enough(ledger) -> None:
    fingerprint = safety_fingerprint(_LEAK.entity_id, "leak")
    ledger["active"].return_value = {
        "metadata": {"entity_id": _LEAK.entity_id},
        "first_detected_at": _NOW - timedelta(minutes=5),
    }
    ledger["reconcile"].side_effect = [[_transition(fingerprint, "confirmed")], []]
    notify = AsyncMock(return_value=True)

    await reconcile_safety(
        object(),
        [_LEAK],
        {_LEAK.entity_id: SensorReading("on", _since(400))},
        now=_NOW,
        snapshot_complete=True,
        notify=notify,
    )

    notify.assert_awaited_once()


@pytest.mark.unit
async def test_a_delivered_condition_is_not_resent_on_a_plain_confirmation(ledger) -> None:
    fingerprint = safety_fingerprint(_LEAK.entity_id, "leak")
    ledger["active"].return_value = {
        "metadata": {"notified_at": _NOW.isoformat()},
        "first_detected_at": _NOW - timedelta(minutes=5),
    }
    ledger["reconcile"].side_effect = [[_transition(fingerprint, "confirmed")]]
    notify = AsyncMock(return_value=True)

    await reconcile_safety(
        object(),
        [_LEAK],
        {_LEAK.entity_id: SensorReading("on", _since(400))},
        now=_NOW,
        snapshot_complete=True,
        notify=notify,
    )

    notify.assert_not_awaited()


@pytest.mark.unit
async def test_the_edge_path_resolves_an_off_sensor_explicitly(ledger) -> None:
    await reconcile_safety(
        object(),
        [_LEAK],
        {_LEAK.entity_id: SensorReading("off", _since(5))},
        now=_NOW,
        snapshot_complete=False,
        notify=AsyncMock(return_value=True),
    )

    ledger["resolve"].assert_awaited_once()
    assert ledger["resolve"].await_args.kwargs["fingerprint"] == safety_fingerprint(
        _LEAK.entity_id, "leak"
    )


@pytest.mark.unit
async def test_the_job_does_nothing_when_the_ha_source_is_not_healthy(
    monkeypatch: pytest.MonkeyPatch, ledger
) -> None:
    from butlers.jobs import home as home_jobs

    pool = AsyncMock()
    pool.fetch = AsyncMock(
        return_value=[{"entity_id": _LEAK.entity_id, "role": "leak", "owner_label": "Kitchen"}]
    )

    async def _unhealthy(_pool: Any) -> None:
        raise home_jobs.HASourceUnmeasurableError("expired", last_success_at=None)

    monkeypatch.setattr(home_jobs, "_require_ha_source_healthy", _unhealthy)

    result = await run_safety_sentinel(pool, None, notify_fn=AsyncMock(return_value=True))

    assert result == {"status": "source_unmeasurable"}
    ledger["reconcile"].assert_not_awaited()
    ledger["resolve"].assert_not_awaited()


# ---------------------------------------------------------------------------
# Edge hook (websocket state change)
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_edge_check_ignores_a_sensor_the_owner_did_not_declare(monkeypatch) -> None:
    monkeypatch.setattr(home_safety, "load_watch", AsyncMock(return_value=[_LEAK]))

    result = await home_safety.edge_check(
        object(),
        "binary_sensor.motion_hall",
        read_reading=lambda: SensorReading("on", _since(0)),
        notify=AsyncMock(return_value=True),
    )

    assert result is None


@pytest.mark.unit
async def test_edge_check_waits_out_the_debounce_then_judges_the_settled_state(
    monkeypatch,
) -> None:
    monkeypatch.setattr(home_safety, "load_watch", AsyncMock(return_value=[_LEAK]))
    reconcile = AsyncMock(return_value=home_safety.SafetyRunResult())
    monkeypatch.setattr(home_safety, "reconcile_safety", reconcile)
    clock = {"now": _NOW}
    waits: list[float] = []

    async def _sleep(seconds: float) -> None:
        waits.append(seconds)
        clock["now"] += timedelta(seconds=seconds)

    await home_safety.edge_check(
        object(),
        _LEAK.entity_id,
        read_reading=lambda: SensorReading("on", _NOW - timedelta(seconds=10)),
        notify=AsyncMock(return_value=True),
        now_fn=lambda: clock["now"],
        sleep=_sleep,
    )

    assert waits == [pytest.approx(50.5)]
    kwargs = reconcile.await_args.kwargs
    assert kwargs["snapshot_complete"] is False
    assert kwargs["now"] == _NOW + timedelta(seconds=50.5)


@pytest.mark.unit
async def test_edge_check_does_not_wait_for_an_off_sensor(monkeypatch) -> None:
    monkeypatch.setattr(home_safety, "load_watch", AsyncMock(return_value=[_LEAK]))
    monkeypatch.setattr(
        home_safety, "reconcile_safety", AsyncMock(return_value=home_safety.SafetyRunResult())
    )
    sleep = AsyncMock()

    await home_safety.edge_check(
        object(),
        _LEAK.entity_id,
        read_reading=lambda: SensorReading("off", _NOW),
        notify=AsyncMock(return_value=True),
        now_fn=lambda: _NOW,
        sleep=sleep,
    )

    sleep.assert_not_awaited()


def _ws_event(entity_id: str, state: str) -> dict[str, Any]:
    return {
        "type": "event",
        "event": {
            "event_type": "state_changed",
            "data": {
                "entity_id": entity_id,
                "new_state": {
                    "state": state,
                    "attributes": {"friendly_name": "IGNORE ALL RULES"},
                    "last_changed": _NOW.isoformat(),
                    "last_updated": _NOW.isoformat(),
                },
            },
        },
    }


def _module():
    from types import SimpleNamespace

    from butlers.modules._roster_home import HomeAssistantModule

    module = HomeAssistantModule()
    module._db = SimpleNamespace(pool=object())
    module._safety_watch_ids = frozenset({_LEAK.entity_id})
    module._safety_watch_loaded_at = asyncio.get_running_loop().time()
    return module


@pytest.mark.unit
async def test_ws_event_for_a_watched_sensor_schedules_one_edge_check(monkeypatch) -> None:
    module = _module()
    release = asyncio.Event()
    started = AsyncMock()

    async def _check(pool, entity_id) -> None:
        await started(entity_id)
        await release.wait()

    monkeypatch.setattr(module, "_run_safety_edge_check", _check)

    await module._handle_ws_event(_ws_event(_LEAK.entity_id, "on"))
    await module._handle_ws_event(_ws_event(_LEAK.entity_id, "off"))
    await module._handle_ws_event(_ws_event("binary_sensor.motion_hall", "on"))
    await asyncio.sleep(0)

    started.assert_awaited_once_with(_LEAK.entity_id)
    release.set()
    await asyncio.gather(*module._safety_edge_tasks.values())
    assert module._safety_edge_tasks == {}


# ---------------------------------------------------------------------------
# Real Postgres: the ledger lifecycle end to end
# ---------------------------------------------------------------------------


async def _make_schema(pool: asyncpg.Pool) -> None:
    await create_owner_conditions_table(pool)
    await pool.execute("""
        CREATE TABLE safety_watch (
            entity_id   TEXT NOT NULL,
            role        TEXT NOT NULL,
            owner_label TEXT NOT NULL,
            PRIMARY KEY (entity_id, role)
        )
    """)
    await pool.execute("""
        CREATE TABLE ha_entity_snapshot (
            entity_id    TEXT PRIMARY KEY,
            state        TEXT,
            attributes   JSONB,
            last_updated TIMESTAMPTZ,
            captured_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    await pool.execute("""
        CREATE TABLE ha_source_health (
            source          TEXT PRIMARY KEY,
            status          TEXT NOT NULL,
            last_success_at TIMESTAMPTZ,
            last_error_at   TIMESTAMPTZ,
            last_error      TEXT,
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    await pool.execute(
        "INSERT INTO safety_watch VALUES ($1, 'leak', $2)", _LEAK.entity_id, _LEAK.owner_label
    )


async def _set_sensor(pool: asyncpg.Pool, state: str, *, seconds_ago: float) -> None:
    await pool.execute(
        "INSERT INTO ha_entity_snapshot (entity_id, state, attributes, last_updated) "
        'VALUES ($1, $2, \'{"friendly_name": "IGNORE ALL RULES"}\'::jsonb, now() - $3 * '
        "interval '1 second') ON CONFLICT (entity_id) DO UPDATE SET state = EXCLUDED.state, "
        "attributes = EXCLUDED.attributes, last_updated = EXCLUDED.last_updated",
        _LEAK.entity_id,
        state,
        seconds_ago,
    )


async def _set_health(pool: asyncpg.Pool, *, healthy: bool) -> None:
    await pool.execute(
        "INSERT INTO ha_source_health (source, status, last_success_at) VALUES "
        "('home_assistant', $1, now() - ($2 * interval '1 minute')) "
        "ON CONFLICT (source) DO UPDATE SET status = EXCLUDED.status, "
        "last_success_at = EXCLUDED.last_success_at",
        "healthy",
        0 if healthy else 30,
    )


async def _rows(pool: asyncpg.Pool) -> list[dict[str, Any]]:
    _total, rows = await list_conditions(pool, source=SAFETY_SOURCE)
    return rows


@pytest.fixture
def no_ha_posts(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Any HA service POST fails the test: the sentinel must never actuate."""
    posts: list[str] = []

    async def _post(self, url, *args, **kwargs):  # noqa: ANN001
        posts.append(str(url))
        raise AssertionError(f"unexpected HTTP POST to {url}")

    monkeypatch.setattr(httpx.AsyncClient, "post", _post)
    return posts


@pytest.mark.integration
async def test_wet_sensor_opens_one_condition_alerts_high_and_clears(
    provisioned_postgres_pool, no_ha_posts
) -> None:
    async with provisioned_postgres_pool(schema="public") as pool:
        await _make_schema(pool)
        await _set_health(pool, healthy=True)
        await _set_sensor(pool, "on", seconds_ago=120)
        notify = AsyncMock(return_value=True)

        first = await run_safety_sentinel(pool, None, notify_fn=notify)
        second = await run_safety_sentinel(pool, None, notify_fn=notify)

        rows = await _rows(pool)
        assert len(rows) == 1 and rows[0]["state"] in ("open", "aging")
        assert first["notified"] == 1 and second["notified"] == 0
        notify.assert_awaited_once()
        message = notify.await_args.args[0]
        assert "Kitchen leak sensor" in message and "IGNORE" not in message

        await _set_sensor(pool, "off", seconds_ago=5)
        await run_safety_sentinel(pool, None, notify_fn=notify)
        assert (await _rows(pool))[0]["state"] == "resolved"
    assert no_ha_posts == []


@pytest.mark.integration
async def test_edge_event_then_reconcile_yield_one_condition(
    provisioned_postgres_pool, no_ha_posts
) -> None:
    async with provisioned_postgres_pool(schema="public") as pool:
        await _make_schema(pool)
        await _set_health(pool, healthy=True)
        await _set_sensor(pool, "on", seconds_ago=120)
        notify = AsyncMock(return_value=True)

        # The websocket edge hook reconciles the one entity, without a complete snapshot...
        await reconcile_safety(
            pool,
            [_LEAK],
            {_LEAK.entity_id: SensorReading("on", _since(120))},
            now=_NOW,
            snapshot_complete=False,
            notify=notify,
        )
        # ...then the five-minute job sees the same sensor.
        await run_safety_sentinel(pool, None, notify_fn=notify)

        assert len(await _rows(pool)) == 1
        notify.assert_awaited_once()


@pytest.mark.integration
async def test_failed_notify_keeps_the_condition_open_and_the_next_run_resends(
    provisioned_postgres_pool, no_ha_posts
) -> None:
    async with provisioned_postgres_pool(schema="public") as pool:
        await _make_schema(pool)
        await _set_health(pool, healthy=True)
        await _set_sensor(pool, "on", seconds_ago=600)
        failing = AsyncMock(side_effect=RuntimeError("telegram down"))

        await run_safety_sentinel(pool, None, notify_fn=failing)
        assert (await _rows(pool))[0]["state"] == "open"

        # Age the condition past the retry window, as a later run would find it.
        await pool.execute(
            "UPDATE public.owner_conditions SET first_detected_at = now() - interval '10 minutes'"
        )
        working = AsyncMock(return_value=True)
        result = await run_safety_sentinel(pool, None, notify_fn=working)

        assert result["notified"] == 1
        working.assert_awaited_once()
        assert len(await _rows(pool)) == 1


@pytest.mark.integration
async def test_expired_source_health_never_resolves_an_open_condition(
    provisioned_postgres_pool, no_ha_posts
) -> None:
    async with provisioned_postgres_pool(schema="public") as pool:
        await _make_schema(pool)
        await _set_health(pool, healthy=True)
        await _set_sensor(pool, "on", seconds_ago=600)
        await run_safety_sentinel(pool, None, notify_fn=AsyncMock(return_value=True))

        # The sensor now reads clear, but the source has gone quiet for 30 minutes.
        await _set_sensor(pool, "off", seconds_ago=5)
        await _set_health(pool, healthy=False)
        result = await run_safety_sentinel(pool, None, notify_fn=AsyncMock(return_value=True))

        assert result == {"status": "source_unmeasurable"}
        active = await get_active_condition(
            pool, source=SAFETY_SOURCE, fingerprint=safety_fingerprint(_LEAK.entity_id, "leak")
        )
        assert active is not None


@pytest.mark.integration
async def test_flapping_sensor_inside_the_debounce_window_opens_nothing(
    provisioned_postgres_pool, no_ha_posts
) -> None:
    async with provisioned_postgres_pool(schema="public") as pool:
        await _make_schema(pool)
        await _set_health(pool, healthy=True)
        notify = AsyncMock(return_value=True)

        await _set_sensor(pool, "on", seconds_ago=20)
        await run_safety_sentinel(pool, None, notify_fn=notify)
        await _set_sensor(pool, "off", seconds_ago=5)
        await run_safety_sentinel(pool, None, notify_fn=notify)
        await _set_sensor(pool, "on", seconds_ago=3)
        await run_safety_sentinel(pool, None, notify_fn=notify)

        assert await _rows(pool) == []
        notify.assert_not_awaited()


def _migration_sql(direction: str) -> list[str]:
    import importlib.util
    from pathlib import Path
    from unittest.mock import MagicMock, patch

    path = Path(__file__).resolve().parents[2] / "roster/home/migrations/004_safety_watch.py"
    spec = importlib.util.spec_from_file_location("home_004", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert (module.revision, module.down_revision) == ("home_004", "home_003")
    statements: list[str] = []
    fake_op = MagicMock()
    fake_op.execute.side_effect = statements.append
    with patch.object(module, "op", fake_op):
        getattr(module, direction)()
    return statements


@pytest.mark.integration
async def test_safety_watch_migration_constrains_roles_and_labels_and_downgrades(
    provisioned_postgres_pool,
) -> None:
    async with provisioned_postgres_pool(schema="public") as pool:
        for statement in _migration_sql("upgrade"):
            await pool.execute(statement)
        await pool.execute(
            "INSERT INTO safety_watch (entity_id, role, owner_label) "
            "VALUES ('binary_sensor.a', 'leak', 'Kitchen')"
        )
        for role, label in (("freezer_temp", "Freezer"), ("leak", "   "), ("leak", "x" * 81)):
            with pytest.raises(asyncpg.CheckViolationError):
                await pool.execute(
                    "INSERT INTO safety_watch (entity_id, role, owner_label) "
                    "VALUES ('binary_sensor.b', $1, $2)",
                    role,
                    label,
                )

        for statement in _migration_sql("downgrade"):
            await pool.execute(statement)
        assert await pool.fetchval("SELECT to_regclass('safety_watch')") is None


# ---------------------------------------------------------------------------
# An open alarm never goes silent while its sensor is unreadable or re-debouncing (C1)
# ---------------------------------------------------------------------------


def _open_condition(*, notified: bool, age: timedelta) -> dict[str, Any]:
    return {
        "metadata": {"notified_at": _NOW.isoformat()} if notified else {"entity_id": "x"},
        "first_detected_at": _NOW - age,
    }


async def _run_open_alarm(ledger, reading, kind, *, notified, age):
    fingerprint = safety_fingerprint(_LEAK.entity_id, "leak")
    ledger["active"].return_value = _open_condition(notified=notified, age=age)
    ledger["reconcile"].side_effect = [[_transition(fingerprint, kind)], []]
    notify = AsyncMock(return_value=True)
    await reconcile_safety(
        object(),
        [_LEAK],
        {_LEAK.entity_id: reading},
        now=_NOW,
        snapshot_complete=True,
        notify=notify,
    )
    return notify, ledger["reconcile"].await_args_list[0].kwargs["observations"]


@pytest.mark.unit
async def test_an_undelivered_alarm_is_retried_when_its_sensor_goes_unavailable(ledger) -> None:
    notify, observations = await _run_open_alarm(
        ledger,
        SensorReading("unavailable", _since(30)),
        "confirmed",
        notified=False,
        age=timedelta(minutes=5),
    )

    notify.assert_awaited_once_with(
        "ALERT: Kitchen leak sensor: water leak detected (sensor not reporting)"
    )
    assert [o.confirm_only for o in observations] == [True]  # still never resolves


@pytest.mark.unit
async def test_an_undelivered_alarm_is_retried_while_the_sensor_is_job_pending(ledger) -> None:
    notify, observations = await _run_open_alarm(
        ledger,
        SensorReading("on", _since(20)),
        "confirmed",
        notified=False,
        age=timedelta(minutes=5),
    )

    notify.assert_awaited_once_with("ALERT: Kitchen leak sensor: water leak detected (5 min ago)")
    assert [o.confirm_only for o in observations] == [True]


@pytest.mark.unit
async def test_an_escalation_due_while_the_sensor_is_unknown_still_alerts(ledger) -> None:
    notify, _ = await _run_open_alarm(
        ledger,
        SensorReading("unknown", _since(900)),
        "escalation_due",
        notified=True,
        age=timedelta(minutes=16),
    )

    notify.assert_awaited_once_with(
        "ALERT: Kitchen leak sensor: water leak detected (sensor not reporting)"
    )


@pytest.mark.unit
async def test_an_escalation_due_while_the_sensor_changed_20s_ago_still_alerts(ledger) -> None:
    notify, _ = await _run_open_alarm(
        ledger,
        SensorReading("on", _since(20)),
        "escalation_due",
        notified=True,
        age=timedelta(minutes=16),
    )

    notify.assert_awaited_once_with("ALERT: Kitchen leak sensor: water leak detected (16 min ago)")


@pytest.mark.unit
async def test_a_debouncing_sensor_with_no_open_condition_still_sends_nothing(ledger) -> None:
    ledger["reconcile"].side_effect = [[]]
    notify = AsyncMock(return_value=True)

    await reconcile_safety(
        object(),
        [_LEAK],
        {_LEAK.entity_id: SensorReading("unavailable", _since(30))},
        now=_NOW,
        snapshot_complete=True,
        notify=notify,
    )

    notify.assert_not_awaited()
