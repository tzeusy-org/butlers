"""Canaries and latch, budgets, audit and cursors.

REQ-public-mcp-authorization-011, -012, -013 and REQ-public-mcp-surface-012.
"""

from __future__ import annotations

import json

import pytest

from butlers.capability_host import Tool
from butlers.capability_host.config import BudgetLimits
from butlers.capability_host.dlp import find_canary
from butlers.knowledge_read.core import decode_cursor, encode_cursor
from tests.capability_host.helpers import (
    BEARER,
    CANARY,
    CID2,
    Env,
    call_body,
    claims,
    default_fake_tools,
    error_code,
    fake_reader,
    item,
    sign,
)

pytestmark = pytest.mark.unit


def _env_with(tmp_path, name: str, domain: str | None, result, **kw) -> Env:
    tools = default_fake_tools()
    tools[name] = Tool(name, domain, fake_reader(domain, result))
    return Env(tmp_path, tools=tools, **kw)


# -- REQ-011 canaries ---------------------------------------------------------------------------


async def test_a_canary_in_a_response_latches_the_surface_off(tmp_path):
    env = _env_with(tmp_path, "trips", "travel", {"items": [item("travel", 1, f"x {CANARY}")]})
    message = await env.tool("trips")
    assert error_code(message) == "blocked" and CANARY not in json.dumps(message)
    for name in ("entity_lookup", "finance_ledger", "trips"):
        assert error_code(await env.tool(name)) == "blocked"
    assert error_code(await env.tool("describe")) is None
    assert env.host.metrics.get("canary_hits") == 1
    assert env.host.metrics.get("canary_latched") == 1
    assert "butlers_public_mcp_canary_latched 1" in env.host.metrics.render()
    env.restart()
    assert error_code(await env.tool("entity_lookup")) is None  # a restart clears the latch


def test_canaries_are_found_through_normalization_and_in_keys():
    hidden = CANARY[:5] + "​" + CANARY[5:]
    assert find_canary({"a": [{"b": hidden}]}, (CANARY,))
    assert find_canary({CANARY: 1}, (CANARY,))
    assert find_canary({"a": "".join(chr(ord(c) + 0xFEE0) for c in "ABCDEFGH")}, ("ABCDEFGH",))
    assert not find_canary({"a": "clean"}, (CANARY,))


async def test_the_canary_check_runs_before_dlp_could_hide_it(tmp_path):
    token = "ghp_" + "Z9y8X7w6V5" * 4
    env = _env_with(tmp_path, "trips", "travel", {"items": [item("travel", 1, token)]})
    env.write("canaries/canaries.json", json.dumps([token]))
    env.reload()
    assert error_code(await env.tool("trips")) == "blocked" and env.host.latched


# -- REQ-012 budgets ----------------------------------------------------------------------------


async def test_per_client_rate_is_enforced(env: Env):
    for _ in range(60):
        assert error_code(await env.tool("entity_lookup")) is None
    message = await env.tool("entity_lookup")
    error = message["result"]["structuredContent"]["error"]
    assert error["code"] == "budget_exhausted" and error["retryable"] is True
    assert error["retry_after_s"] >= 1
    env.clock.now += 2
    assert error_code(await env.tool("entity_lookup")) is None  # the bucket refills


async def test_heavy_tools_have_a_per_tool_rate(env: Env):
    for _ in range(20):
        assert error_code(await env.tool("finance_ledger")) is None
    assert error_code(await env.tool("finance_ledger")) == "budget_exhausted"
    assert error_code(await env.tool("entity_lookup")) is None


async def test_daily_row_budget_caps_bulk_export(tmp_path):
    rows = {"rows": [item("finance", i) for i in range(500)]}
    env = _env_with(tmp_path, "finance_ledger", "finance", rows)
    for _ in range(40):
        assert error_code(await env.tool("finance_ledger")) is None
        env.clock.now += 3  # stay under the per-minute rate
    message = await env.tool("finance_ledger")
    error = message["result"]["structuredContent"]["error"]
    assert error["code"] == "budget_exhausted"
    assert 0 < error["retry_after_s"] <= 86_400
    assert error_code(await env.tool("entity_lookup")) is None
    # One client cannot spend another's budget.
    assert error_code(await env.tool("finance_ledger", cid=CID2)) is None
    env.clock.now += error["retry_after_s"]
    assert error_code(await env.tool("finance_ledger")) is None  # the day rolled over


async def test_proposal_budget_is_enforced_before_the_inbox(env: Env):
    for _ in range(20):
        assert error_code(await env.tool("propose_fact")) is None
        env.clock.now += 3
    before = len(env.calls)
    assert error_code(await env.tool("propose_fact")) == "budget_exhausted"
    assert len(env.calls) == before


async def test_the_server_wide_rate_is_enforced(tmp_path):
    env = Env(tmp_path, budgets=BudgetLimits(server_per_minute=3))
    for _ in range(3):
        assert error_code(await env.tool("entity_lookup")) is None
    assert error_code(await env.tool("entity_lookup", cid=CID2)) == "budget_exhausted"


async def test_a_refused_budget_spends_nothing(tmp_path):
    env = Env(tmp_path, budgets=BudgetLimits(heavy_per_minute=1))
    assert error_code(await env.tool("finance_ledger")) is None
    for _ in range(5):
        assert error_code(await env.tool("finance_ledger")) == "budget_exhausted"
    for _ in range(59):
        assert error_code(await env.tool("entity_lookup")) is None


# -- REQ-013 audit ------------------------------------------------------------------------------


async def test_a_served_call_is_audited(tmp_path):
    facts = {"entity": {"entity_id": "e1"}, "facts": [item("relationship", i) for i in range(12)]}
    env = _env_with(tmp_path, "entity_profile", "relationship", facts)
    await env.tool("entity_profile", {"q": "Avery"})
    (line,) = env.audits()
    assert line["tool"] == "entity_profile" and line["outcome"] == "ok" and line["rows"] == 12
    assert line["ids"][0].startswith("butlers://relationship/fact/row-")
    assert len(line["ids"]) == 12 and len(line["args_sha256"]) == 64
    assert line["bytes"] > 0 and line["latency_ms"] >= 0 and line["jti"]
    assert set(line) == {
        "event", "request_id", "jti", "sub", "cid", "tool", "args_sha256", "outcome",
        "cause", "rows", "bytes", "redactions", "latency_ms", "ids",
    }  # fmt: skip


async def test_secrets_never_reach_the_log(env: Env):
    body = call_body("entity_lookup", {"q": "secret-query-text", "cursor": "cursor-value-xyz"})
    token = sign(claims(body))
    await env.send(body, assertion=token)
    text = "\n".join(env.lines) + env.host.metrics.render()
    for secret in (BEARER, token, "secret-query-text", "cursor-value-xyz", "plain"):
        assert secret not in text


async def test_refusals_are_audited_with_their_cause(env: Env):
    body = call_body("entity_lookup")
    await env.send(
        body, assertion=sign(claims(body, iat=env.clock.now - 40, exp=env.clock.now - 10))
    )
    (line,) = env.audits()
    assert line["outcome"] == "unauthorized" and line["cause"] == "expired"
    assert line["rows"] == 0 and line["ids"] == [] and line["tool"] is None


async def test_bearer_refusals_are_counted_without_content(env: Env):
    await env.send(call_body("entity_lookup"), bearer="presented-wrong-bearer")
    assert env.host.metrics.get("bearer_refusals") == 1 and env.lines == []
    assert "presented-wrong-bearer" not in env.host.metrics.render()


async def test_tool_errors_are_audited_with_their_code(env: Env):
    env.projection.disabled = ["finance"]
    await env.tool("finance_ledger")
    assert env.audits()[-1]["outcome"] == "domain_disabled"


# -- REQ-surface-012 cursors --------------------------------------------------------------------


def _paging_tools():
    rows = [f"r{i}" for i in range(5)]

    def page(context, args):
        position = decode_cursor(context, "trips", args.values, args.cursor())
        start = 0 if position is None else position[0]
        kept = rows[start : start + 2]
        more = start + 2 < len(rows)
        cursor = encode_cursor(context, "trips", args.values, [start + 2]) if more else None
        return {"items": [{"id": r} for r in kept], "next_cursor": cursor}

    tools = default_fake_tools()
    tools["trips"] = Tool("trips", "travel", fake_reader("travel", page))
    return tools


async def _page(env: Env, cursor=None, **kw):
    arguments = {"q": "x"} | ({"cursor": cursor} if cursor else {})
    return (await env.tool("trips", arguments, **kw))["result"]["structuredContent"]


async def test_a_cursor_pages_without_overlap_or_gap(tmp_path):
    env = Env(tmp_path, tools=_paging_tools())
    seen, cursor = [], None
    while True:
        page = await _page(env, cursor)
        seen += [i["id"] for i in page["items"]]
        if not (cursor := page["next_cursor"]):
            break
    assert seen == [f"r{i}" for i in range(5)]


async def test_a_cursor_is_bound_to_its_caller_and_arguments(tmp_path):
    env = Env(tmp_path, tools=_paging_tools())
    cursor = (await _page(env))["next_cursor"]
    other_sub = await env.tool("trips", {"q": "x", "cursor": cursor}, sub="5151")
    assert error_code(other_sub) == "invalid_argument"
    other_args = await env.tool("trips", {"q": "y", "cursor": cursor})
    assert error_code(other_args) == "invalid_argument"
    tampered = await env.tool("trips", {"q": "x", "cursor": cursor[:-3] + "AAA"})
    assert error_code(tampered) == "invalid_argument"


async def test_a_cursor_dies_with_the_process_and_after_15_minutes(tmp_path):
    env = Env(tmp_path, tools=_paging_tools())
    cursor = (await _page(env))["next_cursor"]
    env.clock.now += 15 * 60 + 1
    assert error_code(await env.tool("trips", {"q": "x", "cursor": cursor})) == "invalid_argument"
    env.clock.now -= 15 * 60 + 1
    env.restart()
    assert error_code(await env.tool("trips", {"q": "x", "cursor": cursor})) == "invalid_argument"


async def test_an_oversized_result_is_too_large(tmp_path):
    big = {"items": [item("travel", i, "y " * 1000) for i in range(200)]}
    env = _env_with(tmp_path, "trips", "travel", big)
    assert error_code(await env.tool("trips")) == "too_large"
