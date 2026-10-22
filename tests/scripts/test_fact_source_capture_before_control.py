"""Software controls only; these do not execute the required PostgreSQL witness."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import fact_source_capture_before_control as control  # noqa: E402
import fact_source_capture_before_observer as observer  # noqa: E402

pytestmark = pytest.mark.unit


def phase(name):
    return {
        "schema": "fact-capture-pg-phase.v1",
        "binding": {"nonce": "a" * 64},
        "phase": name,
        "collected": 1,
        "starts": 1,
        "phases": {
            "setup": "passed",
            "call": "failed" if name == "before" else "passed",
            "teardown": "passed",
        },
        "exception": "TimeoutError" if name == "before" else "none",
        "nested_acquire": name == "before",
        "owner_frame": name == "before",
        "pool_capacity_one": True,
        "runtime_role": True,
        "registered_mcp": True,
        "lookup_reached": "UNKNOWN",
        "capture_tasks_settled": True,
        "pool_reacquired": True,
        "durable_partial_reports": 0,
        "memory_partial_reports": 0 if name == "before" else None,
        "process_cleanup": True,
        "sentinel_preserved": True,
        "pytest_exit": 1 if name == "before" else 0,
        "terminal": True,
    }


def test_only_executing_old_red_and_paired_green_with_complete_cleanup_qualify():
    """Setup/no-CALL, healthy old code, wrong failure and missing cleanup cannot qualify."""
    before, after = phase("before"), phase("after")
    import ci_partition

    with pytest.raises(ValueError):
        ci_partition.require_receipt_types(before)
    assert control.require_witness(before, after, {"nonce": "a" * 64}) == "WITNESS_COMPLETE"
    variants = [
        ("collected", 0),
        ("starts", 0),
        ("exception", "AssertionError"),
        ("nested_acquire", False),
        ("owner_frame", False),
        ("pool_capacity_one", False),
        ("runtime_role", False),
        ("registered_mcp", False),
        ("capture_tasks_settled", False),
        ("pool_reacquired", False),
        ("durable_partial_reports", 1),
        ("memory_partial_reports", 1),
        ("process_cleanup", False),
        ("sentinel_preserved", False),
        ("terminal", False),
        ("pytest_exit", 0),
        ("pytest_exit", True),
        ("phases", {"setup": "failed"}),
        ("phases", {"setup": "passed", "call": "passed", "teardown": "passed"}),
        ("phases", {"setup": "passed", "call": "failed", "teardown": "failed"}),
        ("binding", {"nonce": "b" * 64}),
        ("untrusted", "PRIVATE_VALUE"),
    ]
    for key, value in variants:
        bad = copy.deepcopy(before)
        bad[key] = value
        with pytest.raises(ValueError):
            control.require_witness(bad, after, {"nonce": "a" * 64})
    for key, value in [
        ("pytest_exit", 1),
        ("phases", {"call": "failed"}),
        ("process_cleanup", False),
        ("terminal", False),
    ]:
        bad = copy.deepcopy(after)
        bad[key] = value
        with pytest.raises(ValueError):
            control.require_witness(before, bad, {"nonce": "a" * 64})


def test_disposable_workload_allows_only_the_exact_immutable_runtime_overlay(tmp_path, monkeypatch):
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=tmp_path, text=True).strip()

    git("init", "-q")
    runtime = tmp_path / control.RUNTIME
    runtime.parent.mkdir(parents=True)
    runtime.write_text("old runtime\n")
    (tmp_path / "uv.lock").write_text("locked\n")
    git("add", ".")
    git(
        "-c",
        "user.name=Control",
        "-c",
        "user.email=control@example.invalid",
        "commit",
        "-qm",
        "old fixture",
    )
    old = git("rev-parse", "HEAD:" + control.RUNTIME)
    runtime.write_text("new runtime\n")
    git("add", ".")
    git(
        "-c",
        "user.name=Control",
        "-c",
        "user.email=control@example.invalid",
        "commit",
        "-qm",
        "candidate fixture",
    )
    monkeypatch.setattr(control, "CANDIDATE", git("rev-parse", "HEAD"))
    monkeypatch.setattr(control, "CANDIDATE_TREE", git("rev-parse", "HEAD^{tree}"))
    monkeypatch.setattr(control, "OLD_BLOB", old)
    monkeypatch.setattr(control, "NEW_BLOB", git("rev-parse", "HEAD:" + control.RUNTIME))
    healthy = control.workload_state(tmp_path, "after")
    assert healthy["differences"] == []
    control.overlay(tmp_path, "before")
    assert control.workload_state(tmp_path, "before")["differences"] == [control.RUNTIME]
    runtime.write_text("unapproved runtime\n")
    with pytest.raises(ValueError):
        control.workload_state(tmp_path, "before")
    control.overlay(tmp_path, "before")
    (tmp_path / "uv.lock").write_text("changed lock\n")
    with pytest.raises(ValueError):
        control.workload_state(tmp_path, "before")
    git("restore", "uv.lock")
    (tmp_path / "injected.py").write_text("unexpected\n")
    with pytest.raises(ValueError):
        control.workload_state(tmp_path, "before")
    (tmp_path / "injected.py").unlink()
    control.overlay(tmp_path, "after")
    assert control.workload_state(tmp_path, "after") == healthy


def test_owned_domain_settles_resistant_descendant_and_preserves_unrelated_sentinel(
    tmp_path, monkeypatch
):
    sentinel = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"])
    try:
        healthy = control.run_owned([sys.executable, "-c", "pass"], os.environ.copy(), 5)
        assert healthy["exit"] == 0 and healthy["cleanup_complete"]
        escaped = tmp_path / "escaped"
        code = (
            "import os,signal,time,pathlib;"
            "p=os.fork();"
            f"pathlib.Path({str(escaped)!r}).write_text(str(p));"
            "os.setsid() if p==0 else None;"
            "signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(30)"
        )
        hung = control.run_owned([sys.executable, "-c", code], os.environ.copy(), 2)
        assert hung["timed_out"] and hung["cleanup_complete"] and hung["exit"] != 0
        # An absolute script path does not choose its editable-repair target.
        controller = tmp_path / "controller"
        controller.mkdir()
        original = controller / "original"
        original.write_text("unchanged")
        workload = tmp_path / "workload"
        (workload / "scripts").mkdir(parents=True)
        prepare = workload / "scripts/ci_environment.py"
        prepare.write_text(
            "import pathlib,sys\n"
            "assert sys.argv[1:] == ['prepare']\n"
            "(pathlib.Path.cwd()/'prepared').write_text('software-only')\n"
        )
        with monkeypatch.context() as context:
            context.chdir(controller)
            prepared = control.run_owned(
                [sys.executable, str(prepare), "prepare"],
                os.environ.copy(),
                5,
                cwd=workload,
            )
        assert prepared["exit"] == 0 and prepared["cleanup_complete"]
        assert (workload / "prepared").read_text() == "software-only"
        assert sorted(p.name for p in controller.iterdir()) == ["original"]
        assert original.read_text() == "unchanged" and sentinel.poll() is None
        # Exercise the real unchanged installer through the producer boundary.
        # Only curl is a software stand-in; no network or installed shared files.
        binaries = tmp_path / "bin"
        binaries.mkdir()
        curl = binaries / "curl"
        curl.write_text(
            "#!/bin/sh\nprintf '%s\\n' 'mkdir -p \"$HOME/.local/bin\"' 'touch \"$HOME/.local/bin/uv\"'\n"
        )
        curl.chmod(0o755)
        home = tmp_path / "home"
        home.mkdir()
        environment = {
            **os.environ,
            "HOME": str(home),
            "PATH": str(binaries) + os.pathsep + os.environ["PATH"],
        }
        installed = control.prepare_uv(
            Path(__file__).resolve().parents[2], tmp_path, environment, 10
        )
        assert (home / ".local/bin/uv").is_file()
        assert (tmp_path / "installer-path").read_text() == str(home / ".local/bin") + "\n"
        assert installed["PATH"].split(os.pathsep)[0] == str(home / ".local/bin")
        with pytest.raises(ValueError, match="uv-install"):
            control.prepare_uv(tmp_path / "missing-source", tmp_path, environment, 5)
        assert sentinel.poll() is None
        assert escaped.exists(), "negative child actually reached"
    finally:
        sentinel.kill()
        sentinel.wait(timeout=5)


def test_exception_observation_discards_values_and_never_infers_missing_old_await(tmp_path):
    issuer = type("Issuer", (), {"_reports": {}})()
    inboxes = [uuid.uuid4() for _ in range(3)]
    namespace = {"issuer": issuer, "inboxes": inboxes}
    exec(
        compile(
            "def test_capacity_one_registered_owner_capture_and_concurrent_sources(issuer,inboxes):\n"
            ' raise TimeoutError("PRIVATE_ERROR")\n',
            str(tmp_path / control.NODE.split("::")[0]),
            "exec",
        ),
        namespace,
    )
    try:
        namespace["test_capacity_one_registered_owner_capture_and_concurrent_sources"](
            issuer, inboxes
        )
    except TimeoutError as error:
        facts, refs = observer.exception_facts(error, tmp_path)
    assert facts["exception"] == "TimeoutError" and facts["nested_acquire"] is False
    assert "PRIVATE" not in json.dumps(facts)
    assert refs is not None and refs[0] is issuer and refs[1] == inboxes
    # Reach the real observer's exception-chain branch using software-only frames.
    import asyncio

    namespace["asyncio"] = asyncio
    exec(
        compile(
            "def _acquire():\n raise asyncio.CancelledError()\n",
            str(tmp_path / "asyncpg/pool.py"),
            "exec",
        ),
        namespace,
    )
    exec(
        compile(
            "def capture_accepted_report():\n _acquire()\n", str(tmp_path / control.RUNTIME), "exec"
        ),
        namespace,
    )
    exec(
        compile(
            "def test_capacity_one_registered_owner_capture_and_concurrent_sources(issuer,inboxes):\n"
            " try:capture_accepted_report()\n"
            " except asyncio.CancelledError as error:raise TimeoutError() from error\n",
            str(tmp_path / control.NODE.split("::")[0]),
            "exec",
        ),
        namespace,
    )
    try:
        namespace["test_capacity_one_registered_owner_capture_and_concurrent_sources"](
            issuer, inboxes
        )
    except TimeoutError as error:
        facts, refs = observer.exception_facts(error, tmp_path)
    assert facts["nested_acquire"] and facts["owner_frame"] and refs[0] is issuer
    facts, refs = observer.exception_facts(ValueError("PRIVATE_DATA"), tmp_path)
    assert facts["exception"] == "other" and refs is None


def test_real_pytest_observer_records_setup_refusal_without_claiming_pg(tmp_path):
    plugin = Path(observer.__file__).resolve()
    (tmp_path / "test_refusal.py").write_text('def test_refusal():\n assert False,"PRIVATE_BODY"\n')
    config = {
        "binding": {"nonce": "a" * 64},
        "phase": "before",
        "root": str(tmp_path),
        "output": str(tmp_path / "phase.json"),
    }
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps(config))
    env = {
        **os.environ,
        "FACT_CAPTURE_CONTROL_CONFIG": str(cfg),
        "PYTHONPATH": str(plugin.parent),
        "PYTEST_ADDOPTS": "",
    }
    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            plugin.stem,
            "-q",
            "-o",
            "addopts=",
            "test_refusal.py",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        timeout=30,
    )
    assert run.returncode != 0
    data = json.loads((tmp_path / "phase.json").read_text())
    assert data["phases"].get("setup") != "passed" or data["collected"] != 1
    assert data["registered_mcp"] is False
    assert "PRIVATE" not in json.dumps(data)


def test_cli_rejects_unbound_environment_before_any_execution(tmp_path):
    env = {
        "PATH": os.environ["PATH"],
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_REPOSITORY": "other/repository",
        "PRIVATE_VALUE": "never-export",
    }
    run = subprocess.run(
        [sys.executable, str(Path(control.__file__).resolve())],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        timeout=10,
    )
    assert run.returncode != 0
    assert b"never-export" not in run.stdout + run.stderr
    assert not list(tmp_path.glob("**/.venv"))
