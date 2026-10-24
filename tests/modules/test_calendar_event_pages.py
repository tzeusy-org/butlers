"""Actual Calendar scan registration and bounded provider transport controls."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import pytest
from fastmcp import FastMCP

from butlers.modules.calendar import (
    CalendarConfig,
    CalendarModule,
    CalendarProvider,
    _GoogleOAuthCredentials,
    _GoogleProvider,
)

pytestmark = pytest.mark.unit


async def test_registered_scan_without_authorized_target_is_incomplete():
    module = CalendarModule()
    mcp = FastMCP("calendar-scan-test")
    await module.register_tools(mcp, {"provider": "google"}, None, "travel")
    result = (
        await mcp.call_tool(
            "calendar_scan_reminder_events",
            {
                "start_at": "2026-10-01T00:00:00Z",
                "end_at": "2026-10-02T00:00:00Z",
                "marker": "document-expiry:test",
            },
        )
    ).structured_content
    assert result["complete"] is False
    assert result["presence"] == "unknown"
    assert result["reason"] == "target_unavailable"


_START = datetime(2026, 10, 1, tzinfo=UTC)
_END = datetime(2026, 10, 1, 0, 30, tzinfo=UTC)
_ARGS = {
    "start_at": _START.isoformat(),
    "end_at": _END.isoformat(),
    "marker": "document-expiry:test",
}


def _event(id="match", **overrides):
    return {
        "id": id,
        "start": {"dateTime": _START.isoformat()},
        "end": {"dateTime": _END.isoformat()},
        "description": "document-expiry:test",
        **overrides,
    }


async def _google(handler):
    module = CalendarModule()
    mcp = FastMCP("calendar-pages")
    await module.register_tools(mcp, {"provider": "google"}, None, "travel")
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = _GoogleProvider(
        CalendarConfig(provider="google"),
        _GoogleOAuthCredentials(
            client_id="fixture", client_secret="fixture", refresh_token="fixture"
        ),
        client,
    )
    provider._oauth.get_access_token = AsyncMock(return_value="fixture")
    module._provider = provider
    module._resolved_calendar_id = "create-target"
    module._primary_calendar_id = "primary-target"
    module._default_target_calendar_id = "primary-target"
    module._all_provider_calendar_ids = ["create-target", "primary-target"]
    module._provider_calendar_discovery_completed = True
    return module, mcp, client, provider


async def _call(mcp, **overrides):
    return (
        await mcp.call_tool("calendar_scan_reminder_events", {**_ARGS, **overrides})
    ).structured_content


async def test_real_streamed_second_page_uses_creation_target_and_exact_marker_interval():
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.params.get("pageToken") is None:
            return httpx.Response(
                200,
                json={
                    "items": [_event("wrong", description="prefix document-expiry:test")],
                    "nextPageToken": "second",
                },
            )
        return httpx.Response(200, json={"items": [_event()]})

    module, mcp, client, provider = await _google(handler)
    async with client:
        result = await _call(mcp)
    assert result["complete"] and result["presence"] == "present"
    assert result["matches"] == [{"event_id": "match"}] and result["examined"] == 2
    assert result["calendar_id"] == "create-target"
    assert module._resolve_calendar_id(None) == "primary-target"  # old read default unchanged
    assert module._resolve_calendar_id(None, for_create=True) == result["calendar_id"]
    assert len(requests) == 2
    assert all(r.headers["Accept-Encoding"] == "identity" for r in requests)
    assert all(r.url.path.endswith("/calendars/create-target/events") for r in requests)
    assert requests[0].url.params["timeMin"] == requests[1].url.params["timeMin"]
    assert requests[0].url.params["timeMax"] == requests[1].url.params["timeMax"]
    assert provider._oauth.get_access_token.await_count == 2


@pytest.mark.parametrize(
    "page,expected",
    [
        ({"items": [], "nextPageToken": "again"}, "scan_unavailable"),
        ({"items": "malformed"}, "scan_unavailable"),
        ({"items": [{"id": "missing-boundaries"}]}, "scan_unavailable"),
        ({"items": [], "nextPageToken": 3}, "scan_unavailable"),
    ],
)
async def test_cursor_and_malformed_pages_never_attest_absence(page, expected):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=page)

    _, mcp, client, _ = await _google(handler)
    async with client:
        result = await _call(mcp)
    assert not result["complete"] and result["presence"] == "unknown"
    assert result["reason"] == expected and len(calls) <= 2


@pytest.mark.parametrize("fragmented", [False, True])
async def test_preparse_byte_bound_closes_one_chunk_and_fragmented_stream(fragmented):
    class Body(httpx.AsyncByteStream):
        closed = False
        consumed = 0

        async def __aiter__(self):
            payload = b'{"items":[],"padding":"' + b"x" * 262145 + b'"}'
            chunks = (
                [payload]
                if not fragmented
                else [payload[i : i + 131072] for i in range(0, len(payload), 131072)]
            )
            for chunk in chunks:
                self.consumed += 1
                yield chunk

        async def aclose(self):
            self.closed = True

    body = Body()
    _, mcp, client, _ = await _google(lambda request: httpx.Response(200, stream=body))
    async with client:
        result = await _call(mcp)
    assert not result["complete"] and result["presence"] == "unknown"
    assert body.closed and body.consumed == (1 if not fragmented else 3)

    import gzip

    class Compressed(httpx.AsyncByteStream):
        consumed = 0
        closed = False

        async def __aiter__(self):
            self.consumed += 1
            yield gzip.compress(b'{"items":[]}')

        async def aclose(self):
            self.closed = True

    compressed = Compressed()
    _, mcp, client, _ = await _google(
        lambda request: httpx.Response(
            200,
            headers={"Content-Encoding": "gzip"},
            stream=compressed,
        )
    )
    async with client:
        result = await _call(mcp)
    assert not result["complete"] and result["presence"] == "unknown"
    assert compressed.closed and compressed.consumed == 0


async def test_clean_terminal_zero_is_absent_but_page_cap_preserves_known_presence():
    import json

    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        if request.url.params.get("pageToken") is None:
            return httpx.Response(200, json={"items": [_event("match")], "nextPageToken": "1"})
        return httpx.Response(200, json={"items": [], "nextPageToken": str(calls)})

    _, mcp, client, _ = await _google(handler)
    async with client:
        result = await _call(mcp)
    assert not result["complete"] and result["presence"] == "present" and calls == 20
    assert result["reason"] == "page_limit"
    assert len(json.dumps(result).encode()) < 65536
    _, mcp, client, _ = await _google(lambda request: httpx.Response(200, json={"items": []}))
    async with client:
        result = await _call(mcp)
    assert result["complete"] and result["presence"] == "absent"


async def test_wrong_target_never_sends_http_and_401_refresh_stays_inside_same_scan():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(401 if len(calls) == 1 else 200, json={"items": []})

    _, mcp, client, provider = await _google(handler)
    async with client:
        refused = await _call(mcp, calendar_id="unrelated")
        assert not refused["complete"] and not calls
        result = await _call(mcp, calendar_id="primary-target")
    assert result["complete"] and result["calendar_id"] == "primary-target"
    assert len(calls) == 2 and all("primary-target" in r.url.path for r in calls)
    assert [c.kwargs for c in provider._oauth.get_access_token.await_args_list] == [
        {"force_refresh": False},
        {"force_refresh": True},
    ]


async def test_aggregate_deadline_is_shared_and_cancellation_closes_stream():
    import asyncio

    started = asyncio.Event()

    class Body(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            started.set()
            await asyncio.Event().wait()
            yield b"unreachable"

        async def aclose(self):
            self.closed = True

    body = Body()
    _, mcp, client, _ = await _google(lambda request: httpx.Response(200, stream=body))
    async with client:
        task = asyncio.create_task(_call(mcp))
        await asyncio.wait_for(started.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert body.closed
    # Unsupported is concrete and nonabstract: old subclass implementations
    # acquire no new mandatory method or synthetic first-page completeness.
    assert "_reminder_event_page" not in CalendarProvider.__abstractmethods__
    with pytest.raises(NotImplementedError):
        await CalendarProvider._reminder_event_page(
            object(), calendar_id="x", start_at=_START, end_at=_END, page_token=None, deadline=0
        )


async def test_one_absolute_twenty_second_deadline_covers_token_and_all_pages():
    import asyncio
    import time

    requests = 0
    deadlines = []

    async def handler(request):
        nonlocal requests
        requests += 1
        if requests == 1:
            return httpx.Response(200, json={"items": [], "nextPageToken": "two"})
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    _, mcp, client, provider = await _google(handler)
    original = provider._reminder_event_page

    async def observe(**kwargs):
        deadlines.append(kwargs["deadline"])
        return await original(**kwargs)

    provider._reminder_event_page = observe
    started = time.monotonic()
    async with client:
        result = await _call(mcp)
    elapsed = time.monotonic() - started
    assert requests == 2 and len(deadlines) == 2 and deadlines[0] == deadlines[1]
    assert 19 <= elapsed < 24
    assert result["reason"] == "timeout" and not result["complete"]
    assert result["presence"] == "unknown"
