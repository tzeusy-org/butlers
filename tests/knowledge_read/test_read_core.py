"""The shared read contract: closed arguments, bound cursors, envelopes and the size cap."""

from __future__ import annotations

import base64
import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

from butlers.knowledge_read.core import (
    MAX_RESPONSE_BYTES,
    Arguments,
    ReadContext,
    ReadError,
    decode_cursor,
    encode_cursor,
    fit,
    published,
    record_uri,
    span,
)

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)


def _context(**changes) -> ReadContext:
    base = ReadContext(
        sub="github|1", as_of=NOW - timedelta(seconds=5), now=NOW, cursor_key=b"k" * 32
    )
    return replace(base, **changes)


def _code(call) -> str:
    with pytest.raises(ReadError) as caught:
        call()
    return caught.value.code


def test_unknown_or_malformed_arguments_refuse_without_echo():
    assert _code(lambda: Arguments({"name": "x", "sql": "drop"}, {"name"})) == "invalid_argument"
    assert _code(lambda: Arguments(["name"], {"name"})) == "invalid_argument"
    args = Arguments({"name": "x" * 129}, {"name"})
    error = pytest.raises(ReadError, args.text, "name", max_length=128, required=True).value
    assert "x" * 10 not in json.dumps(error.to_dict())
    assert error.to_dict()["error"] == {
        "code": "invalid_argument",
        "domain": None,
        "retryable": False,
        "retry_after_s": None,
        "message": "An argument is missing, malformed or out of range.",
    }


def test_text_is_nfc_normalized_and_typed_values_are_strict():
    args = Arguments({"name": "Café ", "limit": True, "id": "nope"}, {"name", "limit", "id"})
    assert args.text("name", max_length=10) == "Café"
    assert _code(lambda: args.integer("limit", low=1, high=5, default=2)) == "invalid_argument"
    assert _code(lambda: args.uuid("id")) == "invalid_argument"
    days = Arguments({"start": "2026-01-01", "end": "2026-01-01T00:00:00"}, {"start", "end"})
    assert days.day("start") == date(2026, 1, 1)
    assert _code(lambda: days.day("end")) == "invalid_argument"
    # A naive date-time is ambiguous and refuses; a bare date is UTC midnight.
    times = Arguments({"a": "2026-01-01", "b": "2026-01-01T09:00:00"}, {"a", "b"})
    assert times.instant("a") == datetime(2026, 1, 1, tzinfo=UTC)
    assert _code(lambda: times.instant("b")) == "invalid_argument"


def test_span_limits_and_inversion():
    span(date(2026, 1, 1), date(2026, 4, 3), max_days=92)
    assert _code(lambda: span(date(2026, 1, 1), date(2026, 4, 4), max_days=92)) == (
        "invalid_argument"
    )
    assert _code(lambda: span(date(2026, 1, 2), date(2026, 1, 1), max_days=92)) == (
        "invalid_argument"
    )


def test_cursor_round_trips_and_is_bound_to_sub_tool_args_key_and_time():
    context = _context()
    values = {"start": "2026-01-01", "limit": 10}
    token = encode_cursor(context, "finance_ledger", values, ["2026-01-01T00:00:00+00:00", "a"])
    assert decode_cursor(context, "finance_ledger", values, token) == [
        "2026-01-01T00:00:00+00:00",
        "a",
    ]
    assert decode_cursor(context, "finance_ledger", values, None) is None
    refusals = [
        lambda: decode_cursor(_context(sub="github|2"), "finance_ledger", values, token),
        lambda: decode_cursor(context, "timeline", values, token),
        lambda: decode_cursor(context, "finance_ledger", {**values, "limit": 11}, token),
        # A restart generates a new process key.
        lambda: decode_cursor(_context(cursor_key=b"r" * 32), "finance_ledger", values, token),
        lambda: decode_cursor(
            _context(now=NOW + timedelta(minutes=16)), "finance_ledger", values, token
        ),
        lambda: decode_cursor(context, "finance_ledger", values, token[:-2] + "AA"),
        lambda: decode_cursor(context, "finance_ledger", values, "not a cursor"),
    ]
    for refusal in refusals:
        assert _code(refusal) == "invalid_argument"
    # The token is opaque to the caller: it carries no sub.
    raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    assert b"github" not in raw


def test_freshness_degraded_and_domain_refusal_precedence():
    assert _context().freshness == "fresh"
    lagging = _context(as_of=NOW - timedelta(minutes=6), disabled_domains=frozenset({"travel"}))
    assert lagging.freshness == "lagging"
    assert lagging.degraded(("time", "travel")) == [
        {"domain": "travel", "reason": "disabled"},
        {"domain": "time", "reason": "lagging"},
    ]
    assert _code(lambda: lagging.require("travel")) == "domain_disabled"
    stale = _context(as_of=None, disabled_domains=frozenset({"travel"}))
    # D17: projection_stale outranks domain_disabled.
    assert _code(lambda: stale.require("travel")) == "projection_stale"
    assert _code(lambda: _context(as_of=NOW - timedelta(minutes=16)).require("time")) == (
        "projection_stale"
    )


def test_sources_come_from_the_manifest_uri_and_tool_assignment():
    table, view = published("public.entities", "entity_lookup")
    assert view == "mcp_read.public__entities"
    assert record_uri(table, "e1") == ("entity", "butlers://relationship/entity/e1")
    with pytest.raises(LookupError):
        published("finance.transactions", "entity_lookup")
    with pytest.raises(LookupError):
        published("relationship.entity_info", "entity_profile")
    assert _context().source("fact", "butlers://relationship/fact/x") == {
        "kind": "fact",
        "uri": "butlers://relationship/fact/x",
        "as_of": (NOW - timedelta(seconds=5)).isoformat(),
        "content_authority": None,
    }


def test_fit_cuts_trailing_items_and_marks_truncation():
    items = [{"text": "x" * 1000} for _ in range(400)]
    small = fit(items[:3], lambda kept, cut: {"items": kept})
    assert "truncated" not in small and len(small["items"]) == 3
    cut = fit(items, lambda kept, cut: {"items": kept, "cut": cut})
    assert cut["truncated"] is True and cut["cut"] is True
    assert 0 < len(cut["items"]) < 400
    assert len(json.dumps(cut, separators=(",", ":")).encode()) <= MAX_RESPONSE_BYTES


def test_read_library_imports_only_stdlib_asyncpg_and_itself():
    """RFC 0039 D1: no renderer, migration, memory search, graph tool, API or model import."""
    import ast
    import sys
    from importlib.resources import files

    allowed = {"asyncpg", "pydantic", "butlers.knowledge_read"}
    for path in files("butlers.knowledge_read").iterdir():
        if not path.name.endswith(".py"):
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module or ""]
            for name in names:
                top = name.split(".")[0]
                assert (
                    top in sys.stdlib_module_names
                    or top in allowed
                    or name.startswith("butlers.knowledge_read")
                ), f"{path.name} imports {name}"
