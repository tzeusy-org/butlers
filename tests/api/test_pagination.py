"""The one dashboard keyset cursor codec (``butlers.api.pagination``)."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from uuid import UUID

import pytest

from butlers.api.pagination import (
    CURSOR_VERSION,
    InvalidCursorError,
    _checksum,
    decode_cursor,
    encode_cursor,
)
from butlers.pagination import InvalidCursorError as CoreInvalidCursorError
from butlers.pagination import decode_cursor as core_decode_cursor
from butlers.pagination import encode_cursor as core_encode_cursor

pytestmark = pytest.mark.unit

_TS = datetime(2026, 8, 1, 12, 30, 15, 250000, tzinfo=UTC)
_ID = UUID("00000000-0000-4000-8000-000000000001")
_GOOD = encode_cursor((_TS, _ID), scope="test-receiver")


def _resign(body: object) -> str:
    """A token for ``body`` with a valid checksum, so only its content is wrong."""
    raw = json.dumps(body, separators=(",", ":")).encode()
    token = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return f"{token}.{_checksum(raw)}"


@pytest.mark.parametrize(
    "values",
    [(_TS, _ID), (0.125, _TS, str(_ID)), (40,), ("text", None, True), (0,), (100,)],
    ids=[
        "datetime-uuid",
        "float-datetime-str",
        "offset",
        "scalars",
        "offset-zero",
        "offset-hundred",
    ],
)
def test_cursor_round_trips_values_with_their_types(values):
    token = encode_cursor(values, scope="test-receiver")

    assert all(c.isalnum() or c in "-_." for c in token)
    assert core_encode_cursor(values, scope="test-receiver") == token
    assert core_decode_cursor(token, scope="test-receiver") == values
    assert decode_cursor(token, scope="test-receiver") == values
    assert decode_cursor(token, tuple(type(v) for v in values), scope="test-receiver") == values


@pytest.mark.parametrize(
    "bad",
    [
        "not-a-cursor",
        "",
        _GOOD[:-2],
        _GOOD.replace(".", "x.", 1),
        _resign({"v": CURSOR_VERSION + 1, "s": "test-receiver", "k": []}),
        _resign({"v": CURSOR_VERSION, "s": "test-receiver", "k": [["t", "not-a-date"]]}),
        _resign({"v": CURSOR_VERSION, "s": "test-receiver", "k": [["q", "x"]]}),
        _resign({"v": CURSOR_VERSION, "s": "test-receiver"}),
        _resign([]),
        _GOOD.replace(".", "!!!!.", 1),
        _GOOD.replace(".", "    .", 1),
        _GOOD.replace(".", "=.", 1),
        _GOOD + ".",
        _resign({"v": CURSOR_VERSION, "s": "other-receiver", "k": []}),
    ],
    ids=[
        "garbage",
        "empty",
        "truncated-checksum",
        "edited-body",
        "other-version",
        "bad-datetime",
        "unknown-tag",
        "no-keys",
        "non-object-body",
        "ignored-punctuation",
        "ignored-whitespace",
        "padded-spelling",
        "extra-separator",
        "foreign-receiver",
    ],
)
def test_cursor_rejects_tampering_with_a_400(bad):
    with pytest.raises(InvalidCursorError) as caught:
        decode_cursor(bad, scope="test-receiver")

    assert caught.value.status_code == 400
    assert isinstance(caught.value, ValueError)
    with pytest.raises(CoreInvalidCursorError) as core_caught:
        core_decode_cursor(bad, scope="test-receiver")
    assert isinstance(core_caught.value, ValueError)
    assert not hasattr(core_caught.value, "status_code")


def test_cursor_from_another_endpoint_fails_the_shape_check():
    with pytest.raises(InvalidCursorError):
        decode_cursor(_GOOD, (float, datetime, str), scope="test-receiver")
    with pytest.raises(InvalidCursorError):
        decode_cursor(_GOOD, (datetime, str), scope="test-receiver")
    with pytest.raises(CoreInvalidCursorError):
        core_decode_cursor(_GOOD, (float, datetime, str), scope="test-receiver")
    with pytest.raises(CoreInvalidCursorError):
        core_decode_cursor(_GOOD, (datetime, str), scope="test-receiver")


@pytest.mark.parametrize(
    ("value", "shape", "accepted"),
    [
        (True, int, False),
        (False, int, False),
        (1, bool, False),
        (1, float, False),
        (0, int, True),
        (40, int, True),
        (100, int, True),
        (0.125, float, True),
        (True, bool, True),
        (None, str | None, True),
        ("UTC", str | None, True),
    ],
)
def test_cursor_shapes_preserve_exact_scalar_and_union_types(value, shape, accepted):
    token = encode_cursor((value,), scope="test-receiver")
    for decoder, error in (
        (decode_cursor, InvalidCursorError),
        (core_decode_cursor, CoreInvalidCursorError),
    ):
        if accepted:
            result = decoder(token, (shape,), scope="test-receiver")
            assert result == (value,)
            assert type(result[0]) is type(value)
        else:
            with pytest.raises(error):
                decoder(token, (shape,), scope="test-receiver")


# Route table: which envelope each paginated GET route returns. A new route must use
# Page (cursor) or PaginatedResponse (offset); these are the only ones that predate it.
_PAGE_FIELDS = {"items", "next_cursor", "limit"}
_CURSOR_PARAMS = {"cursor", "before"}
_OFFSET_PARAMS = {"offset", "page"}

#: Cursor routes that do not return Page, and why. Drop an entry when its route converts.
_CURSOR_ENVELOPE_EXCEPTIONS = {
    "/api/sessions": "KeysetResponse; retires at the RFC 0038 D19 switch",
    "/api/timeline": "TimelineResponse; sibling heartbeat and degraded fields not yet on a Page",
    "/api/calendar/workspace": "ApiResponse payload; moves with the calendar workspace refactor",
    "/api/calendar/workspace/invitations": "ApiResponse payload; same calendar refactor",
    "/api/qa/cases/{case_id}/journal": "tail-follow by event timestamp, not a keyset position",
}

#: Offset routes whose envelope is not PaginatedResponse. Drop an entry when it converts.
_OFFSET_ENVELOPE_EXCEPTIONS = frozenset(
    {
        "/api/attention/ledger",
        "/api/butlers/{name}/notifications",
        "/api/calendar/workspace/audit",
        "/api/home/devices",
        "/api/notifications",
        "/api/relationship/entities",
        "/api/relationship/entities/queue",
        "/api/relationship/entities/{entity_id}/activity",
        "/api/relationship/entities/{entity_id}/gifts",
        "/api/relationship/entities/{entity_id}/interactions",
        "/api/relationship/entities/{entity_id}/loans",
        "/api/relationship/entities/{entity_id}/notes",
        "/api/relationship/entities/{entity_id}/timeline",
        "/api/relationship/groups",
        "/api/system/conditions",
    }
)


def _paginated_get_routes() -> dict[str, tuple[str, str, set[str]]]:
    """``path -> (kind, response schema name, response schema fields)`` for paginated GETs."""
    from butlers.api.app import create_app

    spec = create_app().openapi()
    routes = {}
    for path, operations in spec["paths"].items():
        operation = operations.get("get")
        if operation is None:
            continue
        params = {p["name"] for p in operation.get("parameters", []) if p["in"] == "query"}
        if not params & {"limit", "page_size"}:
            continue
        kind = "cursor" if params & _CURSOR_PARAMS else "offset" if params & _OFFSET_PARAMS else ""
        if not kind:
            continue
        schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
        name = schema.get("$ref", "").rsplit("/", 1)[-1]
        fields = set(spec["components"]["schemas"].get(name, {}).get("properties", {}))
        routes[path] = (kind, name, fields)
    return routes


def test_every_paginated_route_uses_page_or_a_listed_exception():
    routes = _paginated_get_routes()

    cursor_not_page = {
        path
        for path, (kind, _name, fields) in routes.items()
        if kind == "cursor" and not _PAGE_FIELDS <= fields
    }
    offset_not_paginated = {
        path
        for path, (kind, name, _fields) in routes.items()
        if kind == "offset" and not name.startswith("PaginatedResponse_")
    }

    assert cursor_not_page == set(_CURSOR_ENVELOPE_EXCEPTIONS)
    assert offset_not_paginated == _OFFSET_ENVELOPE_EXCEPTIONS
