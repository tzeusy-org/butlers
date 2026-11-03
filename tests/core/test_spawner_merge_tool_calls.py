"""Regression tests for tool-call name aliasing in `_merge_tool_call_records`.

Context: parsers and the server-side capture report the same underlying MCP
tool under three different name forms:

- bare: ``memory_entity_resolve`` (server-side capture in ``tool_call_capture``)
- opencode-prefixed: ``{butler_name}_memory_entity_resolve`` (opencode parser)
- claude-code/codex prefixed: ``mcp__{butler_name}__memory_entity_resolve``
  (claude_code / codex parsers)
- gemini-prefixed: ``mcp_{butler_name}_memory_entity_resolve`` (gemini parser)
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


@pytest.mark.parametrize("butler_name", ["lifestyle", "home_ops"])
def test_merge_dedupes_prefixed_and_bare_names_against_capture(butler_name):
    """mcp__{butler}__x, mcp_{butler}_x, {butler}_x, and bare x collapse to one entry.

    ``home_ops`` pins that an underscore in the butler name never splits the
    Gemini prefix in the wrong place.
    """

    # Parser-side records (what claude_code / codex / opencode emit)
    queries = ["alice", "bob", "carol"]
    parsed = [
        {
            "id": f"t{i}",
            "name": f"{prefix}memory_entity_resolve",
            "input": {"query": query},
        }
        for i, (prefix, query) in enumerate(
            zip(
                [f"mcp__{butler_name}__", f"{butler_name}_", f"mcp_{butler_name}_"],
                queries,
                strict=True,
            ),
            start=1,
        )
    ]
    # Capture-side records (server-side _SpanWrappingMCP.tool uses fn.__name__)
    executed = [
        {
            "name": "memory_entity_resolve",
            "input": {"query": query},
            "outcome": "success",
            "result": {"entity_id": f"e-{query}"},
        }
        for query in queries
    ]

    merged = _merge_tool_call_records(parsed, executed, butler_name=butler_name)

    # Exactly N entries, not 2N — one per real invocation, each paired
    assert [(m["id"], m["name"], m["result"]["entity_id"]) for m in merged] == [
        ("t1", "memory_entity_resolve", "e-alice"),
        ("t2", "memory_entity_resolve", "e-bob"),
        ("t3", "memory_entity_resolve", "e-carol"),
    ]


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


def test_merge_keeps_stream_order_between_mcp_and_builtin_calls():
    """A subagent alternating Bash with one MCP call stays interleaved, not a loop.

    Capture only sees MCP calls; built-ins exist only on the parser side. Merging
    must keep the parser's stream order so the per-thread loop guard does not
    read the MCP calls as consecutive. A capture record without a parser
    counterpart lands before the next paired call that executed after it.
    """
    from butlers.core.spawner_guardrails import (
        _DEGENERATE_TOOL_LOOP_CONSECUTIVE_THRESHOLD,
        _check_degenerate_tool_loop,
    )

    rounds = _DEGENERATE_TOOL_LOOP_CONSECUTIVE_THRESHOLD
    parsed: list[dict] = []
    for i in range(rounds):
        parsed.append(
            {
                "id": f"bash{i}",
                "name": "Bash",
                "input": {"command": f"ls {i}"},
                "builtin": True,
                "parent_tool_use_id": "agent1",
            }
        )
        parsed.append(
            {
                "id": f"mcp{i}",
                "name": "mcp__lifestyle__state_get",
                "input": {"key": "k"},
                "parent_tool_use_id": "agent1",
            }
        )
    executed = [{"name": "state_get", "input": {"key": "k"}, "outcome": "success"}] * rounds
    executed = [{"name": "notify", "input": {"msg": "hi"}, "outcome": "success"}, *executed]

    merged = _merge_tool_call_records(parsed, executed, butler_name="lifestyle")

    assert [call.get("id", call["name"]) for call in merged] == [
        "bash0",
        "notify",
        "mcp0",
        *[f"{kind}{i}" for i in range(1, rounds) for kind in ("bash", "mcp")],
    ]
    assert all(c["outcome"] == "success" for c in merged if c["name"] == "state_get")
    assert _check_degenerate_tool_loop(merged) is None
