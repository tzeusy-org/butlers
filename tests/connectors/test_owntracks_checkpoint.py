"""Tests for OwnTracks connector: checkpoint and deduplication (tasks 4.1–4.4)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.connectors.owntracks import (
    CONNECTOR_TYPE,
    OwnTracksCheckpoint,
    build_idempotency_key,
    extract_event_type,
    extract_tst,
    is_duplicate_event,
    load_checkpoint,
    save_checkpoint,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_pool() -> MagicMock:
    """Create a mock asyncpg pool with acquire() returning an async context manager."""
    mock_conn = AsyncMock()
    mock_pool = MagicMock()
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)
    mock_pool.acquire.return_value = mock_ctx
    return mock_pool


# ---------------------------------------------------------------------------
# OwnTracksCheckpoint serialisation (task 4.1)
# ---------------------------------------------------------------------------


class TestOwnTracksCheckpointSerialisation:
    """Tests for OwnTracksCheckpoint JSON round-trip."""

    def test_to_json_with_last_tst(self) -> None:
        """to_json() produces a JSON string with last_tst key."""
        cp = OwnTracksCheckpoint(last_tst=1711360400)
        raw = cp.to_json()
        data = json.loads(raw)
        assert data["last_tst"] == 1711360400

    def test_to_json_with_none_last_tst(self) -> None:
        """to_json() serialises None last_tst as JSON null."""
        cp = OwnTracksCheckpoint(last_tst=None)
        raw = cp.to_json()
        data = json.loads(raw)
        assert data["last_tst"] is None

    def test_from_json_round_trips(self) -> None:
        """from_json(to_json()) returns the same checkpoint."""
        cp = OwnTracksCheckpoint(last_tst=1711360400)
        assert OwnTracksCheckpoint.from_json(cp.to_json()) == cp

    def test_from_json_null_tst(self) -> None:
        """from_json() accepts null last_tst."""
        cp = OwnTracksCheckpoint.from_json('{"last_tst": null}')
        assert cp.last_tst is None

    def test_from_json_rejects_invalid_json(self) -> None:
        """from_json() raises ValueError for non-JSON input."""
        with pytest.raises(ValueError, match="not valid JSON"):
            OwnTracksCheckpoint.from_json("not-json")

    def test_from_json_rejects_missing_key(self) -> None:
        """from_json() raises ValueError when last_tst key is absent."""
        with pytest.raises(ValueError, match="missing 'last_tst'"):
            OwnTracksCheckpoint.from_json('{"other_key": 42}')

    def test_from_json_rejects_non_int_tst(self) -> None:
        """from_json() raises ValueError when last_tst is not int or null."""
        with pytest.raises(ValueError, match="must be int or null"):
            OwnTracksCheckpoint.from_json('{"last_tst": "not-an-int"}')


# ---------------------------------------------------------------------------
# OwnTracksCheckpoint.advance (task 4.1)
# ---------------------------------------------------------------------------


class TestOwnTracksCheckpointAdvance:
    """Tests for OwnTracksCheckpoint.advance()."""

    def test_advance_sets_value_when_none(self) -> None:
        """advance() sets last_tst from None to tst."""
        cp = OwnTracksCheckpoint(last_tst=None)
        cp.advance(1711360400)
        assert cp.last_tst == 1711360400

    def test_advance_updates_when_newer(self) -> None:
        """advance() updates last_tst when tst is strictly newer."""
        cp = OwnTracksCheckpoint(last_tst=1711360400)
        cp.advance(1711360500)
        assert cp.last_tst == 1711360500

    def test_advance_does_not_update_when_equal(self) -> None:
        """advance() does not update last_tst when tst equals current value."""
        cp = OwnTracksCheckpoint(last_tst=1711360400)
        cp.advance(1711360400)
        assert cp.last_tst == 1711360400

    def test_advance_does_not_update_when_older(self) -> None:
        """advance() does not update last_tst when tst is older."""
        cp = OwnTracksCheckpoint(last_tst=1711360400)
        cp.advance(1711360300)
        assert cp.last_tst == 1711360400


# ---------------------------------------------------------------------------
# Idempotency key construction (task 4.3)
# ---------------------------------------------------------------------------


class TestBuildIdempotencyKey:
    """Tests for build_idempotency_key()."""

    def test_format_for_location_event(self) -> None:
        """Idempotency key has correct owntracks:<id>:<tst>:<type> format."""
        key = build_idempotency_key("owntracks:alice:phone", 1711360400, "location")
        assert key == "owntracks:owntracks:alice:phone:1711360400:location"

    def test_format_for_transition_event(self) -> None:
        """Idempotency key works for transition events."""
        key = build_idempotency_key("device123", 1711370000, "transition")
        assert key == "owntracks:device123:1711370000:transition"

    def test_format_for_waypoint_event(self) -> None:
        """Idempotency key works for waypoint events."""
        key = build_idempotency_key("user:bob", 1711380000, "waypoint")
        assert key == "owntracks:user:bob:1711380000:waypoint"

    def test_starts_with_owntracks_prefix(self) -> None:
        """Idempotency key always starts with 'owntracks:'."""
        key = build_idempotency_key("any", 1, "location")
        assert key.startswith("owntracks:")

    def test_contains_tst_as_string(self) -> None:
        """Idempotency key contains the tst value as a string component."""
        tst = 1711360400
        key = build_idempotency_key("ep", tst, "location")
        assert str(tst) in key

    def test_contains_event_type(self) -> None:
        """Idempotency key contains the event_type as the last component."""
        key = build_idempotency_key("ep", 123, "location")
        assert key.endswith(":location")

    def test_two_events_different_tst_differ(self) -> None:
        """Different tst values produce different idempotency keys."""
        key1 = build_idempotency_key("ep", 100, "location")
        key2 = build_idempotency_key("ep", 101, "location")
        assert key1 != key2

    def test_two_events_different_type_differ(self) -> None:
        """Different _type values produce different idempotency keys."""
        key1 = build_idempotency_key("ep", 100, "location")
        key2 = build_idempotency_key("ep", 100, "transition")
        assert key1 != key2

    def test_two_events_same_all_equal(self) -> None:
        """Same endpoint, tst, and type produce identical keys (idempotent)."""
        key1 = build_idempotency_key("ep", 100, "location")
        key2 = build_idempotency_key("ep", 100, "location")
        assert key1 == key2


# ---------------------------------------------------------------------------
# Deduplication (task 4.4)
# ---------------------------------------------------------------------------


class TestIsDuplicateEvent:
    """Tests for is_duplicate_event()."""

    def test_not_duplicate_when_no_checkpoint(self) -> None:
        """Any event is processed when no checkpoint exists (first run)."""
        cp = OwnTracksCheckpoint(last_tst=None)
        assert is_duplicate_event(cp, 1711360400) is False

    def test_not_duplicate_when_tst_after_checkpoint(self) -> None:
        """Events strictly after the checkpoint are not duplicates."""
        cp = OwnTracksCheckpoint(last_tst=1711360400)
        assert is_duplicate_event(cp, 1711360401) is False

    def test_duplicate_when_tst_equals_checkpoint(self) -> None:
        """Events at exactly the checkpoint timestamp are duplicates."""
        cp = OwnTracksCheckpoint(last_tst=1711360400)
        assert is_duplicate_event(cp, 1711360400) is True

    def test_duplicate_when_tst_before_checkpoint(self) -> None:
        """Events before the checkpoint timestamp are duplicates."""
        cp = OwnTracksCheckpoint(last_tst=1711360400)
        assert is_duplicate_event(cp, 1711360399) is True

    def test_duplicate_much_older_event(self) -> None:
        """Very old events (e.g. replayed) are correctly identified as duplicates."""
        cp = OwnTracksCheckpoint(last_tst=1711360400)
        assert is_duplicate_event(cp, 1000000000) is True

    def test_first_event_after_reset(self) -> None:
        """After checkpoint reset to None, the first event is always processed."""
        cp = OwnTracksCheckpoint(last_tst=None)
        assert is_duplicate_event(cp, 0) is False


# ---------------------------------------------------------------------------
# Checkpoint persistence — load_checkpoint (task 4.2)
# ---------------------------------------------------------------------------


class TestLoadCheckpoint:
    """Tests for load_checkpoint()."""

    async def test_returns_checkpoint_from_db(self) -> None:
        """load_checkpoint() returns populated checkpoint when DB has cursor."""
        pool = _make_mock_pool()
        conn = pool.acquire.return_value.__aenter__.return_value
        conn.fetchrow.return_value = {"checkpoint_cursor": json.dumps({"last_tst": 1711360400})}

        cp = await load_checkpoint(pool, "owntracks:alice:phone")

        assert cp.last_tst == 1711360400

    async def test_returns_empty_checkpoint_when_no_row(self) -> None:
        """load_checkpoint() returns empty checkpoint when no DB row exists."""
        pool = _make_mock_pool()
        conn = pool.acquire.return_value.__aenter__.return_value
        conn.fetchrow.return_value = None

        cp = await load_checkpoint(pool, "owntracks:alice:phone")

        assert cp.last_tst is None

    async def test_returns_empty_checkpoint_on_db_error(self) -> None:
        """load_checkpoint() returns empty checkpoint on DB failure (no crash)."""
        pool = _make_mock_pool()
        conn = pool.acquire.return_value.__aenter__.return_value
        conn.fetchrow.side_effect = RuntimeError("DB down")

        cp = await load_checkpoint(pool, "owntracks:alice:phone")

        assert cp.last_tst is None

    async def test_returns_empty_checkpoint_on_malformed_json(self) -> None:
        """load_checkpoint() returns empty checkpoint when cursor JSON is malformed."""
        pool = _make_mock_pool()
        conn = pool.acquire.return_value.__aenter__.return_value
        conn.fetchrow.return_value = {"checkpoint_cursor": "not-valid-json"}

        cp = await load_checkpoint(pool, "owntracks:alice:phone")

        assert cp.last_tst is None

    async def test_uses_connector_type_owntracks(self) -> None:
        """load_checkpoint() queries with connector_type='owntracks'."""
        pool = _make_mock_pool()
        conn = pool.acquire.return_value.__aenter__.return_value
        conn.fetchrow.return_value = None

        await load_checkpoint(pool, "ep")

        args = conn.fetchrow.await_args[0]
        # Second arg to fetchrow is connector_type
        assert args[1] == CONNECTOR_TYPE

    async def test_uses_provided_endpoint_identity(self) -> None:
        """load_checkpoint() queries with the supplied endpoint_identity."""
        pool = _make_mock_pool()
        conn = pool.acquire.return_value.__aenter__.return_value
        conn.fetchrow.return_value = None

        await load_checkpoint(pool, "owntracks:bob:watch")

        args = conn.fetchrow.await_args[0]
        assert args[2] == "owntracks:bob:watch"


# ---------------------------------------------------------------------------
# Checkpoint persistence — save_checkpoint (task 4.1)
# ---------------------------------------------------------------------------


class TestSaveCheckpoint:
    """Tests for save_checkpoint()."""

    async def test_saves_checkpoint_to_db(self) -> None:
        """save_checkpoint() calls save_cursor with correct parameters."""
        pool = _make_mock_pool()
        conn = pool.acquire.return_value.__aenter__.return_value

        cp = OwnTracksCheckpoint(last_tst=1711360400)
        await save_checkpoint(pool, "owntracks:alice:phone", cp)

        conn.execute.assert_awaited_once()
        args = conn.execute.await_args[0]
        assert args[1] == CONNECTOR_TYPE
        assert args[2] == "owntracks:alice:phone"
        stored = json.loads(args[3])
        assert stored["last_tst"] == 1711360400

    async def test_does_not_raise_on_db_error(self) -> None:
        """save_checkpoint() swallows DB errors (fail-safe)."""
        pool = _make_mock_pool()
        conn = pool.acquire.return_value.__aenter__.return_value
        conn.execute.side_effect = RuntimeError("DB down")

        cp = OwnTracksCheckpoint(last_tst=1711360400)
        # Must not raise
        await save_checkpoint(pool, "ep", cp)

    async def test_saves_none_tst(self) -> None:
        """save_checkpoint() correctly persists a checkpoint with last_tst=None."""
        pool = _make_mock_pool()
        conn = pool.acquire.return_value.__aenter__.return_value

        cp = OwnTracksCheckpoint(last_tst=None)
        await save_checkpoint(pool, "ep", cp)

        args = conn.execute.await_args[0]
        stored = json.loads(args[3])
        assert stored["last_tst"] is None


# ---------------------------------------------------------------------------
# Round-trip: load → process → advance → save → reload (task 4.2 + 4.4)
# ---------------------------------------------------------------------------


class TestCheckpointRoundTrip:
    """Integration-style tests for the full checkpoint round-trip using mock pool."""

    async def test_advance_and_persist_then_reload(self) -> None:
        """Saving an advanced checkpoint and reloading it returns the updated tst."""
        stored: dict[str, str | None] = {}

        async def mock_execute(sql: str, *args: object) -> None:
            if "INSERT" in sql:
                stored["cursor"] = str(args[2])

        async def mock_fetchrow(sql: str, *args: object) -> dict | None:
            if "SELECT" in sql and "cursor" in stored:
                return {"checkpoint_cursor": stored["cursor"]}
            return None

        pool = _make_mock_pool()
        conn = pool.acquire.return_value.__aenter__.return_value
        conn.execute.side_effect = mock_execute
        conn.fetchrow.side_effect = mock_fetchrow

        endpoint = "owntracks:alice:phone"

        # Start fresh
        cp = await load_checkpoint(pool, endpoint)
        assert cp.last_tst is None

        # Process an event and advance
        tst = 1711360400
        assert is_duplicate_event(cp, tst) is False
        cp.advance(tst)
        await save_checkpoint(pool, endpoint, cp)

        # Reload and check the same event is now a duplicate
        cp2 = await load_checkpoint(pool, endpoint)
        assert cp2.last_tst == tst
        assert is_duplicate_event(cp2, tst) is True

        # A newer event is still processed
        assert is_duplicate_event(cp2, tst + 1) is False

    async def test_dedup_prevents_reprocessing_on_restart(self) -> None:
        """After a simulated restart, replayed events are deduped correctly."""
        tst = 1711360400
        raw_cursor = json.dumps({"last_tst": tst})

        pool = _make_mock_pool()
        conn = pool.acquire.return_value.__aenter__.return_value
        conn.fetchrow.return_value = {"checkpoint_cursor": raw_cursor}

        cp = await load_checkpoint(pool, "ep")

        # The event at the checkpoint is a duplicate
        assert is_duplicate_event(cp, tst) is True
        # An older event is also a duplicate
        assert is_duplicate_event(cp, tst - 1) is True
        # The next event is fresh
        assert is_duplicate_event(cp, tst + 1) is False


# ---------------------------------------------------------------------------
# Payload extraction helpers
# ---------------------------------------------------------------------------


class TestExtractTst:
    """Tests for extract_tst() helper."""

    def test_extracts_integer_tst(self) -> None:
        """extract_tst returns integer tst from payload."""
        assert extract_tst({"tst": 1711360400, "_type": "location"}) == 1711360400

    def test_converts_float_to_int(self) -> None:
        """extract_tst converts float tst to int."""
        assert extract_tst({"tst": 1711360400.9}) == 1711360400

    def test_returns_none_when_absent(self) -> None:
        """extract_tst returns None when tst is absent."""
        assert extract_tst({"_type": "location"}) is None

    def test_returns_none_for_string_tst(self) -> None:
        """extract_tst returns None when tst is a string."""
        assert extract_tst({"tst": "not-an-int"}) is None

    def test_returns_none_for_none_tst(self) -> None:
        """extract_tst returns None when tst is None."""
        assert extract_tst({"tst": None}) is None


class TestExtractEventType:
    """Tests for extract_event_type() helper."""

    def test_extracts_location_type(self) -> None:
        """extract_event_type returns 'location' for location payloads."""
        assert extract_event_type({"_type": "location"}) == "location"

    def test_extracts_transition_type(self) -> None:
        """extract_event_type returns 'transition' for transition payloads."""
        assert extract_event_type({"_type": "transition"}) == "transition"

    def test_returns_unknown_when_absent(self) -> None:
        """extract_event_type returns 'unknown' when _type is absent."""
        assert extract_event_type({}) == "unknown"

    def test_converts_non_string_to_str(self) -> None:
        """extract_event_type coerces non-string _type to string."""
        result = extract_event_type({"_type": 42})
        assert result == "42"
