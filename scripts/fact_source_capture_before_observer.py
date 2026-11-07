"""Observe the unchanged PG witness; never replace its fixtures or test outcome."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import uuid
from pathlib import Path
from types import CodeType

import pytest
import pytest_asyncio
from fact_source_capture_before_control import (
    NODE,
    RUNTIME,
    atomic,
    safe_after_diagnostic,
    unknown_after_diagnostic,
)

STATE = None
CONFIG = None
REFERENCES = None
CAPTURE_TASKS = None
DIAGNOSTIC = None

# These sites are public statements in the one hash-verified candidate runtime.
CANDIDATE_RUNTIME_SHA256 = "f438297121fe03f29d838eabd29ff3392f2d85d6b6b897ebd93c993aacb66bf1"
DIAGNOSTIC_SITES = {
    "capture_accepted_report": (
        (270, 278, "capture-snapshot"),
        (279, 298, "capture-owner-lookup"),
        (299, 303, "capture-lock-context"),
        (304, 310, "capture-lock-read"),
        (311, 326, "capture-source-check"),
        (327, 383, "capture-report-build"),
        (384, 389, "capture-report-write"),
        (390, 392, "capture-cache"),
    ),
    "resolve_identities": (
        (181, 184, "resolver-routing"),
        (185, 187, "resolver-endpoint"),
        (188, 188, "resolver-client-context"),
        (189, 192, "resolver-tool-call"),
        (193, 209, "resolver-result-decode"),
        (210, 212, "resolver-wrap"),
    ),
    "resolve_identity": ((248, 256, "resolver-suppress"),),
}


def prepare_diagnostic(runtime, registry_type):
    """Bind only executable code matching the immutable candidate bytes."""
    global DIAGNOSTIC
    DIAGNOSTIC = None
    if CONFIG["phase"] != "after" or runtime.is_symlink():
        return
    source = runtime.read_bytes()
    if hashlib.sha256(source).hexdigest() != CANDIDATE_RUNTIME_SHA256:
        return
    compiled = compile(source, str(runtime), "exec", dont_inherit=True)
    registry = next(
        c
        for c in compiled.co_consts
        if isinstance(c, CodeType) and c.co_name == "FactSourceContextRegistry"
    )
    expected = {c.co_name: c for c in registry.co_consts if isinstance(c, CodeType)}
    codes = {}
    for name, sites in DIAGNOSTIC_SITES.items():
        code = getattr(registry_type, name).__code__
        if code != expected[name] or code.co_filename != str(runtime):
            return
        codes[id(code)] = (code, sites)
    import asyncpg
    import httpx

    from butlers.identity import IdentityResolutionQueryError

    DIAGNOSTIC = {
        "codes": codes,
        "type": registry_type,
        "seen": [],
        "records": [],
        "truncated": False,
        "stopped": False,
        "call": None,
        "identity_error": IdentityResolutionQueryError,
        "http_timeout": httpx.TimeoutException,
        "http_error": httpx.HTTPError,
        "db_errors": (asyncpg.PostgresError, asyncpg.InterfaceError),
    }


def diagnostic_category(error):
    known = {
        TimeoutError: "timeout",
        ValueError: "value-error",
        AssertionError: "assertion-error",
        KeyError: "key-error",
        TypeError: "type-error",
        RuntimeError: "runtime-error",
        asyncio.CancelledError: "cancelled",
    }
    if type(error) in known:
        return known[type(error)]
    if type(error) is DIAGNOSTIC["identity_error"]:
        return "identity-query"
    for kind, label in (
        (DIAGNOSTIC["http_timeout"], "http-timeout"),
        (DIAGNOSTIC["http_error"], "http-error"),
        (DIAGNOSTIC["db_errors"], "db-error"),
    ):
        if isinstance(error, kind):
            return label
    return "other"


def diagnostic_sites(code):
    entry = DIAGNOSTIC["codes"].get(id(code))
    return entry[1] if entry is not None and entry[0] is code else ()


def diagnostic_operation(code, line):
    for start, end, label in diagnostic_sites(code):
        if start <= line <= end:
            return label
    DIAGNOSTIC["truncated"] = True
    return "unknown"


def record_resolver_error(frame, error):
    """Observe bounded events, never infer a causal relationship or inspect messages."""
    data = DIAGNOSTIC
    if (
        not diagnostic_sites(frame.f_code)
        or data["stopped"]
        or isinstance(error, (StopIteration, StopAsyncIteration, GeneratorExit))
    ):
        return
    operation = diagnostic_operation(frame.f_code, frame.f_lineno)
    if any(previous is error and site == operation for previous, site in data["seen"]):
        return
    category = diagnostic_category(error)
    row = next(
        (r for r in data["records"] if r["category"] == category and r["operation"] == operation),
        None,
    )
    if len(data["seen"]) == 24 or (row is None and len(data["records"]) == 8):
        data["stopped"] = data["truncated"] = True
        return
    data["seen"].append((error, operation))  # bounded RAM references prevent identity reuse
    if row is None:
        row = {"category": category, "operation": operation, "count": 0}
        data["records"].append(row)
    row["count"] += 1


def observe_call_error(error):
    if DIAGNOSTIC is None:
        return
    if error is None:
        DIAGNOSTIC["call"] = {"category": "none", "operation": "none"}
        return
    operation, trace = "unknown", error.__traceback__
    for _ in range(128):
        if trace is None:
            break
        frame = trace.tb_frame
        if diagnostic_sites(frame.f_code) and REFERENCES is not None:
            issuer, inboxes = REFERENCES
            if frame.f_locals.get("self") is issuer and (
                frame.f_code.co_name != "capture_accepted_report"
                or frame.f_locals.get("inbox_id") in inboxes
            ):
                operation = diagnostic_operation(frame.f_code, trace.tb_lineno)
        trace = trace.tb_next
    if trace is not None or operation == "unknown":
        DIAGNOSTIC["truncated"] = True
    DIAGNOSTIC["call"] = {"category": diagnostic_category(error), "operation": operation}


def after_diagnostic():
    unknown = unknown_after_diagnostic(CONFIG["binding"])
    if DIAGNOSTIC is None or REFERENCES is None or type(CAPTURE_TASKS) is not dict:
        return unknown
    issuer, inboxes = REFERENCES
    if (
        type(issuer) is not DIAGNOSTIC["type"]
        or set(CAPTURE_TASKS) != set(inboxes)
        or len(inboxes) != 3
        or DIAGNOSTIC["call"] is None
    ):
        return unknown
    tasks = []
    for inbox in inboxes:
        owner, task = CAPTURE_TASKS[inbox]
        if (
            owner is not issuer
            or not isinstance(task, asyncio.Task)
            or task.get_coro().cr_code is not type(issuer).capture_accepted_report.__code__
        ):
            return unknown
        tasks.append(task)
    if len(set(tasks)) != 3:
        return unknown
    return safe_after_diagnostic(
        {
            **unknown,
            "available": True,
            "truncated": DIAGNOSTIC["truncated"],
            "call_error": DIAGNOSTIC["call"],
            "resolver_errors": DIAGNOSTIC["records"],
        },
        CONFIG["binding"],
    )


def test_frame_references(frame, root):
    """Retain only this immutable node's issuer and three synthetic inboxes in RAM."""
    code = frame.f_code
    if Path(code.co_filename) != root / NODE.split("::")[0] or code.co_name != NODE.split("::")[1]:
        return None
    issuer = frame.f_locals.get("issuer")
    inboxes = frame.f_locals.get("inboxes")
    if (
        issuer is not None
        and type(inboxes) is list
        and len(inboxes) == 3
        and all(type(value) is uuid.UUID for value in inboxes)
        and len(set(inboxes)) == 3
    ):
        return issuer, inboxes
    return None


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    """Observe exact test-frame references for both outcomes without changing its calls."""
    global REFERENCES, CAPTURE_TASKS, DIAGNOSTIC
    REFERENCES = None
    CAPTURE_TASKS = None
    # Preserve any existing tracer; unavailable observation remains UNKNOWN.
    if item.nodeid != NODE or sys.gettrace() is not None:
        DIAGNOSTIC = None
        yield
        return
    root = Path(CONFIG["root"])
    test_filename = str(root / NODE.split("::")[0])
    test_name = NODE.split("::")[1]
    runtime_filename = str(root / RUNTIME)
    CAPTURE_TASKS = {}

    def trace(frame, event, arg):
        global REFERENCES, CAPTURE_TASKS
        code = frame.f_code
        if code.co_filename == test_filename and code.co_name == test_name:
            if event == "return":
                refs = test_frame_references(frame, root)
                if refs is not None:
                    REFERENCES = refs
            return trace
        if (
            event == "call"
            and code.co_filename == runtime_filename
            and code.co_name == "capture_accepted_report"
            and REFERENCES is not None
            and CAPTURE_TASKS is not None
        ):
            issuer, inboxes = REFERENCES
            inbox = frame.f_locals.get("inbox_id")
            if frame.f_locals.get("self") is issuer and inbox in inboxes:
                # Observe identities only. Do not wrap the method, task factory,
                # cancellation or any owning pool/transaction behavior.
                try:
                    task = asyncio.current_task()
                    expected = type(issuer).capture_accepted_report.__code__
                    if code is expected and task.get_coro().cr_code is expected:
                        previous = CAPTURE_TASKS.get(inbox)
                        if previous is not None and (
                            previous[0] is not issuer or previous[1] is not task
                        ):
                            CAPTURE_TASKS = None
                        else:
                            CAPTURE_TASKS[inbox] = (issuer, task)
                except (AttributeError, RuntimeError, TypeError):
                    CAPTURE_TASKS = None
        if (
            DIAGNOSTIC is not None
            and code.co_filename == runtime_filename
            and diagnostic_sites(code)
            and code.co_name in ("resolve_identity", "resolve_identities")
            and CAPTURE_TASKS is not None
        ):
            task = asyncio.current_task()
            if (
                REFERENCES is not None
                and frame.f_locals.get("self") is REFERENCES[0]
                and any(
                    owner is REFERENCES[0] and owned is task
                    for owner, owned in CAPTURE_TASKS.values()
                )
            ):
                frame.f_trace_lines = False
                if event == "exception":
                    record_resolver_error(frame, arg[1])
                return trace
        return None

    sys.settrace(trace)
    try:
        yield
    finally:
        sys.settrace(None)


def observed_capture_tasks(issuer, inboxes):
    """Require all three distinct, exact-owned task observations; absence proves nothing."""
    if type(CAPTURE_TASKS) is not dict or set(CAPTURE_TASKS) != set(inboxes):
        return None
    expected = type(issuer).capture_accepted_report.__code__
    tasks = []
    for inbox in inboxes:
        owner, task = CAPTURE_TASKS[inbox]
        if (
            owner is not issuer
            or not isinstance(task, asyncio.Task)
            or task.get_loop() is not asyncio.get_running_loop()
            or task.get_coro().cr_code is not expected
        ):
            return None
        tasks.append(task)
    if len(set(tasks)) != 3:
        return None
    return tasks


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
            observed = test_frame_references(frame, root)
            if observed is not None:
                refs = observed
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
    global CONFIG, STATE, REFERENCES, CAPTURE_TASKS, DIAGNOSTIC
    REFERENCES = None
    CAPTURE_TASKS = None
    DIAGNOSTIC = None
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
    if report.when == "call":
        observe_call_error(call.excinfo.value if call.excinfo is not None else None)


@pytest_asyncio.fixture(autouse=True, loop_scope="session")
async def observe_registered_context(request, authority_env, single_slot_pool):
    if request.node.nodeid != NODE:
        pytest.fail("witness node mismatch")
    import asyncpg

    from butlers.core import fact_authority

    root = Path(CONFIG["root"])
    runtime = await asyncio.to_thread(Path(fact_authority.__file__).resolve)
    if (
        runtime != root / RUNTIME
        or runtime.is_symlink()
        or hashlib.sha256(runtime.read_bytes()).hexdigest() != CONFIG.get("runtime_sha256")
        or not isinstance(single_slot_pool, asyncpg.Pool)
    ):
        pytest.fail("witness source or real pool unavailable")
    prepare_diagnostic(runtime, fact_authority.FactSourceContextRegistry)
    STATE["pool_capacity_one"] = single_slot_pool.get_max_size() == 1
    async with asyncio.timeout(5):
        STATE["runtime_role"] = (
            await single_slot_pool.fetchval("SELECT current_user") == "butler_switchboard_rw"
        )
        endpoint = getattr(authority_env, "_registered_endpoint", None)
        STATE["registered_mcp"] = isinstance(endpoint, str) and endpoint.startswith(
            "http://127.0.0.1:"
        )
    try:
        yield
    finally:
        # Run before the actual module/session fixtures close their pools/MCP.
        async with asyncio.timeout(10):
            STATE["capture_tasks_settled"] = False
            refs = REFERENCES
            if refs is not None:
                issuer, inboxes = refs
                if (
                    type(issuer) is fact_authority.FactSourceContextRegistry
                    and type(inboxes) is list
                    and len(inboxes) == 3
                    and all(type(value) is uuid.UUID for value in inboxes)
                    and len(set(inboxes)) == 3
                ):
                    tasks = observed_capture_tasks(issuer, inboxes)
                    if tasks is not None:
                        # asyncio.wait never cancels these tasks, including on
                        # timeout. Reserve the existing other five seconds for
                        # pool/report observations inside the same ten-second cap.
                        _, pending = await asyncio.wait(tasks, timeout=5)
                        STATE["capture_tasks_settled"] = not pending and all(
                            task.done() for task in tasks
                        )
            async with single_slot_pool.acquire(timeout=5) as connection:
                STATE["pool_reacquired"] = (
                    not connection.is_in_transaction()
                    and await connection.fetchval("SELECT 1") == 1
                )
            if refs is not None:
                issuer, inboxes = refs
                if (
                    type(issuer) is fact_authority.FactSourceContextRegistry
                    and type(inboxes) is list
                    and len(inboxes) == 3
                    and all(type(value) is uuid.UUID for value in inboxes)
                    and len(set(inboxes)) == 3
                ):
                    # Never enumerate unrelated inboxes or read their reports.
                    rows = await authority_env.sw.fetch(
                        "SELECT id,request_context->'_fact_source_report' AS report "
                        "FROM switchboard.message_inbox WHERE id = ANY($1::uuid[])",
                        inboxes,
                    )
                    if len(rows) == 3 and {row["id"] for row in rows} == set(inboxes):
                        stored = sum(row["report"] is not None for row in rows)
                        STATE["durable_partial_reports"] = (
                            stored if CONFIG["phase"] == "before" else 3 - stored
                        )
                        if CONFIG["phase"] == "before" and isinstance(issuer._reports, dict):
                            STATE["memory_partial_reports"] = len(issuer._reports)


def pytest_sessionfinish(session, exitstatus):
    STATE["pytest_exit"] = int(exitstatus)
    STATE["terminal"] = STATE["collected"] == STATE["starts"] == 1
    atomic(Path(CONFIG["output"]), STATE)
    if CONFIG["phase"] == "after":
        atomic(Path(CONFIG["output"]).with_name("after-diagnostic.json"), after_diagnostic())
