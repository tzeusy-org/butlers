"""Calendar revision journal: row mapping, fail-closed writes and the connector's commit point.

No database: the write path is exercised against a fake pool, and the connector is driven over a
fake Calendar API. The real-Postgres behaviour is in tests/migrations/test_core_366_*.
Synthetic ids and text only (bu-uhbhzq.1 slice 2).
"""

from __future__ import annotations

import json
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.connectors import calendar_event_revisions as revisions
from butlers.connectors.evidence_journal import EvidenceNotCommitted
from butlers.connectors.google_calendar import (
    CalendarAccountConfig,
    CalendarConnectorRuntime,
    _parse_dt,
)

_ENDPOINT = "google_calendar:user:synthetic@example.test"


def _event(**extra: Any) -> dict[str, Any]:
    return {
        "id": "e1",
        "etag": '"3000"',
        "status": "confirmed",
        "summary": "Synthetic title",
        "location": "Synthetic Room",
        "description": "Notes. https://meet.example.test/x Passcode: 4455 PIN 1234#",
        "hangoutLink": "https://meet.example.test/join",
        "conferenceData": {
            "conferenceSolution": {"name": "Synthetic Meet"},
            "entryPoints": [
                {"entryPointType": "video", "uri": "https://meet.example.test/join", "pin": "9"}
            ],
        },
        "start": {"dateTime": "2026-11-24T14:00:00Z"},
        "end": {"dateTime": "2026-11-24T15:00:00Z"},
        "updated": "2026-11-23T01:00:00.000Z",
        "sequence": 2,
        "attendees": [{"email": "guest@example.test"}],
        **extra,
    }


def _row(event: dict[str, Any], **kwargs: Any) -> tuple[Any, ...]:
    return revisions.revision_row(
        event,
        endpoint_identity=_ENDPOINT,
        calendar_id="primary",
        observation=kwargs.pop("observation", "change"),
        policy_blocked=kwargs.pop("policy_blocked", False),
        parse=_parse_dt,
    )


def test_title_location_and_attendees_are_verbatim_but_links_and_passcodes_are_not() -> None:
    import json

    raw = json.loads(_row(_event())[-1])
    assert raw["summary"] == "Synthetic title" and raw["location"] == "Synthetic Room"
    assert raw["attendees"] == [{"email": "guest@example.test"}]
    for secret in ("meet.example.test", "4455", "1234", "hangoutLink"):
        assert secret not in json.dumps(raw)
    assert raw["conferenceData"]["conferenceSolution"] == {"name": "Synthetic Meet"}


def test_a_policy_blocked_event_keeps_structure_and_times_but_no_raw() -> None:
    row = _row(_event(), policy_blocked=True)
    assert row[-1] is None and row[-2] is True
    assert row[9] is not None and row[10] is not None  # starts_at, ends_at


def test_all_day_status_sequence_and_original_start_are_structured() -> None:
    row = _row(
        _event(
            start={"date": "2026-11-24"},
            end={"date": "2026-11-25"},
            originalStartTime={"dateTime": "2026-11-24T14:00:00Z"},
            recurringEventId="series-1",
        )
    )
    assert row[3] == "series-1" and row[4] is not None
    assert row[7] == 2 and row[8] == "confirmed" and row[11] is True


def test_a_missing_etag_gets_a_stable_content_hash_never_an_empty_key() -> None:
    event = _event()
    del event["etag"]
    first, second = _row(event)[5], _row(event)[5]
    assert first == second and first.startswith("sha256:")


def test_nul_is_stripped_from_the_stored_text() -> None:
    import json

    raw = json.loads(_row(_event(summary="Syn\x00thetic"))[-1])
    assert raw["summary"] == "Synthetic"


def test_an_unknown_observation_is_refused() -> None:
    with pytest.raises(ValueError):
        _row(_event(), observation="guess")


class _Conn:
    def __init__(self, fail: bool = False) -> None:
        self.fail, self.rows = fail, []

    def transaction(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def executemany(self, sql: str, rows: list[tuple]) -> None:
        if self.fail:
            raise RuntimeError("Synthetic private title in the failure text")
        assert "ON CONFLICT (endpoint_identity, calendar_id, event_id, etag) DO NOTHING" in sql
        self.rows.extend(rows)


class _Pool:
    def __init__(self, conn: _Conn) -> None:
        self.conn = conn

    def acquire(self):
        return self.conn


async def _record(pool: Any, events: list[dict[str, Any]], observation="change") -> int:
    return await revisions.record_calendar_revisions(
        pool,
        endpoint_identity=_ENDPOINT,
        calendar_id="primary",
        events=events,
        observation=observation,
        parse=_parse_dt,
    )


async def test_a_batch_is_written_in_one_transaction() -> None:
    conn = _Conn()
    assert await _record(_Pool(conn), [_event(), _event(id="e2")]) == 2
    assert len(conn.rows) == 2


async def test_no_pool_fails_closed() -> None:
    with pytest.raises(EvidenceNotCommitted) as raised:
        await _record(None, [_event()])
    assert raised.value.reason == "no_pool"


async def test_a_database_failure_raises_with_the_class_only() -> None:
    with pytest.raises(EvidenceNotCommitted) as raised:
        await _record(_Pool(_Conn(fail=True)), [_event()])
    assert raised.value.reason == "RuntimeError"
    assert "Synthetic private title" not in str(raised.value)
    assert raised.value.__cause__ is None


async def test_a_scrub_failure_commits_nothing_and_never_stores_raw_text() -> None:
    conn = _Conn()
    with (
        patch.object(revisions, "scrub_evidence_json", side_effect=ValueError("boom")),
        pytest.raises(EvidenceNotCommitted),
    ):
        await _record(_Pool(conn), [_event()])
    assert conn.rows == []


# -- the connector's commit point ------------------------------------------------------------


def _runtime(calls: list[tuple[str, int]]) -> CalendarConnectorRuntime:
    runtime = CalendarConnectorRuntime(
        CalendarAccountConfig(
            email="synthetic@example.test",
            client_id="c",
            client_secret="s",
            refresh_token="r",
            switchboard_mcp_url="http://localhost:41100/sse",
        )
    )
    runtime._running = True
    runtime._cursor_pool = AsyncMock()
    return runtime


async def test_the_baseline_is_journaled_before_the_token_is_saved_and_nothing_is_routed() -> None:
    order: list[str] = []
    runtime = _runtime([])

    async def api_get(path: str, params: dict[str, Any]) -> dict[str, Any]:
        return {"items": [_event()], "nextSyncToken": "t1"}

    async def record(*args: Any, **kwargs: Any) -> int:
        order.append(f"journal:{kwargs['observation']}")
        return 1

    async def save(token: str) -> None:
        order.append("token")

    runtime._gcal_api_get = api_get  # type: ignore[method-assign]
    runtime._save_sync_token = save  # type: ignore[method-assign]
    runtime._process_event = AsyncMock()  # type: ignore[method-assign]
    with patch("butlers.connectors.google_calendar.record_calendar_revisions", record):
        await runtime._perform_full_sync(ingest_events=False)

    assert order == ["journal:baseline", "token"]
    runtime._process_event.assert_not_awaited()


async def test_a_failed_journal_write_holds_the_token_and_degrades_health_then_recovers() -> None:
    runtime = _runtime([])
    runtime._sync_token = "t1"
    runtime._record_source_api_success()
    saved: list[str] = []

    async def save(token: str) -> None:
        saved.append(token)

    runtime._save_sync_token = save  # type: ignore[method-assign]
    runtime._process_event = AsyncMock(return_value=True)  # type: ignore[method-assign]
    runtime._incremental_sync = AsyncMock(  # type: ignore[method-assign]
        return_value=([_event()], "t2")
    )

    with patch(
        "butlers.connectors.google_calendar.record_calendar_revisions",
        AsyncMock(side_effect=EvidenceNotCommitted("RuntimeError")),
    ):
        await runtime._run_one_poll_cycle()
    assert saved == [] and runtime._sync_token == "t1"
    runtime._process_event.assert_not_awaited()
    state, reason = runtime._get_health_state()
    assert state == "degraded" and "journal" in (reason or "")

    with patch("butlers.connectors.google_calendar.record_calendar_revisions", AsyncMock()):
        await runtime._run_one_poll_cycle()
    assert saved == ["t2"] and runtime._get_health_state() == ("healthy", None)


async def test_a_410_recovery_is_journaled_as_a_resync() -> None:
    from butlers.connectors.google_calendar import _SyncTokenExpiredError

    runtime = _runtime([])
    runtime._sync_token = "stale"
    seen: list[str] = []

    async def record(*args: Any, **kwargs: Any) -> int:
        seen.append(kwargs["observation"])
        return 1

    async def api_get(path: str, params: dict[str, Any]) -> dict[str, Any]:
        return {"items": [_event()], "nextSyncToken": "fresh"}

    runtime._incremental_sync = AsyncMock(side_effect=_SyncTokenExpiredError())  # type: ignore[method-assign]
    runtime._gcal_api_get = api_get  # type: ignore[method-assign]
    runtime._process_event = AsyncMock(return_value=True)  # type: ignore[method-assign]
    runtime._save_sync_token = AsyncMock()  # type: ignore[method-assign]
    with patch("butlers.connectors.google_calendar.record_calendar_revisions", record):
        await runtime._run_one_poll_cycle()
    assert seen == ["resync"]


async def test_the_policy_flag_reaches_the_journal() -> None:
    runtime = _runtime([])
    blocked: list[bool] = []

    async def record(*args: Any, **kwargs: Any) -> int:
        blocked.append(kwargs["blocked"](_event()))
        return 1

    from types import SimpleNamespace

    cast(Any, runtime)._ingestion_policy = SimpleNamespace(
        evaluate=lambda _env: SimpleNamespace(allowed=False, action="skip")
    )
    with patch("butlers.connectors.google_calendar.record_calendar_revisions", record):
        assert await runtime._journal_events([_event()], "change")
    assert blocked == [True]


async def test_known_global_block_still_redacts_when_local_policy_raises() -> None:
    from types import SimpleNamespace

    runtime = _runtime([])
    conn = _Conn()
    cast(Any, runtime)._cursor_pool = _Pool(conn)
    local = SimpleNamespace(evaluate=MagicMock(side_effect=ValueError("synthetic policy failure")))
    global_ = SimpleNamespace(evaluate=MagicMock(return_value=SimpleNamespace(action="skip")))
    with (
        patch.object(runtime, "_ingestion_policy", local),
        patch.object(runtime, "_global_ingestion_policy", global_),
    ):
        assert await runtime._journal_events([_event()], "change")

    assert len(conn.rows) == 1
    assert conn.rows[0][-2:] == (True, None)
    global_.evaluate.assert_called_once()


def test_a_blocked_fallback_version_key_depends_only_on_retained_structure() -> None:
    base = _event()
    base.pop("etag")
    first = _row({**base, "summary": "synthetic detail A"}, policy_blocked=True)
    changed_private = _row({**base, "summary": "synthetic detail B"}, policy_blocked=True)
    changed_structure = _row({**base, "sequence": 42}, policy_blocked=True)

    assert first[-1] is None and changed_private[-1] is None
    assert first[5] == changed_private[5]
    assert first[5] != changed_structure[5]


async def test_unresolved_policy_error_holds_before_the_journal_write() -> None:
    from types import SimpleNamespace

    runtime = _runtime([])
    runtime._record_source_api_success()
    conn = _Conn()
    cast(Any, runtime)._cursor_pool = _Pool(conn)
    local = SimpleNamespace(evaluate=MagicMock(side_effect=ValueError("synthetic policy failure")))
    global_ = SimpleNamespace(evaluate=MagicMock(return_value=None))
    with (
        patch.object(runtime, "_ingestion_policy", local),
        patch.object(runtime, "_global_ingestion_policy", global_),
    ):
        assert not await runtime._journal_events([_event()], "change")

    assert conn.rows == []
    global_.evaluate.assert_called_once()
    assert runtime._get_health_state()[0] == "degraded"


def test_conference_data_keeps_only_the_solution_name_and_key_type() -> None:
    event = _event(
        conferenceData={
            "conferenceId": "abc-defg-hij",
            "conferenceSolution": {
                "name": "Synthetic Meet",
                "key": {"type": "hangoutsMeet"},
                "iconUri": "https://icons.example.test/m.png",
            },
            "notes": "Dial +1 555 0100, code 998877",
            "parameters": {"addOnParameters": {"parameters": {"room": "secret-room"}}},
            "entryPoints": [{"uri": "https://meet.example.test/join", "pin": "9"}],
        }
    )
    raw = json.loads(_row(event)[-1])
    assert raw["conferenceData"] == {
        "conferenceSolution": {"name": "Synthetic Meet", "key": {"type": "hangoutsMeet"}}
    }
    text = json.dumps(raw)
    for secret in ("abc-defg-hij", "998877", "secret-room", "iconUri", "555 0100"):
        assert secret not in text


def test_attachment_source_and_gadget_links_are_dropped() -> None:
    event = _event(
        attachments=[
            {
                "title": "Synthetic plan.pdf",
                "mimeType": "application/pdf",
                "fileUrl": "https://drive.example.test/file/1",
                "fileId": "1",
                "iconLink": "https://icons.example.test/pdf.png",
            }
        ],
        source={"title": "Synthetic source", "url": "https://source.example.test/p"},
        gadget={"title": "Synthetic gadget", "link": "https://g.example.test/x", "width": 5},
    )
    raw = json.loads(_row(event)[-1])
    assert raw["attachments"] == [{"title": "Synthetic plan.pdf", "mimeType": "application/pdf"}]
    assert raw["source"] == {"title": "Synthetic source"}
    assert raw["gadget"] == {"title": "Synthetic gadget", "width": 5}
    assert "example.test/" not in json.dumps(raw).replace("guest@example.test", "")


@pytest.mark.parametrize(
    "link",
    [
        "zoom.us/j/123456789?pwd=abc",
        "us02web.zoom.us/j/123456789",
        "teams.microsoft.com/l/meetup-join/19%3a",
        "meet.google.com/abc-defg-hij",
        "company.webex.com/meet/someone",
        "webex.com/join",
    ],
)
def test_bare_meeting_hosts_are_scrubbed_from_descriptions(link: str) -> None:
    scrubbed = revisions.scrub_description(f"Agenda. Join at {link} thanks")
    assert link.split("/")[0] not in scrubbed and "[removed]" in scrubbed
    assert scrubbed.startswith("Agenda. Join at ") and scrubbed.endswith(" thanks")
