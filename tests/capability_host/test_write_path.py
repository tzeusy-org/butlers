"""The host's one write path: ``propose_fact`` only (REQ-public-mcp-proposals-001, -003, -007).

A tool marked ``writes`` runs in an ordinary read-write transaction, with the host's status read,
kill-switch layers, budget, canary and audit still applied inside it. Every other tool keeps the
read-only repeatable-read snapshot. Fake projection; the native PG17 file proves it on real roles.
"""

from __future__ import annotations

import pytest

from butlers.capability_host import CapabilityHost, Tool
from butlers.knowledge_read.core import Arguments, ReadError
from tests.capability_host.helpers import CID, SUB, Env, default_fake_tools, error_code

pytestmark = pytest.mark.unit


def _writer(seen: list):
    async def write(conn, context, arguments):
        Arguments(arguments, {"q"}).text("q", max_length=8)
        context.require("proposals")
        await conn.execute("INSERT inbox")  # the argument-validation pass stops here
        seen.append((context.sub, context.cid))
        return {"proposal_id": "p", "status": "received"}

    return write


def _env(tmp_path, seen: list) -> Env:
    tools = default_fake_tools()
    tools["propose_fact"] = Tool("propose_fact", "proposals", _writer(seen), dlp=False, writes=True)
    return Env(tmp_path, tools=tools)


def _begins(env: Env) -> list[str]:
    return [q for q in env.projection.queries if q.startswith("BEGIN")]


async def test_a_write_tool_runs_in_a_read_write_transaction_and_others_in_a_snapshot(tmp_path):
    seen: list = []
    env = _env(tmp_path, seen)
    assert error_code(await env.tool("propose_fact", {"q": "x"})) is None
    assert _begins(env)[-1] == "BEGIN []"  # no readonly, no isolation override
    assert "INSERT inbox" in env.projection.queries
    assert error_code(await env.tool("entity_lookup")) is None
    assert _begins(env)[-1] == "BEGIN [('isolation', 'repeatable_read'), ('readonly', True)]"


async def test_the_write_sees_sub_and_cid_from_the_assertion_only(tmp_path):
    seen: list = []
    env = _env(tmp_path, seen)
    await env.tool("propose_fact", {"q": "x"})
    assert seen == [(SUB, CID)]
    # Arguments cannot carry them: the closed parse refuses before any database work.
    assert error_code(await env.tool("propose_fact", {"cid": "evil"})) == "invalid_argument"
    assert len(seen) == 1


@pytest.mark.parametrize("switch", ["proposals", "all"])
async def test_a_disabled_domain_refuses_before_any_write(tmp_path, switch):
    seen: list = []
    env = _env(tmp_path, seen)
    env.projection.disabled = [switch]
    assert error_code(await env.tool("propose_fact", {"q": "x"})) == "domain_disabled"
    assert seen == [] and "INSERT inbox" not in env.projection.queries


async def test_the_break_glass_file_refuses_before_any_write(tmp_path):
    seen: list = []
    env = _env(tmp_path, seen)
    env.write("switches/disabled_domains", "proposals\n")
    env.mono.now += 10
    assert error_code(await env.tool("propose_fact", {"q": "x"})) == "domain_disabled"
    assert seen == []


async def test_a_write_failure_is_unavailable_and_audited_once(tmp_path):
    seen: list = []
    env = _env(tmp_path, seen)
    env.projection.fail_once = None

    async def broken(conn, context, arguments):
        raise RuntimeError("boom")

    env.tools["propose_fact"] = Tool("propose_fact", "proposals", broken, dlp=False, writes=True)
    env.restart()
    assert error_code(await env.tool("propose_fact")) == "unavailable"
    assert [a["tool"] for a in env.audits()] == ["propose_fact"]
    assert env.audits()[0]["outcome"] == "unavailable"


async def test_a_reader_refusal_passes_through_the_write_transaction(tmp_path):
    async def full(conn, context, arguments):
        raise ReadError("unavailable")

    env = _env(tmp_path, [])
    env.tools["propose_fact"] = Tool("propose_fact", "proposals", full, dlp=False, writes=True)
    env.restart()
    message = await env.tool("propose_fact")
    assert message["result"]["structuredContent"]["error"]["retryable"] is True


def test_no_other_tool_may_write(tmp_path):
    tools = default_fake_tools()
    tools["finance_ledger"] = Tool("finance_ledger", "finance", _writer([]), writes=True)
    env = Env(tmp_path, tools=default_fake_tools())
    with pytest.raises(ValueError, match="only propose_fact may write"):
        CapabilityHost(env.settings, tools=tools)


def test_no_default_or_served_tool_but_propose_fact_writes():
    from butlers.capability_host import default_tools
    from butlers.mcp_public.server import served_tools

    assert not any(t.writes for t in default_tools().values())
    assert [n for n, t in served_tools().items() if t.writes] == ["propose_fact"]
