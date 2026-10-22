"""Observe the unchanged PG witness; never replace its fixtures or test outcome."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path

import pytest
import pytest_asyncio
from fact_source_capture_before_control import NODE, RUNTIME, atomic

STATE = None
CONFIG = None
REFERENCES = None


def exception_facts(error, root):
    facts = {
        "exception": "TimeoutError" if type(error) is TimeoutError else "other",
        "nested_acquire": False,
        "owner_frame": False,
        "lookup_reached": "UNKNOWN",
    }
    refs = None
    seen = set()
    current = error
    for _ in range(12):
        if current is None or id(current) in seen:
            break
        seen.add(id(current))
        trace = current.__traceback__
        for _ in range(128):
            if trace is None:
                break
            frame = trace.tb_frame
            code = frame.f_code
            filename = Path(code.co_filename)
            if filename == root / NODE.split("::")[0] and code.co_name == NODE.split("::")[1]:
                issuer = frame.f_locals.get("issuer")
                inboxes = frame.f_locals.get("inboxes")
                if issuer is not None and isinstance(inboxes, list) and len(inboxes) == 3:
                    refs = (issuer, inboxes)
            if filename == root / RUNTIME and code.co_name == "capture_accepted_report":
                facts["owner_frame"] = True
            if filename.parts[-2:] == ("asyncpg", "pool.py") and code.co_name == "_acquire":
                facts["nested_acquire"] = True
            if "fastmcp" in filename.parts and code.co_name == "call_tool":
                facts["lookup_reached"] = "OBSERVED"
            trace = trace.tb_next
        current = current.__cause__ or current.__context__
    return facts, refs


def pytest_configure(config):
    global CONFIG, STATE
    value = json.loads(Path(os.environ["FACT_CAPTURE_CONTROL_CONFIG"]).read_text())
    CONFIG = value
    STATE = {
        "schema": "fact-capture-pg-phase.v1",
        "binding": value["binding"],
        "phase": value["phase"],
        "collected": 0,
        "starts": 0,
        "phases": {},
        "exception": "none",
        "nested_acquire": False,
        "owner_frame": False,
        "pool_capacity_one": False,
        "runtime_role": False,
        "registered_mcp": False,
        "lookup_reached": "UNKNOWN",
        "capture_tasks_settled": False,
        "pool_reacquired": False,
        "durable_partial_reports": None,
        "memory_partial_reports": None,
        "process_cleanup": False,
        "sentinel_preserved": False,
        "pytest_exit": None,
        "terminal": False,
    }


def pytest_collection_finish(session):
    STATE["collected"] = sum(item.nodeid == NODE for item in session.items)
    if len(session.items) != 1 or STATE["collected"] != 1:
        STATE["collected"] = 0


def pytest_runtest_logstart(nodeid, location):
    if nodeid == NODE:
        STATE["starts"] += 1


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    global REFERENCES
    result = yield
    report = result.get_result()
    if item.nodeid != NODE:
        return
    if report.when in STATE["phases"]:
        STATE["terminal"] = False
        STATE["starts"] += 1
    STATE["phases"][report.when] = report.outcome
    if report.when == "call" and call.excinfo is not None:
        facts, REFERENCES = exception_facts(call.excinfo.value, Path(CONFIG["root"]))
        STATE.update(facts)


@pytest_asyncio.fixture(autouse=True, loop_scope="session")
async def observe_registered_context(request, authority_env, single_slot_pool):
    if request.node.nodeid != NODE:
        pytest.fail("witness node mismatch")
    import asyncpg

    from butlers.core import fact_authority

    root = Path(CONFIG["root"])
    runtime = Path(fact_authority.__file__).resolve()
    if (
        runtime != root / RUNTIME
        or runtime.is_symlink()
        or hashlib.sha256(runtime.read_bytes()).hexdigest() != CONFIG.get("runtime_sha256")
        or not isinstance(single_slot_pool, asyncpg.Pool)
    ):
        pytest.fail("witness source or real pool unavailable")
    STATE["pool_capacity_one"] = single_slot_pool.get_max_size() == 1
    async with asyncio.timeout(5):
        STATE["runtime_role"] = (
            await single_slot_pool.fetchval("SELECT current_user") == "butler_switchboard_rw"
        )
        endpoint = getattr(authority_env, "_registered_endpoint", None)
        STATE["registered_mcp"] = isinstance(endpoint, str) and endpoint.startswith(
            "http://127.0.0.1:"
        )
        previous = {
            row["id"]
            for row in await authority_env.sw.fetch(
                "SELECT id FROM switchboard.message_inbox LIMIT 10001"
            )
        }
    if len(previous) > 10000:
        pytest.fail("witness source population bound")
    try:
        yield
    finally:
        # Run before the actual module/session fixtures close their pools/MCP.
        async with asyncio.timeout(10):
            STATE["capture_tasks_settled"] = not any(
                task is not asyncio.current_task()
                and not task.done()
                and getattr(task.get_coro(), "__qualname__", "")
                == "FactSourceContextRegistry.capture_accepted_report"
                for task in asyncio.all_tasks()
            )
            async with single_slot_pool.acquire(timeout=5) as connection:
                STATE["pool_reacquired"] = (
                    not connection.is_in_transaction()
                    and await connection.fetchval("SELECT 1") == 1
                )
            rows = await authority_env.sw.fetch(
                "SELECT id,request_context->'_fact_source_report' AS report "
                "FROM switchboard.message_inbox LIMIT 10001"
            )
            created = [row for row in rows if row["id"] not in previous]
            if len(created) == 3:
                stored = sum(row["report"] is not None for row in created)
                STATE["durable_partial_reports"] = (
                    stored if CONFIG["phase"] == "before" else 3 - stored
                )
            if CONFIG["phase"] == "before" and REFERENCES is not None:
                issuer, inboxes = REFERENCES
                if (
                    type(issuer) is fact_authority.FactSourceContextRegistry
                    and set(inboxes) == {row["id"] for row in created}
                    and isinstance(issuer._reports, dict)
                ):
                    STATE["memory_partial_reports"] = len(issuer._reports)


def pytest_sessionfinish(session, exitstatus):
    STATE["pytest_exit"] = int(exitstatus)
    STATE["terminal"] = STATE["collected"] == STATE["starts"] == 1
    atomic(Path(CONFIG["output"]), STATE)
