"""Regression tests for tool-call name aliasing in `_merge_tool_call_records`.

Context: parsers and the server-side capture report the same underlying MCP
tool under three different name forms:

- bare: ``memory_entity_resolve`` (server-side capture in ``tool_call_capture``)
- opencode-prefixed: ``{butler_name}_memory_entity_resolve`` (opencode parser)
- claude-code/codex prefixed: ``mcp__{butler_name}__memory_entity_resolve``
  (claude_code / codex parsers)
- endpoint-prefixed: ``mcp__http://127.0.0.1:.../mcp__memory_entity_resolve``
  (runtime builds that echo the MCP endpoint as the server identity)

Without normalization the merge-by-(name, payload) signature never matches
across those forms, so each invocation is persisted multiple times in
``sessions.tool_calls``. These tests lock in normalization behavior.
"""

from __future__ import annotations

import pytest

from butlers.core.spawner import _merge_tool_call_records
from butlers.core.tool_call_capture import fingerprint_tool_call_payload

pytestmark = pytest.mark.unit


def test_merge_dedupes_prefixed_and_bare_names_against_capture():
    """mcp__{butler}__x, {butler}_x, and bare x all collapse to one entry."""
    butler_name = "lifestyle"

    # Parser-side records (what claude_code / codex / opencode emit)
    parsed = [
        {
            "id": "t1",
            "name": "mcp__lifestyle__memory_entity_resolve",
            "input": {"query": "alice"},
        },
        {
            "id": "t2",
            "name": "lifestyle_memory_entity_resolve",
            "input": {"query": "bob"},
        },
    ]
    # Capture-side records (server-side _SpanWrappingMCP.tool uses fn.__name__)
    executed = [
        {
            "name": "memory_entity_resolve",
            "input": {"query": "alice"},
            "outcome": "success",
            "result": {"entity_id": "e-alice"},
        },
        {
            "name": "memory_entity_resolve",
            "input": {"query": "bob"},
            "outcome": "success",
            "result": {"entity_id": "e-bob"},
        },
    ]

    merged = _merge_tool_call_records(parsed, executed, butler_name=butler_name)

    # Exactly N entries, not 2N or 3N — one per real invocation
    assert len(merged) == 2

    # Canonical stored name is the bare (fn.__name__) form
    names = [m["name"] for m in merged]
    assert names == ["memory_entity_resolve", "memory_entity_resolve"]

    # Parser metadata (ids) preserved
    ids = sorted(m.get("id", "") for m in merged)
    assert ids == ["t1", "t2"]

    # Capture metadata (outcome/result) preserved
    for m in merged:
        assert m["outcome"] == "success"
        assert m["result"]["entity_id"] in {"e-alice", "e-bob"}


def test_merge_handles_mixed_prefixed_forms_without_capture():
    """When only parser records exist, names still normalize to bare form.

    Parsers for different runtimes in the same session (rare, but possible during
    runtime switches) could emit both prefix styles. Normalization should collapse
    them so downstream consumers see one canonical entry per invocation.
    """
    butler_name = "lifestyle"

    parsed = [
        {
            "id": "t1",
            "name": "mcp__lifestyle__memory_entity_resolve",
            "input": {"query": "alice"},
        },
        {
            "id": "t1",  # same id -> lifecycle duplicate, last wins
            "name": "lifestyle_memory_entity_resolve",
            "input": {"query": "alice"},
        },
    ]

    merged = _merge_tool_call_records(parsed, [], butler_name=butler_name)
    # id-based dedup collapses the lifecycle duplicates; normalization ensures
    # the canonical stored form is the bare tool name.
    assert len(merged) == 1
    assert merged[0]["name"] == "memory_entity_resolve"


def test_merge_preserves_non_matching_names():
    """Names that don't carry the butler prefix pass through unchanged."""
    butler_name = "lifestyle"

    parsed = [
        {"name": "route_to_butler", "input": {"butler": "relationship"}},
    ]
    executed = [
        {"name": "route_to_butler", "input": {"butler": "relationship"}},
    ]
    merged = _merge_tool_call_records(parsed, executed, butler_name=butler_name)
    assert len(merged) == 1
    assert merged[0]["name"] == "route_to_butler"


def test_merge_preserves_bare_name_as_canonical_when_prefixed_only():
    """When a record has only a prefixed name (no capture counterpart),
    normalization still records the bare name form in the final entry so
    downstream consumers see consistent tool names across sessions.
    """
    butler_name = "lifestyle"

    parsed = [
        {
            "id": "t1",
            "name": "mcp__lifestyle__memory_entity_resolve",
            "input": {"query": "alice"},
        },
    ]
    merged = _merge_tool_call_records(parsed, [], butler_name=butler_name)
    assert len(merged) == 1
    # The stored name is the bare form.
    assert merged[0]["name"] == "memory_entity_resolve"


def test_merge_dedupes_endpoint_prefixed_mcp_names_against_capture():
    """Endpoint-derived MCP aliases collapse against server-side bare names.

    Some runtimes can report the remote MCP endpoint identity in the
    ``mcp__<server>__<tool>`` slot.  That alias is environment-specific and must
    not prevent parser records from merging with capture-side records.
    """
    parsed = [
        {
            "id": "t1",
            "name": (
                "mcp__http://127.0.0.1:41109/mcp?"
                "runtime_session_id=session-1__memory_entity_resolve"
            ),
            "input": {"query": "alice"},
        },
    ]
    executed = [
        {
            "name": "memory_entity_resolve",
            "input": {"query": "alice"},
            "outcome": "success",
        },
    ]

    merged = _merge_tool_call_records(parsed, executed, butler_name="lifestyle")

    assert len(merged) == 1
    assert merged[0]["id"] == "t1"
    assert merged[0]["name"] == "memory_entity_resolve"
    assert merged[0]["outcome"] == "success"


def test_merge_normalizes_endpoint_prefixed_mcp_names_without_capture():
    """Parser-only endpoint-prefixed MCP records are stored under the bare tool name."""
    parsed = [
        {
            "id": "t1",
            "name": "mcp__lifestyle.internal__spotify_search",
            "input": {"query": "jazz"},
        },
    ]

    merged = _merge_tool_call_records(parsed, [], butler_name="lifestyle")

    assert len(merged) == 1
    assert merged[0]["name"] == "spotify_search"


def test_merge_matches_raw_parser_payload_to_capture_fingerprint():
    """Redacted capture input still merges with parser records via full-input fingerprint."""
    parsed = [
        {
            "id": "t1",
            "name": "mcp__relationship__contact_resolve",
            "input": {"name": "Person A"},
        },
        {
            "id": "t2",
            "name": "mcp__relationship__contact_resolve",
            "input": {"name": "Person B"},
        },
    ]
    executed = [
        {
            "name": "contact_resolve",
            "input_fingerprint": fingerprint_tool_call_payload({"name": "Person A"}),
            "outcome": "success",
        },
        {
            "name": "contact_resolve",
            "input_fingerprint": fingerprint_tool_call_payload({"name": "Person B"}),
            "outcome": "success",
        },
    ]

    merged = _merge_tool_call_records(parsed, executed, butler_name="relationship")

    assert len(merged) == 2
    assert [call["id"] for call in merged] == ["t1", "t2"]
    assert [call["name"] for call in merged] == ["contact_resolve", "contact_resolve"]
    assert all(call["outcome"] == "success" for call in merged)
