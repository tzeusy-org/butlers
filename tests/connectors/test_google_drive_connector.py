"""Condensed Google Drive connector tests — ingest.v1 contract only.

Verifies the connector-as-transport contract:
- ingest.v1 envelope production (field mapping, idempotency key)
- Change type detection logic (branching, non-trivial)
- parse_changes_list_response extraction

[bu-35fm7]
"""

from __future__ import annotations

from typing import Any

import pytest

from butlers.connectors.google_drive import (
    _CHANGE_TYPE_CREATED,
    _CHANGE_TYPE_MODIFIED,
    _CHANGE_TYPE_MOVED,
    _CHANGE_TYPE_RENAMED,
    _CHANGE_TYPE_SHARING_CHANGED,
    _CHANGE_TYPE_TRASHED,
    _build_ingest_envelope,
    _detect_change_type,
    _FileMetadata,
    _make_idempotency_key,
    parse_changes_list_response,
)

_FAKE_EMAIL = "user@example.com"
_FAKE_FILE_ID = "gdrive-file-abc123"
_ENDPOINT = f"google_drive:user:{_FAKE_EMAIL}"
_OBSERVED_AT = "2026-03-26T10:00:00+00:00"


def _make_cached(
    name: str = "file.txt",
    parents: list[str] | None = None,
    shared: bool = False,
) -> _FileMetadata:
    return _FileMetadata(
        file_id=_FAKE_FILE_ID,
        name=name,
        mime_type="text/plain",
        parents=parents or ["p1"],
        shared=shared,
        modified_time=None,
    )


# ---------------------------------------------------------------------------
# Envelope contract tests
# ---------------------------------------------------------------------------


@pytest.fixture
def base_envelope() -> dict[str, Any]:
    return _build_ingest_envelope(
        file_id=_FAKE_FILE_ID,
        change_sequence=1,
        endpoint_identity=_ENDPOINT,
        observed_at=_OBSERVED_AT,
        normalized_text="file_created: report.pdf",
        idempotency_key=_make_idempotency_key(_ENDPOINT, _FAKE_FILE_ID, "1711447200"),
    )


def test_envelope_contract_fields(base_envelope: dict[str, Any]) -> None:
    """Envelope carries ingest.v1 schema, drive source, metadata tier, null raw, no extras."""
    assert base_envelope["schema_version"] == "ingest.v1"
    assert base_envelope["source"]["channel"] == "google_drive"
    assert base_envelope["source"]["provider"] == "google_drive"
    assert base_envelope["source"]["endpoint_identity"] == _ENDPOINT
    assert base_envelope["payload"]["raw"] is None  # metadata-tier only
    assert base_envelope["control"]["ingestion_tier"] == "metadata"
    assert "event_type" not in base_envelope["event"]  # IngestEventV1 extra=forbid


def test_envelope_event_id_and_thread_id(base_envelope: dict[str, Any]) -> None:
    """event_id is 'gdrive:<file_id>:<seq>'; thread_id=file_id groups same-file changes."""
    assert base_envelope["event"]["external_event_id"] == f"gdrive:{_FAKE_FILE_ID}:1"
    assert base_envelope["event"]["external_thread_id"] == _FAKE_FILE_ID


def test_envelope_validates_against_parse_ingest_envelope(base_envelope: dict[str, Any]) -> None:
    """Envelope must validate against the canonical parse_ingest_envelope contract."""
    from pydantic import ValidationError

    from butlers.tools.switchboard.routing.contracts import parse_ingest_envelope

    try:
        parse_ingest_envelope(base_envelope)
    except ValidationError as exc:
        pytest.fail(f"parse_ingest_envelope raised ValidationError: {exc}")


def test_idempotency_key_format() -> None:
    """Idempotency key follows 'gdrive:<endpoint>:<file_id>:<epoch>' format."""
    key = _make_idempotency_key(_ENDPOINT, _FAKE_FILE_ID, "1711447200")
    assert key.startswith("gdrive:")
    assert _ENDPOINT in key
    assert _FAKE_FILE_ID in key
    assert "1711447200" in key


def test_idempotency_key_deterministic() -> None:
    """Same inputs always produce the same idempotency key."""
    key1 = _make_idempotency_key(_ENDPOINT, _FAKE_FILE_ID, "1711447200")
    key2 = _make_idempotency_key(_ENDPOINT, _FAKE_FILE_ID, "1711447200")
    assert key1 == key2


def test_idempotency_key_differs_for_different_files() -> None:
    """Different file IDs produce different idempotency keys."""
    key1 = _make_idempotency_key(_ENDPOINT, "file-1", "1711447200")
    key2 = _make_idempotency_key(_ENDPOINT, "file-2", "1711447200")
    assert key1 != key2


# ---------------------------------------------------------------------------
# Change type detection (complex branching logic — keep)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "change,cached,expected",
    [
        # Trashed by removed flag
        ({"removed": True}, None, _CHANGE_TYPE_TRASHED),
        # Trashed by file flag
        ({"file": {"trashed": True, "name": "f"}}, _make_cached(), _CHANGE_TYPE_TRASHED),
        # Created: no cached entry
        ({"file": {"name": "new.txt"}}, None, _CHANGE_TYPE_CREATED),
        # Renamed: name changed
        (
            {"file": {"name": "new.txt", "parents": ["p1"]}},
            _make_cached(name="old.txt", parents=["p1"]),
            _CHANGE_TYPE_RENAMED,
        ),
        # Moved: parent changed
        (
            {"file": {"name": "f.txt", "parents": ["p2"]}},
            _make_cached(name="f.txt", parents=["p1"]),
            _CHANGE_TYPE_MOVED,
        ),
        # Sharing changed
        (
            {"file": {"name": "f", "shared": True}},
            _make_cached(name="f", shared=False),
            _CHANGE_TYPE_SHARING_CHANGED,
        ),
        # Modified (fallback for known file)
        (
            {"file": {"name": "f", "modifiedTime": "2026-01-02T00:00:00Z"}},
            _make_cached(name="f"),
            _CHANGE_TYPE_MODIFIED,
        ),
    ],
    ids=[
        "removed_flag_trashed",
        "file_trashed",
        "file_created",
        "file_renamed",
        "file_moved",
        "sharing_changed",
        "file_modified",
    ],
)
def test_detect_change_type(
    change: dict[str, Any], cached: _FileMetadata | None, expected: str
) -> None:
    result = _detect_change_type(change, cached=cached)
    assert result == expected


# ---------------------------------------------------------------------------
# parse_changes_list_response contract
# ---------------------------------------------------------------------------


def test_parse_changes_list_empty_returns_empty_list() -> None:
    changes, _, _ = parse_changes_list_response({"changes": []})
    assert changes == []


def test_parse_changes_list_extracts_changes() -> None:
    payload = {
        "changes": [{"fileId": "f1", "file": {"name": "a.txt"}}, {"fileId": "f2", "removed": True}],
        "nextPageToken": "tok",
    }
    changes, next_token, _ = parse_changes_list_response(payload)
    assert len(changes) == 2
    assert next_token == "tok"


def test_parse_changes_list_missing_key_returns_empty() -> None:
    """Missing 'changes' key treated gracefully."""
    changes, _, _ = parse_changes_list_response({})
    assert changes == []


# ---------------------------------------------------------------------------
# Health-port / heartbeat override wiring [bu-v7qu5]
# ---------------------------------------------------------------------------


def test_process_config_parses_health_and_heartbeat_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """from_env honors CONNECTOR_HEALTH_PORT / CONNECTOR_HEARTBEAT_INTERVAL_S."""
    from butlers.connectors.google_drive import GDriveProcessConfig

    monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://switchboard.local/mcp")
    monkeypatch.setenv("CONNECTOR_HEALTH_PORT", "45123")
    monkeypatch.setenv("CONNECTOR_HEARTBEAT_INTERVAL_S", "47")

    cfg = GDriveProcessConfig.from_env()

    assert cfg.health_port == 45123
    assert cfg.heartbeat_interval_s == 47


async def test_run_connector_threads_health_overrides_into_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_google_drive_connector must pass the parsed health-port + heartbeat
    overrides through to GDriveConnectorManager (regression guard for bu-v7qu5:
    the construction call previously omitted them, so the health server always
    bound the default 40088 and CONNECTOR_HEARTBEAT_INTERVAL_S was dropped)."""
    import asyncpg

    import butlers.connectors.google_drive as gd
    from butlers import credential_store, db
    from butlers.connectors import cursor_store

    captured: dict[str, Any] = {}

    class _StubPool:
        async def close(self) -> None:
            return None

    class _StubManager:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

        async def start(self) -> None:
            return None

        async def stop(self) -> None:
            return None

    async def _fake_create_pool(**_kwargs: Any) -> _StubPool:
        return _StubPool()

    async def _fake_cursor_pool() -> None:
        return None

    monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://switchboard.local/mcp")
    monkeypatch.setenv("CONNECTOR_HEALTH_PORT", "45321")
    monkeypatch.setenv("CONNECTOR_HEARTBEAT_INTERVAL_S", "53")

    monkeypatch.setattr(gd, "GDriveConnectorManager", _StubManager)
    monkeypatch.setattr(asyncpg, "create_pool", _fake_create_pool)
    monkeypatch.setattr(cursor_store, "create_cursor_pool_from_env", _fake_cursor_pool)
    monkeypatch.setattr(credential_store, "shared_db_name_from_env", lambda: "butlers")
    monkeypatch.setattr(
        db,
        "db_params_from_env",
        lambda: {"host": "localhost", "port": 5432, "user": "u", "password": "p"},
    )
    monkeypatch.setattr(db, "register_jsonb_codec", lambda *a, **k: None)
    monkeypatch.setattr(db, "should_retry_with_ssl_disable", lambda *a, **k: False)

    await gd.run_google_drive_connector()

    assert captured["health_port"] == 45321
    assert captured["heartbeat_interval_s"] == 53
