"""Tests for the HA non-person sensor-activity projection adapter (bu-49fqa).

Covers:
- full_payload decode (correct dict shape + legacy double-JSON-encoded shape).
- Rule-table classification: motion -> room_activity_episode clustering,
  door/garage/opening -> entry_event point events, everything else skipped.
- Watermark advances across ALL matched connector_type rows (not just
  classified ones); missing evidence table degrades gracefully.
- Gap-tolerant motion clustering, including cross-batch carryover.
- Evidence -> activity promotion only when a corroborator overlaps the span;
  lane discipline (never work/occupation).
- Retention-lag monitoring warning.
- Bounded retroactive evidence -> activity promotion re-check (bu-mul8i).
- No-LLM AST guardrail.
"""

from __future__ import annotations

import ast
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.chronicler.adapters.home_assistant_sensor_activity import (
    EPISODE_TYPE_ROOM_ACTIVITY,
    EVENT_TYPE_ENTRY,
    PROMOTION_LOOKBACK_HOURS,
    PROMOTION_RECHECK_LIMIT,
    RETENTION_LAG_WARNING_DAYS,
    SOURCE_NAME,
    HomeAssistantSensorActivityAdapter,
)
from butlers.chronicler.models import Confidence, Episode, Layer, PointEvent

_NOW = datetime(2026, 7, 6, 8, 0, 0, tzinfo=UTC)
_UUID_1 = uuid.UUID("11111111-1111-1111-1111-111111111111")
_UUID_2 = uuid.UUID("22222222-2222-2222-2222-222222222222")
_UUID_3 = uuid.UUID("33333333-3333-3333-3333-333333333333")


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _AsyncCtx:
    def __init__(self, obj: object) -> None:
        self._obj = obj

    async def __aenter__(self) -> object:
        return self._obj

    async def __aexit__(self, *_: object) -> None:
        pass


class _FakeReadConn:
    """Fake read-side connection for the ``pool`` argument to ``project()``."""

    def __init__(
        self,
        *,
        table_exists: bool = True,
        rows: list[dict] | None = None,
        partition_names: list[dict] | None = None,
    ) -> None:
        self._table_exists = table_exists
        self._rows = rows or []
        self._partition_names = partition_names if partition_names is not None else []
        self.fetch_calls: list[tuple] = []

    async def fetchval(self, query: str, *args: object) -> object:
        if "information_schema.tables" in query and "filtered_events" in query:
            return self._table_exists
        # resolve_owner_entity_id / other fetchval paths not exercised here.
        return None

    async def fetchrow(self, query: str, *args: object) -> None:
        return None

    async def fetch(self, query: str, *args: object) -> list:
        self.fetch_calls.append((query, args))
        if "BASE TABLE" in query:
            # Retention-lag partition listing query.
            return [_Row(r) for r in self._partition_names]
        return [_Row(r) for r in self._rows]


class _TieBoundaryReadConn(_FakeReadConn):
    """Read fake that applies the adapter's received_at/limit SQL semantics."""

    async def fetch(self, query: str, *args: object) -> list:
        self.fetch_calls.append((query, args))
        if "BASE TABLE" in query:
            return [_Row(r) for r in self._partition_names]
        if "received_at > $2" in query:
            since = args[1]
            assert isinstance(since, datetime)
            candidates = [row for row in self._rows if row["received_at"] > since]
        else:
            candidates = list(self._rows)
        limit = args[-1]
        assert isinstance(limit, int)
        return [_Row(row) for row in candidates[:limit]]


class _Row(dict):
    def __getitem__(self, key: str) -> object:
        return dict.__getitem__(self, key)


def _read_pool(conn: _FakeReadConn) -> AsyncMock:
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))
    return pool


class _FakeChroniclerConn:
    """Fake chronicler-pool connection: fetch() answers overlap queries in order."""

    def __init__(
        self,
        fetch_results: list[list[dict]] | None = None,
        *,
        execute_result: str = "UPDATE 1",
    ) -> None:
        self._fetch_results = list(fetch_results or [])
        self.fetch_calls: list[tuple] = []
        self.execute_calls: list[tuple] = []
        self.execute_result = execute_result

    async def fetch(self, query: str, *args: object) -> list[dict]:
        self.fetch_calls.append((query, args))
        if not self._fetch_results:
            return []
        return self._fetch_results.pop(0)

    async def execute(self, query: str, *args: object) -> str:
        self.execute_calls.append((query, args))
        return self.execute_result


def _chronicler_pool(conn: _FakeChroniclerConn) -> AsyncMock:
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))
    return pool


def _ha_row(
    *,
    row_id: uuid.UUID,
    received_at: datetime,
    entity_id: str,
    domain: str,
    device_class: str | None,
    new_state: str | None,
    old_state: str | None = None,
    friendly_name: str | None = None,
    double_encode: bool = False,
) -> dict:
    raw: dict = {
        "entity_id": entity_id,
        "event_type": "state_changed",
        "domain": domain,
        "device_class": device_class,
        "friendly_name": friendly_name,
    }
    if old_state is not None:
        raw["old_state"] = {"state": old_state}
    if new_state is not None:
        raw["new_state"] = {"state": new_state}

    full_payload: object = {
        "source": {"channel": "home_assistant", "provider": "home_assistant"},
        "event": {"external_event_id": f"ha:{entity_id}:{int(received_at.timestamp() * 1000)}"},
        "sender": {"identity": entity_id},
        "payload": {"raw": raw},
        "control": {},
    }
    if double_encode:
        full_payload = json.dumps(full_payload)

    return {"id": row_id, "received_at": received_at, "full_payload": full_payload}


# ---------------------------------------------------------------------------
# No-LLM guardrail
# ---------------------------------------------------------------------------


def test_no_llm_imports_in_sensor_activity_adapter() -> None:
    import butlers.chronicler.adapters.home_assistant_sensor_activity as mod

    source_path = mod.__file__
    assert source_path is not None
    with open(source_path) as fh:
        tree = ast.parse(fh.read(), filename=source_path)

    forbidden_prefixes = ("anthropic", "openai", "langchain", "litellm", "llm")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                for prefix in forbidden_prefixes:
                    assert not alias.name.startswith(prefix)
        elif isinstance(node, ast.ImportFrom) and node.module:
            for prefix in forbidden_prefixes:
                assert not node.module.startswith(prefix)


def test_adapter_exported_from_package() -> None:
    from butlers.chronicler.adapters import HomeAssistantSensorActivityAdapter as pkg_export

    assert pkg_export is HomeAssistantSensorActivityAdapter


# ---------------------------------------------------------------------------
# full_payload decode
# ---------------------------------------------------------------------------


def test_extract_raw_handles_dict_payload() -> None:
    payload = {"payload": {"raw": {"domain": "binary_sensor", "entity_id": "x"}}}
    raw = HomeAssistantSensorActivityAdapter._extract_raw(payload)
    assert raw == {"domain": "binary_sensor", "entity_id": "x"}


def test_extract_raw_handles_double_encoded_string_payload() -> None:
    """Legacy rows are double-JSON-encoded (jsonb string, not object) — bu-dycxq."""
    payload = json.dumps({"payload": {"raw": {"domain": "binary_sensor", "entity_id": "y"}}})
    raw = HomeAssistantSensorActivityAdapter._extract_raw(payload)
    assert raw == {"domain": "binary_sensor", "entity_id": "y"}


@pytest.mark.parametrize(
    "malformed",
    [None, "not json", "{}", json.dumps({"payload": "not-a-dict"}), 42, []],
)
def test_extract_raw_returns_none_for_malformed_payload(malformed: object) -> None:
    assert HomeAssistantSensorActivityAdapter._extract_raw(malformed) is None


def test_state_value_extracts_from_dict_and_string() -> None:
    assert HomeAssistantSensorActivityAdapter._state_value({"state": "on"}) == "on"
    assert HomeAssistantSensorActivityAdapter._state_value("open") == "open"
    assert HomeAssistantSensorActivityAdapter._state_value(None) is None
    assert HomeAssistantSensorActivityAdapter._state_value({"other": 1}) is None


# ---------------------------------------------------------------------------
# Missing evidence table / empty batch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_filtered_events_table_skips_gracefully() -> None:
    adapter = HomeAssistantSensorActivityAdapter()
    read_conn = _FakeReadConn(table_exists=False)
    result = await adapter.project(_read_pool(read_conn), chronicler_pool=AsyncMock(), since=None)
    assert result.skipped is True
    assert "filtered_events" in (result.skipped_reason or "")


@pytest.mark.asyncio
async def test_empty_batch_preserves_watermark() -> None:
    adapter = HomeAssistantSensorActivityAdapter()
    read_conn = _FakeReadConn(table_exists=True, rows=[])
    result = await adapter.project(
        _read_pool(read_conn),
        chronicler_pool=_chronicler_pool(_FakeChroniclerConn()),
        since=_NOW,
    )
    assert result.watermark == _NOW
    assert result.rows_projected == 0
    assert result.episodes_promoted == 0


# ---------------------------------------------------------------------------
# Classification + watermark advance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_watermark_advances_across_unclassified_rows() -> None:
    """Domains outside binary_sensor (e.g. 'sensor') still advance the watermark."""
    later = _NOW + timedelta(minutes=5)
    rows = [
        _ha_row(
            row_id=_UUID_1,
            received_at=_NOW,
            entity_id="sensor.bedroom_humidity",
            domain="sensor",
            device_class="humidity",
            new_state="42",
        ),
        _ha_row(
            row_id=_UUID_2,
            received_at=later,
            entity_id="person.alice",
            domain="person",
            device_class=None,
            new_state="home",
        ),
    ]
    adapter = HomeAssistantSensorActivityAdapter()
    read_conn = _FakeReadConn(table_exists=True, rows=rows)
    with patch(
        "butlers.chronicler.adapters.home_assistant_sensor_activity.resolve_owner_entity_id",
        new=AsyncMock(return_value=None),
    ):
        result = await adapter.project(
            _read_pool(read_conn),
            chronicler_pool=_chronicler_pool(_FakeChroniclerConn()),
            since=None,
        )
    assert result.watermark == later
    assert result.rows_projected == 0  # neither row is binary_sensor


@pytest.mark.asyncio
async def test_single_column_received_at_watermark_skips_equal_tie_after_split_batch() -> None:
    """Record the accepted UUID-source checkpoint trade-off at a batch boundary.

    ``filtered_events.id`` has no compatible BIGINT ``watermark_id``
    tiebreaker, so the adapter deliberately resumes with ``received_at >
    watermark``. When a batch limit splits equal ``received_at`` values, the
    remaining tie is not replayed. This characterization keeps that residual
    risk visible; a composite watermark needs a separately scoped contract.
    """
    tied_at = _NOW
    rows = [
        _ha_row(
            row_id=_UUID_1,
            received_at=tied_at,
            entity_id="binary_sensor.front_door",
            domain="binary_sensor",
            device_class="door",
            new_state="on",
        ),
        _ha_row(
            row_id=_UUID_2,
            received_at=tied_at,
            entity_id="binary_sensor.back_door",
            domain="binary_sensor",
            device_class="door",
            new_state="on",
        ),
        _ha_row(
            row_id=_UUID_3,
            received_at=tied_at,
            entity_id="binary_sensor.garage_door",
            domain="binary_sensor",
            device_class="garage_door",
            new_state="open",
        ),
    ]
    adapter = HomeAssistantSensorActivityAdapter(batch_limit=2)
    read_conn = _TieBoundaryReadConn(table_exists=True, rows=rows)
    chron_conn = _FakeChroniclerConn()

    with (
        patch(
            "butlers.chronicler.adapters.home_assistant_sensor_activity.resolve_owner_entity_id",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "butlers.chronicler.adapters.home_assistant_sensor_activity.upsert_point_event"
        ) as mock_point_event,
    ):
        first = await adapter.project(
            _read_pool(read_conn), chronicler_pool=_chronicler_pool(chron_conn), since=None
        )
        second = await adapter.project(
            _read_pool(read_conn),
            chronicler_pool=_chronicler_pool(chron_conn),
            since=first.watermark,
        )

    assert first.rows_projected == 2
    assert first.watermark == tied_at
    assert second.rows_projected == 0
    assert second.watermark == tied_at
    assert mock_point_event.await_count == 2


@pytest.mark.asyncio
async def test_entry_event_projected_for_door_transition() -> None:
    row = _ha_row(
        row_id=_UUID_1,
        received_at=_NOW,
        entity_id="binary_sensor.front_door",
        domain="binary_sensor",
        device_class="door",
        old_state="off",
        new_state="on",
        friendly_name="Front Door",
    )
    adapter = HomeAssistantSensorActivityAdapter()
    read_conn = _FakeReadConn(table_exists=True, rows=[row])
    chron_conn = _FakeChroniclerConn()

    upserted: list[PointEvent] = []

    async def _fake_upsert_point_event(conn: object, event: PointEvent) -> PointEvent:
        upserted.append(event)
        event.id = uuid.uuid4()
        return event

    with (
        patch(
            "butlers.chronicler.adapters.home_assistant_sensor_activity.resolve_owner_entity_id",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "butlers.chronicler.adapters.home_assistant_sensor_activity.upsert_point_event",
            side_effect=_fake_upsert_point_event,
        ),
    ):
        result = await adapter.project(
            _read_pool(read_conn), chronicler_pool=_chronicler_pool(chron_conn), since=None
        )

    assert result.point_events == 1
    assert result.rows_projected == 1
    assert len(upserted) == 1
    event = upserted[0]
    assert event.event_type == EVENT_TYPE_ENTRY
    assert event.layer == Layer.EVIDENCE
    assert event.source_ref == f"connectors.filtered_events:sensor_activity:entry:{_UUID_1}"
    assert event.title == "Front Door: on"
    assert event.payload["old_state"] == "off"
    assert event.payload["new_state"] == "on"


@pytest.mark.asyncio
async def test_unclassified_device_class_is_not_projected() -> None:
    """binary_sensor rows with an out-of-v1-scope device_class are skipped."""
    row = _ha_row(
        row_id=_UUID_1,
        received_at=_NOW,
        entity_id="binary_sensor.smoke",
        domain="binary_sensor",
        device_class="smoke",
        new_state="on",
    )
    adapter = HomeAssistantSensorActivityAdapter()
    read_conn = _FakeReadConn(table_exists=True, rows=[row])
    with patch(
        "butlers.chronicler.adapters.home_assistant_sensor_activity.resolve_owner_entity_id",
        new=AsyncMock(return_value=None),
    ):
        result = await adapter.project(
            _read_pool(read_conn),
            chronicler_pool=_chronicler_pool(_FakeChroniclerConn()),
            since=None,
        )
    assert result.rows_projected == 0
    assert result.point_events == 0
    assert result.episodes_closed == 0


# ---------------------------------------------------------------------------
# Motion clustering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_motion_pings_within_gap_cluster_into_one_episode() -> None:
    entity = "binary_sensor.hallway_motion"
    t1 = _NOW
    t2 = _NOW + timedelta(minutes=5)  # well within the 15-minute gap
    rows = [
        _ha_row(
            row_id=_UUID_1,
            received_at=t1,
            entity_id=entity,
            domain="binary_sensor",
            device_class="motion",
            new_state="on",
        ),
        _ha_row(
            row_id=_UUID_2,
            received_at=t2,
            entity_id=entity,
            domain="binary_sensor",
            device_class="motion",
            new_state="on",
        ),
    ]
    adapter = HomeAssistantSensorActivityAdapter()
    read_conn = _FakeReadConn(table_exists=True, rows=rows)
    # Corroborator lookups (occupation_block, spotify) both return no rows.
    chron_conn = _FakeChroniclerConn(fetch_results=[[], []])

    upserted: list[Episode] = []

    async def _fake_upsert_episode(conn: object, episode: Episode) -> Episode:
        upserted.append(episode)
        episode.id = uuid.uuid4()
        return episode

    with (
        patch(
            "butlers.chronicler.adapters.home_assistant_sensor_activity.resolve_owner_entity_id",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "butlers.chronicler.adapters.home_assistant_sensor_activity.upsert_episode",
            side_effect=_fake_upsert_episode,
        ),
        patch(
            "butlers.chronicler.adapters.home_assistant_sensor_activity.upsert_owner_episode_entity",
            new=AsyncMock(),
        ),
        patch(
            "butlers.chronicler.adapters.home_assistant_sensor_activity.get_carryover",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "butlers.chronicler.adapters.home_assistant_sensor_activity.save_carryover",
            new=AsyncMock(),
        ),
    ):
        result = await adapter.project(
            _read_pool(read_conn), chronicler_pool=_chronicler_pool(chron_conn), since=None
        )

    assert result.episodes_closed == 1
    assert len(upserted) == 1
    ep = upserted[0]
    assert ep.episode_type == EPISODE_TYPE_ROOM_ACTIVITY
    assert ep.start_at == t1
    assert ep.end_at == t2
    assert ep.layer == Layer.EVIDENCE  # no corroborator
    assert ep.confidence == Confidence.LOW


@pytest.mark.asyncio
async def test_motion_pings_beyond_gap_split_into_two_episodes() -> None:
    entity = "binary_sensor.hallway_motion"
    t1 = _NOW
    t2 = _NOW + timedelta(minutes=45)  # exceeds the 15-minute gap
    rows = [
        _ha_row(
            row_id=_UUID_1,
            received_at=t1,
            entity_id=entity,
            domain="binary_sensor",
            device_class="motion",
            new_state="on",
        ),
        _ha_row(
            row_id=_UUID_2,
            received_at=t2,
            entity_id=entity,
            domain="binary_sensor",
            device_class="motion",
            new_state="on",
        ),
    ]
    adapter = HomeAssistantSensorActivityAdapter()
    read_conn = _FakeReadConn(table_exists=True, rows=rows)
    chron_conn = _FakeChroniclerConn(fetch_results=[[], [], [], []])

    upserted: list[Episode] = []

    async def _fake_upsert_episode(conn: object, episode: Episode) -> Episode:
        upserted.append(episode)
        episode.id = uuid.uuid4()
        return episode

    with (
        patch(
            "butlers.chronicler.adapters.home_assistant_sensor_activity.resolve_owner_entity_id",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "butlers.chronicler.adapters.home_assistant_sensor_activity.upsert_episode",
            side_effect=_fake_upsert_episode,
        ),
        patch(
            "butlers.chronicler.adapters.home_assistant_sensor_activity.upsert_owner_episode_entity",
            new=AsyncMock(),
        ),
        patch(
            "butlers.chronicler.adapters.home_assistant_sensor_activity.get_carryover",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "butlers.chronicler.adapters.home_assistant_sensor_activity.save_carryover",
            new=AsyncMock(),
        ),
    ):
        result = await adapter.project(
            _read_pool(read_conn), chronicler_pool=_chronicler_pool(chron_conn), since=None
        )

    assert result.episodes_closed == 2
    assert len(upserted) == 2
    assert upserted[0].start_at == t1 and upserted[0].end_at == t1
    assert upserted[1].start_at == t2 and upserted[1].end_at == t2


@pytest.mark.asyncio
async def test_carryover_extends_episode_across_batches() -> None:
    entity = "binary_sensor.hallway_motion"
    prior_ref = "connectors.filtered_events:sensor_activity:room:binary_sensor.hallway_motion:1000"
    prior_start = _NOW - timedelta(minutes=10)
    prior_end = _NOW - timedelta(minutes=5)
    carryover = {
        entity: {
            "source_ref": prior_ref,
            "start_at": prior_start.isoformat(),
            "end_at": prior_end.isoformat(),
        }
    }
    new_ping = _NOW  # within 15-minute gap of prior_end

    row = _ha_row(
        row_id=_UUID_1,
        received_at=new_ping,
        entity_id=entity,
        domain="binary_sensor",
        device_class="motion",
        new_state="on",
    )
    adapter = HomeAssistantSensorActivityAdapter()
    read_conn = _FakeReadConn(table_exists=True, rows=[row])
    chron_conn = _FakeChroniclerConn(fetch_results=[[], []])

    upserted: list[Episode] = []

    async def _fake_upsert_episode(conn: object, episode: Episode) -> Episode:
        upserted.append(episode)
        episode.id = uuid.uuid4()
        return episode

    with (
        patch(
            "butlers.chronicler.adapters.home_assistant_sensor_activity.resolve_owner_entity_id",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "butlers.chronicler.adapters.home_assistant_sensor_activity.upsert_episode",
            side_effect=_fake_upsert_episode,
        ),
        patch(
            "butlers.chronicler.adapters.home_assistant_sensor_activity.upsert_owner_episode_entity",
            new=AsyncMock(),
        ),
        patch(
            "butlers.chronicler.adapters.home_assistant_sensor_activity.get_carryover",
            new=AsyncMock(return_value=carryover),
        ),
        patch(
            "butlers.chronicler.adapters.home_assistant_sensor_activity.save_carryover",
            new=AsyncMock(),
        ),
    ):
        result = await adapter.project(
            _read_pool(read_conn), chronicler_pool=_chronicler_pool(chron_conn), since=None
        )

    assert result.episodes_closed == 1
    ep = upserted[0]
    assert ep.source_ref == prior_ref  # same row extended, not a new one
    assert ep.start_at == prior_start  # start carried from the prior batch
    assert ep.end_at == new_ping


# ---------------------------------------------------------------------------
# _resolve_carryover — malformed/stale carryover discard paths (same class of
# logic as the sibling place-cluster adapter's carryover reject paths).
# ---------------------------------------------------------------------------


_GAP = timedelta(minutes=15)
_VALID_CARRY: dict[str, Any] = {
    "source_ref": "connectors.filtered_events:sensor_activity:room:binary_sensor.x:1000",
    "start_at": (_NOW - timedelta(minutes=10)).isoformat(),
    "end_at": (_NOW - timedelta(minutes=5)).isoformat(),
}


def test_resolve_carryover_accepts_well_formed_carry_within_gap() -> None:
    resolved = HomeAssistantSensorActivityAdapter._resolve_carryover(_VALID_CARRY, _NOW, _GAP)
    assert resolved == (_VALID_CARRY["source_ref"], _NOW - timedelta(minutes=10))


def test_resolve_carryover_rejects_non_dict_carry() -> None:
    assert HomeAssistantSensorActivityAdapter._resolve_carryover("garbage", _NOW, _GAP) is None
    assert HomeAssistantSensorActivityAdapter._resolve_carryover(None, _NOW, _GAP) is None
    assert HomeAssistantSensorActivityAdapter._resolve_carryover([], _NOW, _GAP) is None


def test_resolve_carryover_rejects_missing_keys() -> None:
    carry = {"source_ref": "ref", "start_at": _NOW.isoformat()}  # missing end_at
    assert HomeAssistantSensorActivityAdapter._resolve_carryover(carry, _NOW, _GAP) is None


def test_resolve_carryover_rejects_malformed_iso_timestamp() -> None:
    carry = {**_VALID_CARRY, "start_at": "not-a-timestamp"}
    assert HomeAssistantSensorActivityAdapter._resolve_carryover(carry, _NOW, _GAP) is None


def test_resolve_carryover_rejects_blank_source_ref() -> None:
    carry = {**_VALID_CARRY, "source_ref": "   "}
    assert HomeAssistantSensorActivityAdapter._resolve_carryover(carry, _NOW, _GAP) is None


def test_resolve_carryover_rejects_non_string_source_ref() -> None:
    carry = {**_VALID_CARRY, "source_ref": 12345}
    assert HomeAssistantSensorActivityAdapter._resolve_carryover(carry, _NOW, _GAP) is None


def test_resolve_carryover_rejects_naive_start_at() -> None:
    carry = {
        **_VALID_CARRY,
        "start_at": (_NOW - timedelta(minutes=10)).replace(tzinfo=None).isoformat(),
    }
    assert HomeAssistantSensorActivityAdapter._resolve_carryover(carry, _NOW, _GAP) is None


def test_resolve_carryover_rejects_naive_end_at() -> None:
    carry = {
        **_VALID_CARRY,
        "end_at": (_NOW - timedelta(minutes=5)).replace(tzinfo=None).isoformat(),
    }
    assert HomeAssistantSensorActivityAdapter._resolve_carryover(carry, _NOW, _GAP) is None


def test_resolve_carryover_rejects_when_gap_since_prior_end_too_large() -> None:
    stale_carry = {
        "source_ref": _VALID_CARRY["source_ref"],
        "start_at": (_NOW - timedelta(hours=5)).isoformat(),
        "end_at": (_NOW - timedelta(hours=4, minutes=30)).isoformat(),
    }
    assert HomeAssistantSensorActivityAdapter._resolve_carryover(stale_carry, _NOW, _GAP) is None


@pytest.mark.asyncio
async def test_malformed_carryover_is_discarded_not_raised_and_starts_fresh_episode() -> None:
    """Project-level: a malformed carryover degrades to a fresh episode, with a warning."""
    entity = "binary_sensor.hallway_motion"
    row = _ha_row(
        row_id=_UUID_1,
        received_at=_NOW,
        entity_id=entity,
        domain="binary_sensor",
        device_class="motion",
        new_state="on",
    )
    adapter = HomeAssistantSensorActivityAdapter()
    read_conn = _FakeReadConn(table_exists=True, rows=[row])
    chron_conn = _FakeChroniclerConn(fetch_results=[[], []])

    upserted: list[Episode] = []

    async def _fake_upsert_episode(conn: object, episode: Episode) -> Episode:
        upserted.append(episode)
        episode.id = uuid.uuid4()
        return episode

    with (
        patch(
            "butlers.chronicler.adapters.home_assistant_sensor_activity.resolve_owner_entity_id",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "butlers.chronicler.adapters.home_assistant_sensor_activity.upsert_episode",
            side_effect=_fake_upsert_episode,
        ),
        patch(
            "butlers.chronicler.adapters.home_assistant_sensor_activity.upsert_owner_episode_entity",
            new=AsyncMock(),
        ),
        patch(
            "butlers.chronicler.adapters.home_assistant_sensor_activity.get_carryover",
            new=AsyncMock(return_value={entity: {"garbage": True}}),
        ),
        patch(
            "butlers.chronicler.adapters.home_assistant_sensor_activity.save_carryover",
            new=AsyncMock(),
        ),
    ):
        result = await adapter.project(
            _read_pool(read_conn), chronicler_pool=_chronicler_pool(chron_conn), since=None
        )

    assert result.episodes_closed == 1
    ep = upserted[0]
    assert ep.start_at == _NOW  # fresh episode, not extended from garbage carryover
    assert any("Discarding stale/malformed" in w for w in result.warnings)


@pytest.mark.asyncio
async def test_corroborated_span_promotes_to_activity() -> None:
    entity = "binary_sensor.hallway_motion"
    t1 = _NOW
    occupation_episode_id = uuid.uuid4()
    rows = [
        _ha_row(
            row_id=_UUID_1,
            received_at=t1,
            entity_id=entity,
            domain="binary_sensor",
            device_class="motion",
            new_state="on",
        ),
    ]
    adapter = HomeAssistantSensorActivityAdapter()
    read_conn = _FakeReadConn(table_exists=True, rows=rows)
    # First corroborator source (occupation_block) hits; second (spotify) empty.
    chron_conn = _FakeChroniclerConn(fetch_results=[[{"id": occupation_episode_id}], []])

    upserted: list[Episode] = []

    async def _fake_upsert_episode(conn: object, episode: Episode) -> Episode:
        upserted.append(episode)
        episode.id = uuid.uuid4()
        return episode

    with (
        patch(
            "butlers.chronicler.adapters.home_assistant_sensor_activity.resolve_owner_entity_id",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "butlers.chronicler.adapters.home_assistant_sensor_activity.upsert_episode",
            side_effect=_fake_upsert_episode,
        ),
        patch(
            "butlers.chronicler.adapters.home_assistant_sensor_activity.upsert_owner_episode_entity",
            new=AsyncMock(),
        ),
        patch(
            "butlers.chronicler.adapters.home_assistant_sensor_activity.get_carryover",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "butlers.chronicler.adapters.home_assistant_sensor_activity.save_carryover",
            new=AsyncMock(),
        ),
    ):
        await adapter.project(
            _read_pool(read_conn), chronicler_pool=_chronicler_pool(chron_conn), since=None
        )

    ep = upserted[0]
    assert ep.layer == Layer.ACTIVITY
    assert ep.confidence == Confidence.LOW
    assert str(occupation_episode_id) in ep.evidence_refs


# ---------------------------------------------------------------------------
# Retention-lag monitoring
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retention_lag_warning_when_watermark_near_oldest_partition() -> None:
    watermark = datetime(2026, 1, 15, tzinfo=UTC)  # 15 days into the oldest retained month
    conn = _FakeReadConn(
        table_exists=True,
        partition_names=[
            {"table_name": "filtered_events_202601"},
            {"table_name": "filtered_events_202602"},
        ],
    )
    pool = _read_pool(conn)
    warning = await HomeAssistantSensorActivityAdapter._check_retention_lag(pool, watermark)
    assert warning is not None
    assert str(RETENTION_LAG_WARNING_DAYS) in warning
    assert "202601" in warning


@pytest.mark.asyncio
async def test_retention_lag_no_warning_when_watermark_far_from_cutoff() -> None:
    watermark = datetime(2026, 6, 1, tzinfo=UTC)
    conn = _FakeReadConn(
        table_exists=True,
        partition_names=[{"table_name": "filtered_events_202601"}],
    )
    pool = _read_pool(conn)
    warning = await HomeAssistantSensorActivityAdapter._check_retention_lag(pool, watermark)
    assert warning is None


@pytest.mark.asyncio
async def test_retention_lag_skips_when_watermark_none() -> None:
    conn = _FakeReadConn(table_exists=True, partition_names=[])
    pool = _read_pool(conn)
    warning = await HomeAssistantSensorActivityAdapter._check_retention_lag(pool, None)
    assert warning is None


# ---------------------------------------------------------------------------
# source_name / registration sanity
# ---------------------------------------------------------------------------


def test_source_name_matches_contracts_registration() -> None:
    from butlers.chronicler.contracts import INITIAL_SOURCES

    names = {s.source_name for s in INITIAL_SOURCES}
    assert SOURCE_NAME in names
    assert SOURCE_NAME == "home_assistant.sensor_activity"


# ---------------------------------------------------------------------------
# Retroactive evidence -> activity promotion re-check (bu-mul8i)
# ---------------------------------------------------------------------------


class _RecheckChroniclerConn:
    """Chronicler-conn fake that separates candidate-SELECT from corroborator-SELECT."""

    def __init__(
        self,
        *,
        candidates: list[dict] | None = None,
        corroborators: list[dict] | None = None,
        execute_result: str = "UPDATE 1",
    ) -> None:
        self.candidates = candidates or []
        self.corroborators = corroborators or []
        self.candidate_calls: list[tuple] = []
        self.corroborator_calls: list[tuple] = []
        self.execute_calls: list[tuple] = []
        self.execute_result = execute_result

    async def fetch(self, query: str, *args: object) -> list:
        if "LIMIT $5" in query:
            self.candidate_calls.append((query, args))
            return [_Row(r) for r in self.candidates]
        self.corroborator_calls.append((query, args))
        return [_Row(r) for r in self.corroborators]

    async def execute(self, query: str, *args: object) -> str:
        self.execute_calls.append((query, args))
        return self.execute_result


def _evidence_span(*, span_id: uuid.UUID, start_at: datetime, end_at: datetime) -> dict:
    return {"id": span_id, "start_at": start_at, "end_at": end_at}


async def _run_recheck(
    adapter: HomeAssistantSensorActivityAdapter,
    conn: _RecheckChroniclerConn,
    *,
    now: datetime = _NOW,
) -> int:
    with patch(
        "butlers.chronicler.adapters.home_assistant_sensor_activity._now",
        return_value=now,
    ):
        return await adapter._recheck_evidence_promotions(_chronicler_pool(conn))


@pytest.mark.asyncio
async def test_recheck_promotes_span_whose_corroborator_arrived_late() -> None:
    span_id = uuid.uuid4()
    corroborator_id = uuid.uuid4()
    conn = _RecheckChroniclerConn(
        candidates=[
            _evidence_span(
                span_id=span_id,
                start_at=_NOW - timedelta(hours=1),
                end_at=_NOW - timedelta(minutes=50),
            )
        ],
        corroborators=[{"id": corroborator_id}],
    )
    promoted = await _run_recheck(HomeAssistantSensorActivityAdapter(), conn)

    assert promoted == 1
    assert len(conn.execute_calls) == 1
    query, args = conn.execute_calls[0]
    assert "UPDATE episodes" in query
    assert args[0] == span_id
    assert args[1] == Layer.ACTIVITY.value
    assert args[2] == Confidence.LOW.value
    assert args[3] == [str(corroborator_id)]


@pytest.mark.asyncio
async def test_recheck_candidate_query_is_bounded_to_the_lookback_window() -> None:
    conn = _RecheckChroniclerConn()
    adapter = HomeAssistantSensorActivityAdapter(promotion_lookback_hours=6)
    await _run_recheck(adapter, conn, now=_NOW)

    assert len(conn.candidate_calls) == 1
    query, args = conn.candidate_calls[0]
    assert "layer = $3" in query and "start_at >= $4" in query
    assert args[0] == SOURCE_NAME
    assert args[1] == EPISODE_TYPE_ROOM_ACTIVITY
    assert args[2] == Layer.EVIDENCE.value
    assert args[3] == _NOW - timedelta(hours=6)  # bounded, not a full-history sweep
    assert args[4] == PROMOTION_RECHECK_LIMIT


@pytest.mark.asyncio
async def test_recheck_default_lookback_uses_the_named_constant() -> None:
    conn = _RecheckChroniclerConn()
    await _run_recheck(HomeAssistantSensorActivityAdapter(), conn, now=_NOW)
    assert conn.candidate_calls[0][1][3] == _NOW - timedelta(hours=PROMOTION_LOOKBACK_HOURS)


@pytest.mark.asyncio
async def test_recheck_leaves_uncorroborated_span_alone() -> None:
    conn = _RecheckChroniclerConn(
        candidates=[_evidence_span(span_id=uuid.uuid4(), start_at=_NOW, end_at=_NOW)],
        corroborators=[],
    )
    promoted = await _run_recheck(HomeAssistantSensorActivityAdapter(), conn)

    assert promoted == 0
    assert conn.execute_calls == []  # no write at all — nothing to churn


@pytest.mark.asyncio
async def test_recheck_update_is_guarded_on_evidence_layer() -> None:
    """The guard is what makes a concurrent second pass a no-op, not a double-promote."""
    conn = _RecheckChroniclerConn(
        candidates=[_evidence_span(span_id=uuid.uuid4(), start_at=_NOW, end_at=_NOW)],
        corroborators=[{"id": uuid.uuid4()}],
        execute_result="UPDATE 0",  # another run promoted it between SELECT and UPDATE
    )
    promoted = await _run_recheck(HomeAssistantSensorActivityAdapter(), conn)

    assert promoted == 0
    query, args = conn.execute_calls[0]
    assert "AND layer = $5" in query
    assert args[4] == Layer.EVIDENCE.value


@pytest.mark.asyncio
async def test_recheck_treats_open_ended_span_like_an_instant() -> None:
    start = _NOW - timedelta(minutes=30)
    conn = _RecheckChroniclerConn(
        candidates=[{"id": uuid.uuid4(), "start_at": start, "end_at": None}],
        corroborators=[{"id": uuid.uuid4()}],
    )
    promoted = await _run_recheck(HomeAssistantSensorActivityAdapter(), conn)

    assert promoted == 1
    # Corroborator overlap is evaluated over [start, start], never against None.
    for _query, args in conn.corroborator_calls:
        assert args[2] == start
        assert args[3] == start


@pytest.mark.asyncio
async def test_clustering_and_recheck_share_one_corroboration_predicate() -> None:
    """Both promotion paths must route through the SAME predicate, or they drift."""
    entity = "binary_sensor.shared_predicate_motion"
    rows = [
        _ha_row(
            row_id=_UUID_1,
            received_at=_NOW,
            entity_id=entity,
            domain="binary_sensor",
            device_class="motion",
            new_state="on",
        ),
    ]
    adapter = HomeAssistantSensorActivityAdapter()
    read_conn = _FakeReadConn(table_exists=True, rows=rows)
    chron_conn = _FakeChroniclerConn()

    predicate = AsyncMock(return_value=[])

    async def _fake_upsert_episode(conn: object, episode: Episode) -> Episode:
        episode.id = uuid.uuid4()
        return episode

    with (
        patch.object(HomeAssistantSensorActivityAdapter, "_corroborator_episode_ids", predicate),
        patch(
            "butlers.chronicler.adapters.home_assistant_sensor_activity.resolve_owner_entity_id",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "butlers.chronicler.adapters.home_assistant_sensor_activity.upsert_episode",
            side_effect=_fake_upsert_episode,
        ),
        patch(
            "butlers.chronicler.adapters.home_assistant_sensor_activity.upsert_owner_episode_entity",
            new=AsyncMock(),
        ),
        patch(
            "butlers.chronicler.adapters.home_assistant_sensor_activity.get_carryover",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "butlers.chronicler.adapters.home_assistant_sensor_activity.save_carryover",
            new=AsyncMock(),
        ),
    ):
        await adapter.project(
            _read_pool(read_conn), chronicler_pool=_chronicler_pool(chron_conn), since=None
        )

    # Clustering consulted it; the re-check pass ran and would consult the very
    # same callable for any candidate it found.
    assert predicate.await_count >= 1
    assert not any("start_at < $4" in q for q, _ in chron_conn.fetch_calls), (
        "corroboration SQL must live only behind _corroborator_episode_ids"
    )


@pytest.mark.asyncio
async def test_project_runs_recheck_even_when_there_are_no_new_source_rows() -> None:
    """The late-corroborator case is precisely the empty-batch case."""
    adapter = HomeAssistantSensorActivityAdapter()
    read_conn = _FakeReadConn(table_exists=True, rows=[])
    recheck = AsyncMock(return_value=3)

    with patch.object(HomeAssistantSensorActivityAdapter, "_recheck_evidence_promotions", recheck):
        result = await adapter.project(
            _read_pool(read_conn), chronicler_pool=AsyncMock(), since=_NOW
        )

    recheck.assert_awaited_once()
    assert result.episodes_promoted == 3
