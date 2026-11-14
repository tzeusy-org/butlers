"""Kill switches, freshness, the applied digest and D17 precedence, over a fake projection.

REQ-public-mcp-authorization-009, REQ-public-mcp-surface-002/-013 and
REQ-public-mcp-projection-015. The native PG17 file proves the same against real views.
"""

from __future__ import annotations

import json
from datetime import timedelta

import asyncpg
import pytest

from butlers.capability_host.config import BudgetLimits, parse_disabled_domains
from butlers.capability_host.projection import ProjectionStatus, judge
from butlers.knowledge_read.core import DOMAINS
from butlers.knowledge_read.manifest import Manifest, ManifestError, decode_applied_manifest
from tests.capability_host.helpers import (
    MANIFEST,
    Env,
    Projection,
    default_fake_tools,
    error_code,
)

pytestmark = pytest.mark.unit

STALE = timedelta(minutes=16)
LAGGING = timedelta(minutes=5)


def _error(message: dict) -> dict:
    return message["result"]["structuredContent"]["error"]


def _narrowed() -> Manifest:
    """The shipped manifest without one published table, still a valid applied record."""
    for table in MANIFEST.tables:
        candidate = Manifest(tuple(t for t in MANIFEST.tables if t is not table))
        try:
            decode_applied_manifest(candidate.canonical)
        except ManifestError:
            continue
        return candidate
    raise AssertionError("no table can be dropped from the manifest")


# -- REQ-public-mcp-authorization-009 kill switches --


async def test_a_replicated_switch_refuses_its_domain_only(env: Env):
    env.projection.disabled = ["finance"]
    refused = await env.tool("finance_ledger")
    assert _error(refused) == {
        "code": "domain_disabled",
        "domain": "finance",
        "retryable": False,
        "retry_after_s": None,
        "message": "This data source is turned off by the owner.",
    }
    assert error_code(await env.tool("entity_lookup")) is None


async def test_the_all_switch_stops_every_tool_but_describe(env: Env):
    env.projection.disabled = ["all"]
    for name in ("entity_lookup", "finance_ledger", "health_series", "trips", "propose_fact"):
        assert error_code(await env.tool(name)) == "domain_disabled"
    described = await env.tool("describe")
    assert set(described["result"]["structuredContent"]["domains"]) == set(DOMAINS)


async def test_break_glass_works_while_replication_lags(env: Env):
    env.projection.as_of_age = LAGGING
    assert error_code(await env.tool("health_series")) is None
    env.write("switches/disabled_domains", "# incident\nhealth\n")
    env.mono.now += 9
    assert error_code(await env.tool("health_series")) is None  # not yet re-read
    env.mono.now += 1
    assert error_code(await env.tool("health_series")) == "domain_disabled"
    assert error_code(await env.tool("entity_lookup")) is None
    (env.root / "switches/disabled_domains").unlink()
    env.reload()
    assert error_code(await env.tool("health_series")) is None  # re-enabling is clearing


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (None, frozenset()),
        (b"", frozenset()),
        (b"finance\n  health  # why\n\n", frozenset({"finance", "health"})),
        (b"all\n", frozenset(DOMAINS)),
        (b"finanse\n", frozenset(DOMAINS)),
        (b"\xff", frozenset(DOMAINS)),
    ],
)
def test_the_break_glass_file_fails_closed(content, expected):
    assert parse_disabled_domains(content) == expected


async def test_an_unreadable_break_glass_file_disables_everything(env: Env):
    (env.root / "switches/disabled_domains").mkdir()
    env.reload()
    assert error_code(await env.tool("entity_lookup")) == "domain_disabled"


async def test_finance_and_health_need_no_unlock(env: Env):
    assert error_code(await env.tool("finance_summary")) is None


# -- REQ-public-mcp-surface-002 allowlist and arguments --


async def test_unknown_argument_is_refused_before_any_query(tmp_path):
    env = Env(tmp_path, tools=None)
    from butlers.capability_host import default_tools

    env.tools = default_tools()
    env.restart()
    message = await env.tool("entity_lookup", {"name": "Avery", "sql": "select 1"})
    assert error_code(message) == "invalid_argument"
    assert env.projection.acquired == 0


async def test_error_messages_never_echo_input(tmp_path):
    from butlers.capability_host import default_tools

    env = Env(tmp_path)
    env.tools = default_tools()
    env.restart()
    name = "Zq" * 250
    message = await env.tool("entity_lookup", {"name": name})
    assert _error(message)["message"] == "An argument is missing, malformed or out of range."
    assert "Zq" not in json.dumps(message)
    assert env.projection.acquired == 0


@pytest.mark.parametrize(
    "params",
    [
        {"name": "query", "arguments": {}},
        {"name": "sql", "arguments": {}},
        {"name": "entity_lookup", "arguments": []},
        {"name": "entity_lookup", "arguments": {}, "extra": 1},
    ],
)
async def test_only_registered_tools_with_object_arguments_run(env: Env, params):
    from tests.capability_host.helpers import rpc

    response = await env.send(rpc("tools/call", params), tool=params["name"])
    assert error_code(json.loads(response.body)) == "invalid_argument"
    assert env.projection.acquired == 0


# -- REQ-public-mcp-surface-013 error shape and precedence --


async def test_stale_projection_refuses_data(env: Env):
    env.projection.as_of_age = STALE
    message = await env.tool("entity_profile")
    assert message["result"]["isError"] is True and error_code(message) == "projection_stale"
    assert "items" not in message["result"]["structuredContent"]


async def test_null_replication_time_is_stale(env: Env):
    env.projection.as_of_age = None
    assert error_code(await env.tool("entity_lookup")) == "projection_stale"


async def test_staleness_wins_over_a_disabled_domain(env: Env):
    env.projection.as_of_age = STALE
    env.projection.disabled = ["finance"]
    assert error_code(await env.tool("finance_ledger")) == "projection_stale"


async def test_an_invalid_call_is_refused_before_budgets(tmp_path):
    env = Env(tmp_path, budgets=BudgetLimits(per_client_per_minute=1))
    assert error_code(await env.tool("entity_lookup")) is None
    assert error_code(await env.tool("entity_lookup")) == "budget_exhausted"
    assert error_code(await env.tool("entity_lookup", {"sql": "x"})) == "invalid_argument"


async def test_deploy_skew_refuses_every_tool_but_describe(env: Env):
    env.projection.replicated = ["0" * 64]
    assert error_code(await env.tool("entity_lookup")) == "projection_version_mismatch"
    assert error_code(await env.tool("describe")) is None


async def test_a_missing_replicated_digest_is_skew(env: Env):
    env.projection.replicated = []
    assert error_code(await env.tool("trips")) == "projection_version_mismatch"


async def test_a_pending_owner_step_wins_over_staleness_and_switches(env: Env):
    env.projection.applied = None
    env.projection.as_of_age = STALE
    env.projection.disabled = ["finance"]
    message = await env.tool("finance_ledger")
    assert _error(message)["code"] == "projection_apply_pending"
    assert _error(message)["retryable"] is True and _error(message)["retry_after_s"] is None


async def test_deploy_skew_wins_over_a_pending_owner_step(env: Env):
    env.projection.replicated = ["1" * 64]
    env.projection.applied = {**env.projection.applied, "digest": "2" * 64}
    assert error_code(await env.tool("trips")) == "projection_version_mismatch"
    assert env.host.metrics.get("owner_step_pending", change="widening") == 1


async def test_projection_outage_is_retryable(env: Env):
    env.pool.refuse = asyncpg.TooManyConnectionsError("too many")
    message = await env.tool("entity_lookup")
    assert _error(message)["code"] == "unavailable" and _error(message)["retryable"] is True
    env.pool.refuse = OSError("refused")
    assert error_code(await env.tool("entity_lookup")) == "unavailable"


async def test_a_cancelled_replication_read_is_unavailable(env: Env):
    env.projection.fail = asyncpg.QueryCanceledError("statement timeout")
    assert error_code(await env.tool("entity_lookup")) == "unavailable"


async def test_missing_projection_objects_mean_the_owner_step_never_ran(env: Env):
    env.projection.fail = asyncpg.UndefinedTableError("missing view")
    assert error_code(await env.tool("entity_lookup")) == "projection_apply_pending"


async def test_a_reader_defect_fails_closed(tmp_path):
    tools = default_fake_tools()

    async def broken(conn, context, arguments):
        await conn.fetchval("SELECT 1")
        raise KeyError("bug")

    from butlers.capability_host import Tool

    tools["trips"] = Tool("trips", "travel", broken)
    env = Env(tmp_path, tools=tools)
    assert error_code(await env.tool("trips")) == "unavailable"


async def test_missing_canary_values_stop_every_tool(env: Env):
    (env.root / "canaries/canaries.json").unlink()
    env.reload()
    assert error_code(await env.tool("entity_lookup")) == "unavailable"
    env.write("canaries/canaries.json", '["short"]')
    env.reload()
    assert error_code(await env.tool("entity_lookup")) == "unavailable"


# -- REQ-public-mcp-projection-015 fail closed while the owner step is pending --


def _status(**kw) -> ProjectionStatus:
    base = {
        "replicated_digests": frozenset({MANIFEST.digest}),
        "applied_digest": MANIFEST.digest,
        "applied_canonical": MANIFEST.canonical,
        "applied_at": None,
        "copy_complete": True,
        "disabled": frozenset(),
    }
    return ProjectionStatus(**{**base, **kw})


def test_judge_labels_narrowing_and_widening():
    narrowed = _narrowed()
    # The server ships the narrowed manifest; the projection still holds the wider one.
    pending = judge(_status(replicated_digests=frozenset({narrowed.digest})), narrowed)
    assert pending.code == "projection_apply_pending" and pending.change == "narrowing"
    # The server ships the wider manifest over a narrower applied one.
    wider = judge(
        _status(applied_digest=narrowed.digest, applied_canonical=narrowed.canonical), MANIFEST
    )
    assert wider.code == "projection_apply_pending" and wider.change == "widening"
    assert judge(_status(), MANIFEST).code is None
    corrupt = judge(_status(applied_digest="f" * 64, applied_canonical="{"), MANIFEST)
    assert corrupt.change == "narrowing"


async def test_a_narrowing_pending_sets_its_gauge(env: Env):
    narrowed = _narrowed()
    env.projection.replicated = [narrowed.digest]
    env.host.manifest = narrowed
    assert error_code(await env.tool("entity_lookup")) == "projection_apply_pending"
    metrics = env.host.metrics
    assert metrics.get("owner_step_pending", change="narrowing") == 1
    assert metrics.get("owner_step_pending", change="widening") == 0
    assert error_code(await env.tool("describe")) is None


async def test_a_recopy_in_progress_still_refuses(env: Env):
    env.projection.applied = {**env.projection.applied, "copy_complete": False}
    assert error_code(await env.tool("entity_lookup")) == "projection_apply_pending"
    assert env.host.metrics.get("owner_step_pending", change="widening") == 1


async def test_matching_again_serves_after_one_clean_probe(env: Env):
    env.projection.applied = None
    assert error_code(await env.tool("entity_lookup")) == "projection_apply_pending"
    assert not any("strpos" in q for q in env.projection.queries)
    env.projection.applied = Projection().applied
    assert error_code(await env.tool("entity_lookup")) is None
    assert error_code(await env.tool("entity_lookup")) is None
    assert sum("strpos" in q for q in env.projection.queries) == len(MANIFEST.tables)
    assert env.host.metrics.get("owner_step_pending", change="narrowing") == 0
    assert env.host.metrics.get("owner_step_pending", change="widening") == 0


async def test_a_canary_in_the_projection_latches_before_serving(env: Env):
    env.projection.projection_canary = True
    assert error_code(await env.tool("entity_lookup")) == "blocked"
    assert env.host.latched and env.host.metrics.get("canary_hits") == 1
    env.projection.projection_canary = False
    assert error_code(await env.tool("entity_lookup")) == "blocked"
    assert error_code(await env.tool("describe")) is None
