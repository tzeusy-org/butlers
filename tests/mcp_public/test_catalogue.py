"""The pinned D10 catalogue and the declared capabilities (REQ-public-mcp-surface-001, -002).

``PINNED`` is the SHA-256 of each tool's whole ``tools/list`` entry (name, title, description,
``inputSchema``, ``outputSchema`` and ``annotations``) as sorted compact JSON. Any change to a
tool's contract fails here and must be re-pinned in review, together with RFC 0039 D10.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import cast

import asyncpg
import pytest
from jsonschema import Draft202012Validator
from mcp.types import LATEST_PROTOCOL_VERSION, ListToolsResult

from butlers.capability_host import default_tools
from butlers.knowledge_read.core import ReadContext, ReadError
from butlers.mcp_public.catalogue import (
    STATIC_HANDLERS,
    catalogue,
    initialize,
    tools_list,
    tools_list_result,
)
from butlers.mcp_public.server import served_tools

pytestmark = pytest.mark.unit

PINNED = {
    "search": "7cb8c3a7a36eed5ec5d62012260914ed0515a505b71df8121e00789e72cc90af",
    "fetch": "e30d7f7ff04e4eccebaa6e83d9c4ee0c377f86113cd701812b06b762b9de64e3",
    "search_filtered": "037c6f096a4b6a41cddd62688342139993e8b8168cbb8c4ee580790275ccb7c2",
    "entity_lookup": "36a3b03c6e5904130b2f11342951ec930b1a8c1388533ea7e93429a89d7f00a3",
    "entity_profile": "0de5996f8960b52c9525b342d5d76e21c21e35fa4435c65f890eda27774331a3",
    "graph_neighbors": "c4d336946cb9e313bb4da207a5a2dee7c9c0e0f9dce1a96d6995910971a031a2",
    "graph_path": "ebdd94c7cb9dba367c7e9caeed6933c4dbc3cf4975ce0d3d3e3e9d4f7a974dc4",
    "timeline": "759b0e6faa9d22b7d5310699dd9301fea6263c8585006e566ce82af15195c195",
    "time_use": "37edcb1d466996c13e1033b70b96c0565942c2f000d5b9b0c013581417d3130d",
    "trips": "abf582111ffb69b79b1a22e578f49b6b5a0ed8b4ac0f06f08d1b655270b13d31",
    "describe": "2dbb3cc3ad2b9fe9c80c330cc41d15ecfd0770c9fa746b539569e93e4d99df9a",
    "finance_summary": "ffa978634add914e14e9d40092d9ec3d950cf6bc5cc81a7e53befaf33b2e1ca8",
    "finance_ledger": "b68e04602f0e1b1cedb4f15a0f46a775fd9c060940ede36c8980450f361ac323",
    "finance_obligations": "34cd1851404d20fba00064754837bb8de8a5dcbd526f5182b4ad9d23d365e42b",
    "finance_records": "7a3a712a00641f4f8109c0c100e50f4c6172dffedbaee36143657b080e54e86f",
    "health_series": "524a4c12a6524214b45829cdb8b9233f1c40ac09d68e0654daf9a2189ecf2f50",
    "health_profile": "acd2bf516151a9fa678109e878ecfa66690c9953df0fd5aa134ce5096e4fa96f",
    "health_records": "326ce4e81dcbc5c9c267edc80454807c16dab637b3b08d96d6deaa0773a7e0a4",
    "propose_fact": "31e5c63eda2cde1da3ff06c93446bf7b0b516827747d2d169f9c969486aa1e58",
}

#: REQ-public-mcp-surface-002, in RFC 0039 D10 table order.
D10 = (
    "search",
    "fetch",
    "search_filtered",
    "entity_lookup",
    "entity_profile",
    "graph_neighbors",
    "graph_path",
    "timeline",
    "time_use",
    "trips",
    "describe",
    "finance_summary",
    "finance_ledger",
    "finance_obligations",
    "finance_records",
    "health_series",
    "health_profile",
    "health_records",
    "propose_fact",
)


def _entries() -> list[dict]:
    return json.loads(tools_list_result())["tools"]


def _digest(entry: dict) -> str:
    return hashlib.sha256(
        json.dumps(entry, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def test_the_catalogue_is_exactly_the_d10_table_in_order():
    assert tuple(e["name"] for e in _entries()) == D10
    assert not {"query", "sql", "invoke"} & set(D10)


def test_the_catalogue_matches_the_pinned_digests():
    actual = {e["name"]: _digest(e) for e in _entries()}
    changed = sorted(n for n in PINNED if actual.get(n) != PINNED[n])
    assert actual.keys() == PINNED.keys() and changed == [], f"re-pin after review: {changed}"


def test_every_tool_carries_the_d10_annotations():
    for entry in _entries():
        assert entry["annotations"] == {
            "title": entry["title"],
            "readOnlyHint": entry["name"] != "propose_fact",
            "destructiveHint": False,
            "idempotentHint": entry["name"] != "propose_fact",
            "openWorldHint": False,
        }, entry["name"]


def test_every_schema_is_a_valid_closed_object_schema():
    for entry in _entries():
        for key in ("inputSchema", "outputSchema"):
            Draft202012Validator.check_schema(entry[key])
            assert entry[key]["type"] == "object", (entry["name"], key)
        assert entry["inputSchema"]["additionalProperties"] is False, entry["name"]


def test_tools_list_is_one_static_valid_mcp_result():
    first = tools_list({})
    assert tools_list({"cursor": "x"}) == first
    assert ListToolsResult.model_validate(first).tools == list(catalogue())
    assert STATIC_HANDLERS["tools/list"] is tools_list


def test_initialize_declares_exactly_tools():
    result = initialize({"protocolVersion": "2025-06-18", "capabilities": {"sampling": {}}})
    assert result["capabilities"] == {"tools": {"listChanged": False}}
    assert result["protocolVersion"] == "2025-06-18"
    assert result["serverInfo"]["name"] == "butlers-public-mcp"
    assert set(result) == {"protocolVersion", "capabilities", "serverInfo"}


@pytest.mark.parametrize("requested", ["1999-01-01", None, 7])
def test_initialize_offers_the_latest_version_for_an_unsupported_one(requested):
    assert initialize({"protocolVersion": requested})["protocolVersion"] == (
        LATEST_PROTOCOL_VERSION
    )


def test_server_discover_is_not_answered_until_the_gateway_probe_is_cross_checked():
    assert set(STATIC_HANDLERS) == {"initialize", "tools/list"}


class _NoDatabase:
    def __getattr__(self, name: str):
        raise LookupError("argument parsing passed")


async def test_every_advertised_argument_is_one_the_reader_accepts():
    """Each schema property is known to its reader (an unknown name would be refused first)."""
    now = datetime.now(UTC)
    context = ReadContext(sub="1", as_of=now, now=now, cursor_key=b"k" * 32)
    readers = served_tools()
    for entry in _entries():
        schema = entry["inputSchema"]
        arguments = {name: None for name in schema["properties"]}
        try:
            await readers[entry["name"]].read(
                cast(asyncpg.Connection, _NoDatabase()), context, arguments
            )
        except ReadError as refused:
            assert refused.argument is None or refused.argument in schema["required"], (
                entry["name"],
                refused.argument,
            )
        except LookupError:
            pass
        assert set(schema["required"]) <= set(schema["properties"]), entry["name"]


def test_the_served_tools_are_the_catalogue():
    assert set(D10) - set(default_tools()) == {"propose_fact"}
    assert set(served_tools()) == set(D10)


async def test_propose_fact_refuses_unavailable_after_parsing_its_arguments():
    now = datetime.now(UTC)
    context = ReadContext(sub="1", as_of=now, now=now, cursor_key=b"k" * 32)
    read = served_tools()["propose_fact"].read
    conn = cast(asyncpg.Connection, _NoDatabase())
    good = {
        "subject_entity_id": "00000000-0000-0000-0000-000000000001",
        "predicate": "knows",
        "object_entity_id": "00000000-0000-0000-0000-000000000002",
        "idempotency_key": "k1",
    }
    with pytest.raises(ReadError) as refused:
        await read(conn, context, good)
    assert (refused.value.code, refused.value.domain) == ("unavailable", "proposals")
    for bad in (
        {**good, "predicate": "parent-of"},
        {**good, "sql": "x"},
        {**good, "note": "n" * 501},
    ):
        with pytest.raises(ReadError) as refused:
            await read(conn, context, bad)
        assert refused.value.code == "invalid_argument"
