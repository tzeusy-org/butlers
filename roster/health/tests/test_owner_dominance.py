"""Owner-logged measurements beat device readings on the same owner-local day (bu-q7vx1q.35)."""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime

import pytest

from butlers.tools.health import measurements as meas
from butlers.tools.health import owner_dominance as od

SGT = "Asia/Singapore"  # UTC+8: UTC and owner-local dates differ for a third of every day


def _at(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC)


def _row(valid_at: str, source: str | None, value: float, predicate: str = "measurement_weight"):
    metadata = {"value": value}
    if source is not None:
        metadata["source"] = source
    return {
        "id": uuid.uuid4(),
        "predicate": predicate,
        "content": f"weight: {value} kg",
        "valid_at": _at(valid_at),
        "created_at": _at("2026-10-18T00:00:00Z"),
        "metadata": metadata,
    }


class FakePool:
    """Answers the history/latest reads and the dominance queries from in-memory rows."""

    def __init__(self, rows: list[dict], zone: str = SGT) -> None:
        self.rows = rows
        self.zone = zone
        self.calls: list[str] = []

    def _owner(self, row: dict) -> bool:
        return row["metadata"].get("source", od.OWNER_SOURCE) == od.OWNER_SOURCE

    def _window(self, predicate: str, start: datetime, end: datetime, owner_only: bool):
        return sorted(
            (
                r
                for r in self.rows
                if r["predicate"] == predicate
                and start <= r["valid_at"] < end
                and (self._owner(r) or not owner_only)
            ),
            key=lambda r: r["valid_at"],
            reverse=True,
        )

    async def fetchval(self, sql, *args):
        self.calls.append("fetchval")
        return {"timezone": self.zone}

    async def fetch(self, sql, *args):
        self.calls.append("fetch")
        far = (datetime.min.replace(tzinfo=UTC), datetime.max.replace(tzinfo=UTC))
        if "COALESCE(metadata->>'source'" in sql:
            return self._window(args[0], args[1], args[2], owner_only=True)
        return self._window(args[0], *far, owner_only=False)

    async def fetchrow(self, sql, *args):
        self.calls.append("fetchrow")
        if "COALESCE(metadata->>'source'" in sql:
            rows = self._window(args[0], args[1], args[2], owner_only=True)
        else:
            rows = self._window(
                args[0],
                datetime.min.replace(tzinfo=UTC),
                datetime.max.replace(tzinfo=UTC),
                owner_only=False,
            )
        return rows[0] if rows else None


pytestmark = pytest.mark.unit


async def test_owner_value_beats_a_later_device_value_on_the_same_owner_day() -> None:
    # 01:00 SGT (owner) and 10:00 SGT (device) on 2026-10-18; the UTC dates differ (17th vs 18th).
    owner = _row("2026-10-17T17:00:00Z", "owner_log", 70.0)
    device = _row("2026-10-18T02:00:00Z", "google_health", 71.2)
    latest = await meas.measurement_latest(FakePool([owner, device]), "weight")

    assert latest["value"] == 70.0 and latest["id"] == owner["id"]


async def test_device_value_stands_when_the_owner_logged_a_different_owner_day() -> None:
    owner = _row("2026-10-18T02:00:00Z", "owner_log", 70.0)  # 10:00 SGT on the 18th
    device = _row("2026-10-18T17:00:00Z", "google_health", 71.2)  # 01:00 SGT on the 19th
    latest = await meas.measurement_latest(FakePool([owner, device]), "weight")

    assert latest["value"] == 71.2


async def test_legacy_rows_without_a_source_count_as_owner_logged() -> None:
    legacy = _row("2026-10-18T01:00:00Z", None, 70.0)
    device = _row("2026-10-18T05:00:00Z", "google_health", 71.2)
    latest = await meas.measurement_latest(FakePool([legacy, device]), "weight")

    assert latest["value"] == 70.0


async def test_history_drops_device_rows_only_on_days_with_an_owner_log() -> None:
    owner_day = _row("2026-10-17T17:00:00Z", "owner_log", 70.0)  # 18th SGT
    dominated = _row("2026-10-18T02:00:00Z", "google_health", 71.2)  # 18th SGT
    other_day = _row("2026-10-18T17:00:00Z", "google_health", 72.0)  # 19th SGT
    pool = FakePool([owner_day, dominated, other_day])

    history = await meas.measurement_history(pool, "weight")

    assert {h["value"] for h in history} == {70.0, 72.0}
    assert dominated["id"] in {r["id"] for r in pool.rows}  # never deleted


async def test_no_device_rows_means_no_timezone_lookup_and_no_extra_query() -> None:
    pool = FakePool([_row("2026-10-18T01:00:00Z", "owner_log", 70.0)])

    history = await meas.measurement_history(pool, "weight")
    latest = await meas.measurement_latest(pool, "weight")

    assert len(history) == 1 and latest["value"] == 70.0
    assert "fetchval" not in pool.calls
    assert pool.calls == ["fetch", "fetchrow"]


async def test_a_device_only_day_is_untouched() -> None:
    device = _row("2026-10-18T02:00:00Z", "google_health", 71.2)
    pool = FakePool([device])

    assert (await meas.measurement_latest(pool, "weight"))["value"] == 71.2
    assert len(await meas.measurement_history(pool, "weight")) == 1


async def test_body_fat_is_readable_but_not_loggable() -> None:
    row = _row("2026-10-18T02:00:00Z", "google_health", 18.2, "measurement_body_fat")
    pool = FakePool([row])

    assert (await meas.measurement_latest(pool, "body_fat"))["type"] == "body_fat"
    with pytest.raises(ValueError, match="Unrecognized measurement type"):
        await meas.measurement_log(pool, "body_fat", 18.2)


def test_day_bounds_are_dst_safe() -> None:
    from datetime import date
    from zoneinfo import ZoneInfo

    zone = ZoneInfo("America/Los_Angeles")
    start, end = od.day_bounds(date(2026, 11, 1), zone)  # fall back: a 25-hour day
    assert (end.astimezone(UTC) - start.astimezone(UTC)).total_seconds() == 25 * 3600


async def test_latest_endpoint_prefers_the_owner_row_on_the_same_day() -> None:
    from butlers.tools.health import owner_dominance

    owner = _row("2026-10-17T17:00:00Z", "owner_log", 70.0)
    device = _row("2026-10-18T02:00:00Z", "google_health", 71.2)
    pool = FakePool([owner, device])

    chosen = await owner_dominance.dominant_latest(
        pool, "measurement_weight", device, columns="predicate, valid_at, metadata"
    )

    assert chosen["metadata"]["value"] == 70.0


docker_available = shutil.which("docker") is not None


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
async def test_real_postgres_owner_beats_device_and_device_row_survives(
    provisioned_postgres_pool,
) -> None:
    """Real facts + public.state: the owner zone comes from public.state (not an empty
    health.state), and device rows are kept."""
    async with provisioned_postgres_pool(schema="health") as pool:
        await pool.execute("CREATE SCHEMA IF NOT EXISTS health")
        await pool.execute(
            "CREATE TABLE IF NOT EXISTS public.state (key TEXT PRIMARY KEY, value JSONB NOT NULL,"
            " updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), version INTEGER NOT NULL DEFAULT 1)"
        )
        await pool.execute(
            "INSERT INTO public.state (key, value) VALUES ('settings.general', $1)",
            {"timezone": SGT},
        )
        # An empty health.state shadows nothing: the zone must still come from public.state.
        await pool.execute(
            "CREATE TABLE IF NOT EXISTS health.state (key TEXT PRIMARY KEY, value JSONB NOT NULL,"
            " updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), version INTEGER NOT NULL DEFAULT 1)"
        )
        await pool.execute(
            "CREATE TABLE IF NOT EXISTS health.facts (id uuid PRIMARY KEY DEFAULT"
            " gen_random_uuid(), predicate text, scope text, validity text, content text,"
            " valid_at timestamptz, created_at timestamptz DEFAULT now(), metadata jsonb)"
        )
        for valid_at, source, value in (
            ("2026-10-17T17:00:00Z", "owner_log", 70.0),
            ("2026-10-18T02:00:00Z", "google_health", 71.2),
            ("2026-10-18T17:00:00Z", "google_health", 72.0),
        ):
            await pool.execute(
                "INSERT INTO health.facts (predicate, scope, validity, content, valid_at, metadata)"
                " VALUES ('measurement_weight', 'health', 'active', 'w', $1, $2)",
                _at(valid_at),
                {"value": value, "source": source},
            )

        history = await meas.measurement_history(pool, "weight")
        assert sorted(h["value"] for h in history) == [70.0, 72.0]
        assert (await meas.measurement_latest(pool, "weight"))["value"] == 72.0
        assert await pool.fetchval("SELECT count(*) FROM health.facts") == 3
