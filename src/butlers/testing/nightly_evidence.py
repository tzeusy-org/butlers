"""Opt-in pytest plugin: safe selected identities and phase outcomes, no payloads."""

from __future__ import annotations

import ast
import hashlib
import os
import re
import tempfile
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from butlers.nightly_assurance import atomic_json, digest

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_PATH = re.compile(r"(?:tests|roster)/[A-Za-z0-9_./-]+\.py\Z")
_COLLECTION: list[str] | None = None
_MAPPING: dict[str, str] = {}
_OUTCOMES: dict[str, str] = {}
_PATH_TO_FILE: Path | None = None
_CONSISTENT = True
_LAST_WRITE = float("-inf")
_IS_WORKER = False
_CLOCKS: dict[str, dict] = {}
_EXPECTED_WORKERS: set[str] = set()
_LOCAL_CLOCK: dict = {}
_QA_CONTROL: dict = {}


def safe_manifest(nodes: list[str]) -> dict[str, str]:
    """Number cases within their real function's collected order, without parameter text."""
    counts: dict[str, int] = defaultdict(int)
    result = {}
    for node in nodes:
        base = node.split("[", 1)[0]
        parts = base.split("::")
        if not _PATH.fullmatch(parts[0]) or ".." in Path(parts[0]).parts or len(parts) < 2:
            raise ValueError("unsafe-collected-path")
        if not all(_IDENTIFIER.fullmatch(part) for part in parts[1:]):
            raise ValueError("unsafe-collected-name")
        counts[base] += 1
        result[node] = f"{base}::case-{counts[base]}"
    if len(result) != len(nodes):
        raise ValueError("duplicate-collected-node")
    return result


def pytest_configure(config: pytest.Config) -> None:
    global _PATH_TO_FILE, _IS_WORKER, _COLLECTION, _CONSISTENT, _LAST_WRITE
    _IS_WORKER = hasattr(config, "workerinput")
    _COLLECTION = None
    _CONSISTENT = True
    _LAST_WRITE = float("-inf")
    _MAPPING.clear()
    _OUTCOMES.clear()
    _CLOCKS.clear()
    _EXPECTED_WORKERS.clear()
    _LOCAL_CLOCK.clear()
    _QA_CONTROL.clear()
    if os.environ.get("BUTLERS_NIGHTLY_CLOCK_REQUIRED") == "1":
        library = Path(os.environ["LD_PRELOAD"])
        started = time.monotonic()
        wall = datetime.now(UTC)
        time.sleep(0.02)
        _LOCAL_CLOCK.update(
            wall_at_start=wall.isoformat(),
            monotonic_start=started,
            monotonic_delta=time.monotonic() - started,
            library_sha256=hashlib.sha256(library.read_bytes()).hexdigest(),
            monotonic_unshifted=os.environ.get("FAKETIME_DONT_FAKE_MONOTONIC") == "1",
        )
    path = os.environ.get("BUTLERS_NIGHTLY_PYTEST_EVIDENCE")
    _PATH_TO_FILE = Path(path) if path and not _IS_WORKER else None


def _collected(nodes: list[str]) -> None:
    global _COLLECTION, _CONSISTENT
    try:
        mapping = safe_manifest(nodes)
    except ValueError:
        _CONSISTENT = False
        _write()
        return
    if _COLLECTION is not None and _COLLECTION != nodes:
        _CONSISTENT = False
    else:
        _COLLECTION = list(nodes)
        _MAPPING.update(mapping)
    _write()


def neutralized_qa_cron(source: Path, destination: Path) -> tuple[object, dict]:
    """Copy the complete current module, neutralizing ONLY PR4310's cron literal.

    Compile only its existing function against the original module globals.
    All helpers, fixtures, decorators, parameters and assertions stay current.
    Generated experiment source has an ignored historical filename, never a
    production filename or an applied historical migration identity.
    """
    name = "test_qa_tick_rechecks_own_policy_before_each_dispatch"
    body = source.read_text()
    parsed = ast.parse(body)
    function = next(
        item for item in parsed.body if isinstance(item, ast.AsyncFunctionDef) and item.name == name
    )
    lines = body.splitlines(keepends=True)
    start, end = function.lineno - 1, function.end_lineno
    function_body = "".join(lines[start:end])
    needle = 'schedule_create(owner, "qa-patrol", "0 0 1 1 *", "qa-patrol")'
    assert function_body.count(needle) == 1, "qa-cron-control-source-drift"
    changed = function_body.replace(needle, needle.replace('"0 0 1 1 *"', '"*/1 * * * *"'))
    snapshot = "".join(lines[:start]) + changed + "".join(lines[end:])
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(snapshot)
    transformed = ast.parse(snapshot)
    replacement = next(
        item
        for item in transformed.body
        if isinstance(item, ast.AsyncFunctionDef) and item.name == name
    )
    replacement.decorator_list = []  # existing collected item keeps its original marks
    code = compile(ast.Module(body=[replacement], type_ignores=[]), str(destination), "exec")
    return code, {
        "species": "current-module-with-only-PR4310-cron-neutralized",
        "source_sha256": hashlib.sha256(body.encode()).hexdigest(),
        "snapshot_sha256": hashlib.sha256(snapshot.encode()).hexdigest(),
        "changed_literals": 1,
    }


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    if os.environ.get("BUTLERS_NIGHTLY_QA_OLD_CRON_CONTROL") != "1":
        return
    assert os.environ.get("BUTLERS_NIGHTLY_MINUTE_MILESTONE") == "1" and not _IS_WORKER
    selected = [
        item
        for item in items
        if item.originalname == "test_qa_tick_rechecks_own_policy_before_each_dispatch"
    ]
    assert len(selected) == 2, "qa-cron-control-requires-both-original-cases"
    original = selected[0].obj
    source = Path(selected[0].path)
    destination = Path.cwd() / ".tmp/nightly-controls/core_scheduler_PR4310_cron_only.py"
    code, witness = neutralized_qa_cron(source, destination)
    namespace = {}
    exec(code, original.__globals__, namespace)
    original.__code__ = namespace[original.__name__].__code__
    _QA_CONTROL.update(witness)


def pytest_collection_finish(session: pytest.Session) -> None:
    if not _IS_WORKER and session.items:
        _collected([item.nodeid for item in session.items])


@pytest.hookimpl(optionalhook=True)
def pytest_xdist_node_collection_finished(node: object, ids: list[str]) -> None:
    _EXPECTED_WORKERS.add(node.gateway.id)
    _collected(ids)


@pytest.hookimpl(optionalhook=True)
def pytest_testnodedown(node: object, error: object) -> None:
    observation = node.workeroutput.get("nightly_clock")
    if isinstance(observation, dict):
        _CLOCKS[node.gateway.id] = observation


def _write(*, complete: bool = False) -> None:
    global _LAST_WRITE
    if _PATH_TO_FILE is not None:
        manifest = list(_MAPPING.values())
        atomic_json(
            _PATH_TO_FILE,
            {
                "manifest": manifest,
                "manifest_digest": digest(manifest),
                "outcomes": _OUTCOMES,
                "controller_complete": complete and _CONSISTENT,
                "collection_consistent": _CONSISTENT,
                "process_clocks": _CLOCKS if _EXPECTED_WORKERS else {"controller": _LOCAL_CLOCK},
                "expected_clock_processes": sorted(_EXPECTED_WORKERS) or ["controller"],
                "qa_cron_control": _QA_CONTROL or None,
            },
        )
        _LAST_WRITE = time.monotonic()


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    if _IS_WORKER or report.nodeid not in _MAPPING:
        return
    node = _MAPPING[report.nodeid]
    if report.failed:
        _OUTCOMES[node] = {"setup": "SETUP_ERROR", "call": "FAILED", "teardown": "TEARDOWN_ERROR"}[
            report.when
        ]
    elif report.skipped:
        _OUTCOMES.setdefault(node, "SKIPPED")
    elif report.when == "teardown":
        _OUTCOMES.setdefault(node, "PASSED")
    if report.failed or time.monotonic() - _LAST_WRITE >= 30:
        _write()


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if _IS_WORKER:
        session.config.workeroutput["nightly_clock"] = dict(_LOCAL_CLOCK)
    _write(complete=exitstatus in {0, 1} and len(_OUTCOMES) == len(_MAPPING))


def minute_milestone(stage: str, case: str) -> None:
    """Only the isolated folded QA subphase may move its own process wall clock.

    The barrier is positioned after the original resume assertions and before
    the original zero-repeat assertion. No sleep oracle or production clock
    argument is introduced. Ordinary/full-suite invocations take no action.
    """
    if os.environ.get("BUTLERS_NIGHTLY_MINUTE_MILESTONE") != "1":
        return
    from butlers.nightly_assurance import read_json

    assert stage in {"before-resume", "before-repeat"} and case in {"0", "1"}
    assert not _IS_WORKER, "minute-control-must-be-serial"
    assert "FAKETIME" not in os.environ and os.environ.get("FAKETIME_NO_CACHE") == "1"
    target_file = Path(os.environ["FAKETIME_TIMESTAMP_FILE"])
    witness_file = Path(os.environ["BUTLERS_NIGHTLY_CLOCK_WITNESS"])
    witness = read_json(witness_file) if witness_file.exists() else {}
    current = datetime.now(UTC)
    target = current.replace(hour=12, minute=34, second=50, microsecond=0)
    if stage == "before-repeat":
        prior = witness[case]
        before = datetime.fromisoformat(prior["before"])
        target = before.replace(second=0, microsecond=0) + timedelta(minutes=1, seconds=10)
    monotonic = time.monotonic()
    fd, name = tempfile.mkstemp(dir=target_file.parent, prefix="clock-")
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(target.strftime("@%Y-%m-%d %H:%M:%S") + "\n")
        os.replace(name, target_file)
    finally:
        Path(name).unlink(missing_ok=True)
    observed = datetime.now(UTC)
    assert abs((observed - target).total_seconds()) < 5, "wall-clock-milestone-not-observed"
    elapsed = time.monotonic() - monotonic
    assert 0 <= elapsed < 5, "monotonic-clock-milestone-shifted"
    if stage == "before-resume":
        witness[case] = {"before": observed.isoformat(), "monotonic_before": monotonic}
    else:
        assert observed.replace(second=0, microsecond=0) > before.replace(second=0, microsecond=0)
        assert 0 <= monotonic - prior["monotonic_before"] < 300
        witness[case].update(
            after=observed.isoformat(), monotonic_after=monotonic, actual_boundary_crossed=True
        )
    atomic_json(witness_file, witness)
