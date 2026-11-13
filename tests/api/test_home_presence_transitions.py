"""Owner-only presence timeline: /api/home/presence/transitions (bu-jv1moa.17).

Drives the production app and owner middleware through raw ASGI. The perception reader is
the one seam, replaced by a fake connection that returns canonical timeline rows.
"""

from __future__ import annotations

import json

import pytest

from butlers.api.app import create_app
from tests.api.auth_helpers import _DomainOwnerState

# ``seams`` is a shared fixture: owner-control and audit seams with a counting pool manager.
from tests.api.test_home_owner_places import (
    _OWNER_KEY,
    _mounted,
    _raw,
    seams,  # noqa: F401
)

_ROUTE = "/api/home/presence/transitions"
_OWNER = [(b"x-api-key", _OWNER_KEY.encode())]
_HOME = "33333333-3333-4333-8333-333333333333"


def _row(n, *, before, after, operation="assert", klass="transition", **extra):
    def value(side):
        if side is None:
            return None
        return {"presence": side, "place_ref": _HOME, "locality_ref": None}

    return {
        "id": f"00000000-0000-4000-8000-{n:012d}",
        "operation": operation,
        "class": klass,
        "from_value": value(before),
        "to_value": value(after),
        "at": f"2026-11-13T09:{n:02d}:00+00:00",
        "confirmed_at": f"2026-11-13T09:{n:02d}:30+00:00",
        "late": False,
        "retracted": False,
        "retraction_event_id": None,
        # Everything below must never reach the response.
        "evidence_refs": [{"ref": "secret-evidence"}],
        "labels": {"label_sens": ["location"]},
        "writer": "perception_dots_writer",
        **extra,
    }


class FakeReader:
    def __init__(self, rows):
        self.rows = rows
        self.closed = False
        self.calls = []

    async def fetchval(self, query, *args):
        self.calls.append(args)
        return self.rows

    async def close(self):
        self.closed = True


def _endpoint(app):
    pending = list(app.routes)
    while pending:
        route = pending.pop()
        pending.extend(getattr(route, "routes", ()) or ())
        original = getattr(route, "original_router", None)
        pending.extend(getattr(original, "routes", ()) or ())
        if getattr(route, "name", "") == "get_presence_transitions":
            return route.endpoint
    raise LookupError("presence transitions route not mounted")


def _app(seams, monkeypatch, reader):  # noqa: F811
    app = _mounted(seams.manager, create_app(api_key=_OWNER_KEY))
    app.state.owner_auth_service = _DomainOwnerState(_OWNER_KEY)

    async def open_reader():
        return reader

    monkeypatch.setitem(_endpoint(app).__globals__, "_open_presence_reader", open_reader)
    return app


@pytest.mark.unit
@pytest.mark.asyncio
async def test_owner_control_refuses_before_any_reader(seams, monkeypatch) -> None:  # noqa: F811
    reader = FakeReader([])
    app = _app(seams, monkeypatch, reader)
    for headers in ([], [(b"x-api-key", b"wrong-owner-key")]):
        status, response, _ = await _raw(app, "GET", _ROUTE, b"", headers)
        assert (status, response["error"]["code"]) == (401, "UNAUTHORIZED")
    assert reader.calls == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_unactivated_reader_is_a_fixed_503(seams, monkeypatch) -> None:  # noqa: F811
    monkeypatch.delenv("BUTLERS_PERCEPTION_READER_DSN", raising=False)
    app = _mounted(seams.manager, create_app(api_key=_OWNER_KEY))
    app.state.owner_auth_service = _DomainOwnerState(_OWNER_KEY)
    status, response, _ = await _raw(app, "GET", _ROUTE, b"", _OWNER)
    assert (status, response["error"]["code"]) == (503, "PRESENCE_TIMELINE_UNAVAILABLE")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_only_confirmed_edges_are_projected_with_late_and_retraction_flags(
    seams,  # noqa: F811
    monkeypatch,
) -> None:
    rows = [
        _row(5, before="outside", after="inside"),
        _row(4, before="inside", after="inside"),  # heartbeat
        _row(3, before=None, after="inside"),  # establishing, not an edge
        _row(
            2,
            before="inside",
            after="outside",
            retracted=True,
            retraction_event_id="00000000-0000-4000-8000-0000000000aa",
            late=True,
        ),
        _row(1, before=None, after=None, operation="retract", klass="record"),
    ]
    reader = FakeReader(rows)
    app = _app(seams, monkeypatch, reader)
    status, response, _ = await _raw(app, "GET", f"{_ROUTE}?limit=5", b"", _OWNER)
    assert status == 200 and reader.closed
    page = response["data"]
    assert [(t["direction"], t["late"], t["retracted"]) for t in page["transitions"]] == [
        ("enter", False, False),
        ("leave", True, True),
    ]
    assert page["next_before"] == rows[-1]["id"]
    text = json.dumps(response)
    for leaked in ("secret-evidence", "label_sens", "writer", "evidence_refs", "lat", "lon"):
        assert leaked not in text.replace("late", "")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_short_page_has_no_cursor_and_bad_limit_is_refused(seams, monkeypatch) -> None:  # noqa: F811
    app = _app(seams, monkeypatch, FakeReader([_row(1, before="inside", after="outside")]))
    status, response, _ = await _raw(app, "GET", f"{_ROUTE}?limit=20", b"", _OWNER)
    assert status == 200 and response["data"]["next_before"] is None
    status, _, _ = await _raw(app, "GET", f"{_ROUTE}?limit=101", b"", _OWNER)
    assert status == 422
