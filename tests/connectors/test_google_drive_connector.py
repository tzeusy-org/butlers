"""Condensed Google Drive connector tests — ingest.v1 contract only.

Verifies the connector-as-transport contract:
- ingest.v1 envelope production (field mapping, idempotency key)
- Change type detection logic (branching, non-trivial)
- parse_changes_list_response extraction

[bu-35fm7]
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from butlers.connectors.google_drive import (
    _CHANGE_TYPE_CREATED,
    _CHANGE_TYPE_MODIFIED,
    _CHANGE_TYPE_MOVED,
    _CHANGE_TYPE_RENAMED,
    _CHANGE_TYPE_SHARING_CHANGED,
    _CHANGE_TYPE_TRASHED,
    DriveAccountSource,
    GDriveAccountConfig,
    GDriveAccountLoop,
    _build_ingest_envelope,
    _classify_source_api_error,
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

    from butlers.tools.switchboard.contracts import parse_ingest_envelope

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
    overrides through to DriveAccountSource (regression guard for bu-v7qu5:
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

    monkeypatch.setattr(gd, "DriveAccountSource", _StubManager)
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


# ---------------------------------------------------------------------------
# Source API health classification (bu-q2m3n)
# ---------------------------------------------------------------------------


def _google_token_http_error(payload: dict[str, Any], status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://oauth2.googleapis.com/token")
    response = httpx.Response(status_code, json=payload, request=request)
    return httpx.HTTPStatusError("error", request=request, response=response)


@pytest.mark.parametrize(
    ("payload", "status_code", "is_auth_revocation"),
    [
        (
            {"error": "invalid_grant", "error_description": "Token expired or revoked"},
            400,
            True,
        ),
        (
            {"error": "unauthorized_client", "error_description": "Client is not authorized"},
            400,
            True,
        ),
        ({"error": {"code": 401, "message": "Invalid Credentials"}}, 401, False),
        ({"error": {"code": 403, "message": "Insufficient Permission"}}, 403, False),
        ({"error": {"code": 503, "message": "Backend Error"}}, 503, False),
    ],
)
def test_classify_source_api_error_distinguishes_google_revocation_from_api_failure(
    payload: dict[str, Any], status_code: int, is_auth_revocation: bool
) -> None:
    """Drive follows the Google token contract without coupling to Gmail internals."""
    classified, detail = _classify_source_api_error(_google_token_http_error(payload, status_code))

    assert classified is is_auth_revocation
    if is_auth_revocation:
        assert payload["error"] in detail
    else:
        assert str(status_code) in detail


@pytest.mark.parametrize(
    "url",
    [
        "https://www.googleapis.com/drive/v3/changes",
        "https://oauth2.googleapis.com/token",
    ],
)
def test_drive_non_post_token_response_never_proves_token_revocation(url: str) -> None:
    """Drive data APIs must not promote a payload-shaped transient failure."""
    request = httpx.Request("GET", url)
    response = httpx.Response(
        403,
        json={"error": "invalid_grant", "error_description": "Synthetic resource failure"},
        request=request,
    )
    error = httpx.HTTPStatusError("resource API failure", request=request, response=response)

    classified, _ = _classify_source_api_error(error)

    assert classified is False


def test_drive_account_health_reports_revocation_as_error_and_api_failure_as_degraded() -> None:
    config = GDriveAccountConfig(
        email=_FAKE_EMAIL,
        client_id="client-id",
        client_secret="client-secret",
        refresh_token="refresh-token",
        switchboard_mcp_url="http://switchboard.test/mcp",
    )
    account = GDriveAccountLoop(_FAKE_EMAIL, config)
    account._record_source_api_failure(
        _google_token_http_error({"error": {"code": 503, "message": "Backend Error"}}, 503)
    )

    health = account.get_health()
    assert health.status == "degraded"
    assert health.error == "code=503, message=Backend Error"

    account._record_source_api_failure(
        _google_token_http_error(
            {"error": "invalid_grant", "error_description": "Token expired or revoked"}, 400
        )
    )

    health = account.get_health()
    assert health.status == "error"
    assert health.error == "error=invalid_grant, description=Token expired or revoked"


def test_drive_manager_heartbeat_preserves_degraded_source_error() -> None:
    """A recoverable Drive outage remains diagnosable in the dashboard heartbeat."""
    config = GDriveAccountConfig(
        email=_FAKE_EMAIL,
        client_id="client-id",
        client_secret="client-secret",
        refresh_token="refresh-token",
        switchboard_mcp_url="http://switchboard.test/mcp",
    )
    account = GDriveAccountLoop(_FAKE_EMAIL, config)
    account._record_source_api_failure(
        _google_token_http_error({"error": {"code": 503, "message": "Backend Error"}}, 503)
    )
    manager = DriveAccountSource(db_pool=None, credential_store=object())
    manager._loops[_FAKE_EMAIL] = account

    assert manager._get_health_state_for_heartbeat() == (
        "degraded",
        "code=503, message=Backend Error",
    )


def _drive_account_with_cache(name: str) -> tuple[GDriveAccountLoop, Any]:
    from unittest.mock import AsyncMock, MagicMock

    config = GDriveAccountConfig(
        email=_FAKE_EMAIL,
        client_id="client-id",
        client_secret="client-secret",
        refresh_token="refresh-token",
        switchboard_mcp_url="http://switchboard.test/mcp",
    )
    account = GDriveAccountLoop(_FAKE_EMAIL, config)
    conn = AsyncMock()
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=None)
    account._cursor_pool = MagicMock()
    account._cursor_pool.acquire.return_value = ctx
    account._metadata_cache = {_FAKE_FILE_ID: _make_cached(name=name)}
    return account, conn


async def test_metadata_cache_save_withholds_code_in_file_name() -> None:
    account, conn = _drive_account_with_cache("Your code is 482913.txt")
    await account._save_metadata_cache_to_store()
    saved = conn.execute.await_args.args[3][_FAKE_FILE_ID]
    assert "482913" not in saved["name"]
    assert saved["mime_type"] == "text/plain" and saved["parents"] == ["p1"]

    account, conn = _drive_account_with_cache("Budget 482913.xlsx")
    await account._save_metadata_cache_to_store()
    assert conn.execute.await_args.args[3][_FAKE_FILE_ID]["name"] == "Budget 482913.xlsx"


async def test_metadata_cache_save_skips_write_when_detector_fails(caplog) -> None:
    from unittest.mock import patch

    account, conn = _drive_account_with_cache("Your code is 482913.txt")
    with patch("butlers.connectors.google_drive.scrub_text", side_effect=RuntimeError("482913")):
        await account._save_metadata_cache_to_store()
    conn.execute.assert_not_awaited()
    assert "bearer scrub failed" in caplog.text
    assert "482913" not in caplog.text and "Your code" not in caplog.text


async def test_scrubbed_cache_reloaded_after_restart_does_not_fake_a_rename() -> None:
    """A saved (scrubbed) name reloads and still classifies the unchanged raw name as modified."""
    from unittest.mock import AsyncMock

    raw_name = "Your code is 482913.txt"
    account, conn = _drive_account_with_cache(raw_name)
    await account._save_metadata_cache_to_store()
    saved = conn.execute.await_args.args[3]
    assert "482913" not in saved[_FAKE_FILE_ID]["name"]

    restarted, restarted_conn = _drive_account_with_cache("unused")
    restarted._metadata_cache = {}
    restarted_conn.fetchrow = AsyncMock(return_value={"settings": {"metadata_cache": saved}})
    await restarted._load_metadata_cache_from_store()
    cached = restarted._metadata_cache[_FAKE_FILE_ID]

    change = {"file": {"id": _FAKE_FILE_ID, "name": raw_name, "parents": ["p1"]}}
    assert _detect_change_type(change, cached) == _CHANGE_TYPE_MODIFIED
    change["file"]["name"] = "Quarterly plan.txt"
    assert _detect_change_type(change, cached) == _CHANGE_TYPE_RENAMED

    # In steady state the raw in-memory cache sees a rename that only changes a withheld span.
    live = _make_cached(name="Code of Conduct 2025.pdf")
    change["file"]["name"] = "Code of Conduct 2026.pdf"
    assert _detect_change_type(change, live) == _CHANGE_TYPE_RENAMED


@pytest.mark.parametrize("source", ["gmail", "calendar", "drive"])
async def test_google_fleet_real_adapters_reconcile_and_keep_source_policy(monkeypatch, source):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    from butlers.connectors import gmail, google_calendar, google_drive

    loop_type = {
        "gmail": "GmailAccountLoop",
        "calendar": "CalendarAccountLoop",
        "drive": "GDriveAccountLoop",
    }[source]
    module = {"gmail": gmail, "calendar": google_calendar, "drive": google_drive}[source]
    made = []

    def loop_factory(**kwargs):
        loop = SimpleNamespace(
            start=MagicMock(),
            stop=AsyncMock(),
            is_running=True,
            _error=None,
            config=kwargs["config"],
        )
        made.append((kwargs, loop))
        return loop

    monkeypatch.setattr(module, loop_type, loop_factory)
    if source == "gmail":
        fleet = gmail.GmailAccountSource(
            gmail.GmailProcessConfig(switchboard_mcp_url="http://switchboard.invalid/mcp"),
            None,
            None,
        )
    elif source == "calendar":
        fleet = google_calendar.CalendarAccountSource(
            google_calendar.CalendarProcessConfig(
                switchboard_mcp_url="http://switchboard.invalid/mcp"
            ),
            None,
            None,
        )
    else:
        fleet = DriveAccountSource(db_pool=None, credential_store=object())
    selected = []
    # Exercise real source discovery, scope filtering and source configuration.
    connection = AsyncMock()
    connection.fetch.side_effect = lambda *args: selected.copy()
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=connection)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    fleet._db_pool = pool
    creds = dict(client_id="id", client_secret="secret", refresh_token="refresh")
    if source == "drive":
        monkeypatch.setattr(
            module, "resolve_google_credentials", AsyncMock(return_value=SimpleNamespace(**creds))
        )
    else:
        fleet._resolve_credentials_for_account = AsyncMock(return_value=creds)
    assert await fleet.reload_accounts() == {"added": [], "removed": [], "unchanged": []}
    scope = {
        "gmail": "https://www.googleapis.com/auth/gmail.readonly",
        "calendar": "https://www.googleapis.com/auth/calendar.readonly",
        "drive": "https://www.googleapis.com/auth/drive.readonly",
    }[source]
    selected[:] = [
        dict(email="a@example.invalid", granted_scopes=[scope], metadata={}),
        dict(email="excluded@example.invalid", granted_scopes=["email"], metadata={}),
    ]
    assert (await fleet.reload_accounts())["added"] == ["a@example.invalid"]
    original = fleet._loops["a@example.invalid"]
    original.start.assert_called_once_with()
    assert made[0][0]["email"] == "a@example.invalid"
    assert (
        getattr(made[0][1].config, "gmail_refresh_token" if source == "gmail" else "refresh_token")
        == "refresh"
    )
    assert (await fleet.reload_accounts())["unchanged"] == ["a@example.invalid"]
    assert fleet._loops["a@example.invalid"] is original
    original.is_running = False
    await fleet.reload_accounts()
    assert (fleet._loops["a@example.invalid"] is original) is (source != "calendar")
    selected.clear()  # a revoked account no longer qualifies for this discovery
    assert (await fleet.reload_accounts())["removed"] == ["a@example.invalid"]
    assert fleet._loops == {}
    import asyncio
    import signal

    callbacks = []
    event_loop = asyncio.get_running_loop()
    monkeypatch.setattr(
        event_loop, "add_signal_handler", lambda sig, callback: callbacks.append((sig, callback))
    )
    monkeypatch.setattr(event_loop, "remove_signal_handler", lambda sig: None)
    fleet._main_loop = event_loop
    fleet._setup_sighup()
    assert callbacks[0][0] == signal.SIGHUP
    fleet._running = True
    task = asyncio.create_task(fleet._run_rescan_loop())
    selected[:] = [dict(email="signal@example.invalid", granted_scopes=[scope], metadata={})]
    changed = asyncio.Event()
    original_sync = fleet._sync_accounts

    async def observe_sync():
        result = await original_sync()
        changed.set()
        return result

    monkeypatch.setattr(fleet, "_sync_accounts", observe_sync)
    callbacks[0][1]()
    await asyncio.wait_for(changed.wait(), timeout=1)
    assert "signal@example.invalid" in fleet._loops
    selected.clear()
    changed.clear()
    callbacks[0][1]()
    await asyncio.wait_for(changed.wait(), timeout=1)
    assert not fleet._loops
    await fleet.stop()
    await task
    assert not fleet._running


@pytest.mark.parametrize("ending", ["cancel", "startup_error", "normal"])
async def test_google_fleet_owns_reload_signal_and_cleanup_even_before_ready(monkeypatch, ending):
    import asyncio
    import signal
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    from butlers.connectors.google_fleet import GoogleAccountFleet

    class Fleet(GoogleAccountFleet):
        rescan_interval = 0.001

        def _start_health_server(self):
            self._health_server = SimpleNamespace(should_exit=False)

        async def _discover_accounts(self):
            return {}

    fleet = Fleet()
    fleet._close_source = AsyncMock()
    event_loop = asyncio.get_running_loop()
    registered = []
    monkeypatch.setattr(
        event_loop, "add_signal_handler", lambda sig, callback: registered.append((sig, callback))
    )
    remove = MagicMock()
    monkeypatch.setattr(event_loop, "remove_signal_handler", remove)
    if ending == "startup_error":
        fleet._close_source.side_effect = RuntimeError("synthetic cleanup failure")

        async def fail():
            raise RuntimeError("synthetic startup failure")

        fleet._fleet_ready = fail
        with pytest.raises(RuntimeError, match="synthetic startup failure"):
            await fleet.start()
    else:
        task = asyncio.create_task(fleet.start())
        await asyncio.sleep(0.01)  # actual timer runs without network or account bodies
        assert registered[0][0] == signal.SIGHUP
        registered[0][1]()
        await asyncio.sleep(0)
        if ending == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            fleet._running = False
            fleet._reload_event.set()
            await task
    assert fleet._health_server.should_exit
    fleet._close_source.assert_awaited_once_with()
    remove.assert_called_once_with(signal.SIGHUP)
    assert not fleet._running
