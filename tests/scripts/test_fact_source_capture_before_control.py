"""Software controls only; these do not execute the required PostgreSQL witness."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
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
    import ci_lanes

    # The current fan-in consumes strict lane counts; phase diagnostics are not counts.
    with tempfile.TemporaryDirectory() as directory:
        counts = Path(directory) / "lane-counts.json"
        counts.write_text(json.dumps({"unit": 1, "integration": 1}))
        assert ci_lanes.read_lane_counts(counts) == {"unit": 1, "integration": 1}
        counts.write_text(json.dumps(before))
        with pytest.raises(ValueError):
            ci_lanes.read_lane_counts(counts)
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
        assert healthy["observation"]["child_exit"] == 0
        assert healthy["observation"]["supervisor_exit"] == 0
        assert healthy["observation"]["settled_complete"] is True
        assert healthy["observation"].get("first_scan_refusal") is None
        refused = control.run_owned(
            [sys.executable, "-c", "import sys;print('PRIVATE_CHILD');sys.exit(7)"],
            os.environ.copy(),
            5,
        )
        assert refused["exit"] == 7 and refused["cleanup_complete"]
        assert refused["observation"]["child_exit"] == 7
        assert refused["observation"]["reason"] == "settled"
        diagnostic = control.refusal(
            ValueError("environment-prepare"),
            {"stage": "environment-prepare", "preparation": refused["observation"]},
            {"nonce": "a" * 64},
        )
        assert diagnostic["preparation"]["child_exit"] == 7
        assert diagnostic["pg_call"] == diagnostic["cleanup"] == "UNKNOWN"
        assert "PRIVATE" not in json.dumps(diagnostic)
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
        assert hung["observation"]["timed_out"] is True
        assert hung["observation"]["child_signal"] == "SIGKILL"
        assert hung["observation"]["kernel_children_closed"] is True
        private = control.lifecycle_observation(
            [
                {"event": "outcome", "code": "PRIVATE_EXIT", "signal": "PRIVATE_SIGNAL"},
                {
                    "event": "diagnostic",
                    "reason": "PRIVATE_REASON",
                    "counts": {"scan_refused": "PRIVATE_COUNT"},
                    "counts_saturated": "PRIVATE_BOOL",
                },
            ],
            "PRIVATE_SUPERVISOR",
            False,
            False,
            True,
        )
        assert private["child_exit"] is private["supervisor_exit"] is None
        assert private["counts"]["scan_refused"] is None
        assert private["protocol_refused"] and "PRIVATE" not in json.dumps(private)
        latched = control.lifecycle_observation(
            [
                {"event": "outcome", "code": 1},
                {"event": "settled", "complete": False},
                {
                    "event": "diagnostic",
                    "reason": "latched_refusal",
                    "counts": {key: int(key == "scan_refused") for key in control.REFUSAL_COUNTS},
                    "counts_saturated": False,
                    "first_scan_refusal": {"s": 2, "k": 1, "o": 2, "b": 7},
                    "kernel_children_closed": True,
                },
            ],
            2,
            False,
            False,
            False,
        )
        assert latched["reason"] == "latched_refusal" and latched["counts"]["scan_refused"] == 1
        assert latched["settled_complete"] is False and latched["supervisor_exit"] == 2
        assert latched.get("first_scan_refusal") == {"s": 2, "k": 1, "o": 2, "b": 7}
        # Exercise the actual local producer's closed refusal branch without procfs IO.
        import builtins
        import errno
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "_fact_capture_supervisor", control.SUPERVISOR
        )
        supervisor = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(supervisor)
        # Module-level failures preserve exit2 and never render exception input/context.
        boundary = subprocess.run(
            [
                sys.executable,
                str(control.SUPERVISOR),
                "PRIVATE_PARENT",
                "PRIVATE_START",
                sys.executable,
                "-c",
                "pass",
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=5,
        )
        assert boundary.returncode == 2 and boundary.stdout == boundary.stderr == b""
        producer = {
            "counts": dict.fromkeys(supervisor.REFUSAL_KEYS, 0),
            "counts_saturated": False,
            "first_scan_refusal": None,
        }
        with monkeypatch.context() as context:
            context.setattr(supervisor, "process", lambda pid: (101, "S"))
            context.setattr(supervisor.os, "listdir", lambda path: ["37"])

            def refused_open(*args, **kwargs):
                raise FileNotFoundError(errno.ENOENT, "PRIVATE_ERROR", "PRIVATE_PATH")

            context.setattr(builtins, "open", refused_open)
            children, truncated = supervisor.children(37, producer, site=2)
        assert children == set() and truncated is True
        assert producer["counts"]["scan_refused"] == 1
        assert producer.get("first_scan_refusal") == {"s": 2, "k": 1, "o": 2, "b": 7}
        events = [
            {"event": "outcome", "code": 0},
            {"event": "settled", "complete": False},
            {"event": "diagnostic", "reason": "latched_refusal", **producer},
        ]
        projected = control.lifecycle_observation(events, 2, False, False, False)
        assert projected["first_scan_refusal"] == producer["first_scan_refusal"]
        assert projected["supervisor_exit"] == 2 and projected["settled_complete"] is False
        assert "PRIVATE" not in json.dumps(projected)
        # Only one well-formed closed tuple may be exported; values never escape.
        malformed = [
            None,
            "PRIVATE_VALUE",
            {},
            {**producer["first_scan_refusal"], "path": "PRIVATE_PATH"},
        ]
        for key, value in (
            ("s", True),
            ("s", 0),
            ("s", 4),
            ("s", "PRIVATE_SITE"),
            ("k", 0),
            ("k", 7),
            ("o", 0),
            ("o", 5),
            ("b", -1),
            ("b", 32),
            ("b", 4),
            ("b", 8),
            ("b", 15),
            ("b", 31),
        ):
            malformed.append({**producer["first_scan_refusal"], key: value})
        for value in malformed:
            invalid = copy.deepcopy(events)
            invalid[-1]["first_scan_refusal"] = value
            closed = control.lifecycle_observation(invalid, 2, False, False, False)
            assert closed["first_scan_refusal"] is None
            assert closed["supervisor_exit"] == 2 and closed["settled_complete"] is False
            assert "PRIVATE" not in json.dumps(closed)
        for count in (0, True, -1, 65536, "PRIVATE_COUNT"):
            invalid = copy.deepcopy(events)
            invalid[-1]["counts"]["scan_refused"] = count
            assert (
                control.lifecycle_observation(invalid, 2, False, False, False)["first_scan_refusal"]
                is None
            )
        for invalid in (events[:-1], [*events, copy.deepcopy(events[-1])]):
            assert (
                control.lifecycle_observation(invalid, 2, False, False, False)["first_scan_refusal"]
                is None
            )

        # Diagnostic-only task metadata retains refusal and never exports identities.
        for stage, operation in (
            ("child-open", 2),
            ("child-context-enter", 2),
            ("child-read", 3),
            ("child-parse", 4),
            ("child-context-exit", 2),
        ):
            for before, after in (
                (((123, "R"), "known"), (None, "absent")),
                (((123, "R"), "known"), ((123, "R"), "known")),
                (((123, "R"), "known"), ((456, "R"), "known")),
                (((123, "R"), "known"), ((123, "Z"), "known")),
                ((None, "unknown"), (None, "denied")),
                ((None, "invalid"), (None, "other")),
            ):
                task = supervisor.task_scan_observation(before, after, stage)
                diagnostic = {
                    "counts": {"scan_refused": 1},
                    "first_scan_refusal": {"s": 2, "k": 1, "o": operation, "b": 7},
                    "first_task_scan": task,
                }
                assert control.task_scan_observation(diagnostic) == task
                assert "123" not in json.dumps(task) and "456" not in json.dumps(task)
                for key, value in (
                    ("phase", []),
                    ("before_probe", {}),
                    ("before_known", 1),
                    ("same_task_start", 1),
                    ("classification", "PRIVATE"),
                    ("path", "PRIVATE"),
                ):
                    bad = copy.deepcopy(diagnostic)
                    bad["first_task_scan"][key] = value
                    assert control.task_scan_observation(bad) is None
        assert control.task_scan_observation({}) is None
        assert projected["first_task_scan"] == producer["first_task_scan"]

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


def test_cli_rejects_unbound_environment_before_any_execution(tmp_path, monkeypatch, capsys):
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

    # A provider-bound refusal must remain diagnostic evidence, never a PG witness.
    binding = {
        "run": "123",
        "attempt": "1",
        "controller_head": "a" * 40,
        "controller_tree": "b" * 40,
        "nonce": "c" * 64,
        "repository": "tzeusy-org/butlers",
        "workflow": "Fact Source Capture BEFORE Control",
        "python": "3.12.15",
    }
    real_binding = control.provider_binding
    monkeypatch.setattr(control, "provider_binding", lambda root: copy.deepcopy(binding))
    monkeypatch.setattr(control.sys, "argv", ["control"])
    monkeypatch.setattr(control.signal, "signal", lambda *args: None)
    real_execute = control.execute
    cases = [
        ("workload-clone", subprocess.TimeoutExpired("PRIVATE_COMMAND", 30), "subprocess-timeout"),
        ("uv-install", ValueError("uv-install"), "value-error"),
        ("environment-prepare", OSError("PRIVATE_PATH"), "os-error"),
        ("aggregate-budget", KeyError("PRIVATE_ENV"), "missing-key"),
        ("PRIVATE_STAGE", ValueError("PRIVATE_PAYLOAD"), "value-error"),
    ]

    def refuse(root, output, progress=None):
        if progress is not None:
            progress["stage"] = stage
        raise error

    for index, (stage, _error, category) in enumerate(cases):
        error = _error
        directory = tmp_path / str(index)
        directory.mkdir()
        monkeypatch.setenv("RUNNER_TEMP", str(directory))

        monkeypatch.setattr(control, "execute", refuse)
        assert control.main() == 2
        evidence = directory / "fact-capture-before-evidence"
        data = json.loads((evidence / "control-refusal.json").read_text())
        assert data["schema"] == "fact-capture-control-refusal.v1"
        assert data["stage"] == (stage if stage != "PRIVATE_STAGE" else "unknown")
        assert data["error_class"] == category
        assert data["binding"] == binding
        assert data["complete"] is False and data["control_verdict"] == "UNKNOWN"
        assert data["pg_call"] == data["cleanup"] == "UNKNOWN"
        assert not (evidence / "summary.json").exists()
        with pytest.raises(ValueError):
            control.safe_phase(data, "before", binding)
        assert "PRIVATE" not in json.dumps(data) + capsys.readouterr().out

    # Execute the real prephase sequence with closed software dependencies. Neither
    # installer failure nor frozen-environment failure may reach the PG child.
    def install(*args):
        calls.append("installer")
        if stage == "uv-install":
            raise ValueError("uv-install")
        return {}

    def prepare(command, environment, seconds, *, cwd):
        calls.append("prepare")
        assert cwd.name == "workload" and command[-1] == "--prepare-child"
        assert Path(environment["FACT_CAPTURE_PREPARE_CONFIG"]).parent == cwd.parent
        return {
            "exit": 2,
            "cleanup_complete": True,
            "timed_out": False,
            "observation": control.lifecycle_observation([], 0, False, False, False),
        }

    for stage in ("uv-install", "environment-prepare"):
        directory = tmp_path / ("real-" + stage)
        directory.mkdir()
        root = directory / "controller"
        (root / "scripts").mkdir(parents=True)
        (root / "scripts/fact_source_capture_before_observer.py").write_text("# software fixture")
        progress = {}
        calls = []
        with monkeypatch.context() as context:
            context.setenv("FACT_CAPTURE_CONTROL_STARTED", str(control.time.monotonic()))
            context.setattr(control, "git", lambda *args, **kwargs: "")
            context.setattr(control, "workload_state", lambda *args: {})
            context.setattr(control, "overlay", lambda *args: None)
            context.setattr(
                control.subprocess,
                "run",
                lambda *args, **kwargs: subprocess.CompletedProcess([], 0),
            )

            context.setattr(control, "prepare_uv", install)
            context.setattr(control, "run_owned", prepare)
            # Any attempt to start the sentinel/PG child fails the software control.
            context.setattr(
                control.subprocess,
                "Popen",
                lambda *args, **kwargs: pytest.fail("refused prephase must not start a PG child"),
            )
            with pytest.raises(ValueError, match=stage):
                # Preserve the real execute, replaced only in the main() cases above.
                real_execute(root, directory, progress)
        assert progress["stage"] == stage
        assert calls == (["installer"] if stage == "uv-install" else ["installer", "prepare"])

        # A refusal after execute starts must retain its execution nonce and source
        # bindings, rather than main's earlier provider-only preflight binding.
        execution_binding = {**binding, "nonce": "d" * 64}
        bindings = iter((copy.deepcopy(binding), copy.deepcopy(execution_binding)))
        with monkeypatch.context() as context:
            context.setenv("RUNNER_TEMP", str(directory))
            context.setenv("FACT_CAPTURE_CONTROL_STARTED", str(control.time.monotonic()))
            context.setattr(control, "__file__", str(root / "scripts/control.py"))
            context.setattr(control, "execute", real_execute)
            context.setattr(
                control, "provider_binding", lambda root, bindings=bindings: next(bindings)
            )
            context.setattr(control, "git", lambda *args, **kwargs: "")
            context.setattr(
                control,
                "workload_state",
                lambda root, phase: {"phase": phase, "runtime_blob": "e" * 40},
            )
            context.setattr(control, "overlay", lambda *args: None)
            context.setattr(
                control.subprocess,
                "run",
                lambda *args, **kwargs: subprocess.CompletedProcess([], 0),
            )
            context.setattr(control, "prepare_uv", install)
            context.setattr(control, "run_owned", prepare)
            context.setattr(
                control.subprocess,
                "Popen",
                lambda *args, **kwargs: pytest.fail("refused prephase must not start a PG child"),
            )
            assert control.main() == 2
        evidence = directory / "fact-capture-before-evidence"
        data = json.loads((evidence / "control-refusal.json").read_text())
        assert data["binding"]["nonce"] == execution_binding["nonce"]
        assert data["binding"]["workload"] == {"phase": "after", "runtime_blob": "e" * 40}
        assert data["binding"]["phase_states"] == {
            name: {"phase": name, "runtime_blob": "e" * 40} for name in ("before", "after")
        }
        assert data["stage"] == stage and data["complete"] is False
        assert data["pg_call"] == data["cleanup"] == "UNKNOWN"
        assert not (evidence / "summary.json").exists()
        assert "PRIVATE" not in json.dumps(data) + capsys.readouterr().out

    # Controller mismatches remain fail-closed, but expose only which predicate failed.
    with monkeypatch.context() as context:
        context.setenv("GITHUB_REPOSITORY", "tzeusy-org/butlers")
        context.setenv("GITHUB_EVENT_NAME", "pull_request")
        context.setenv("GITHUB_WORKFLOW", "Fact Source Capture BEFORE Control")
        context.setenv("GITHUB_RUN_ID", "123")
        context.setenv("GITHUB_RUN_ATTEMPT", "1")
        context.setenv("GITHUB_SHA", "a" * 40)
        context.setenv("GITHUB_WORKFLOW_SHA", "a" * 40)
        context.setenv(
            "GITHUB_WORKFLOW_REF",
            "tzeusy-org/butlers/.github/workflows/"
            "fact-source-capture-before-control.yml@refs/pull/1/merge",
        )
        context.setattr(control.platform, "python_version", lambda: "3.12.15")
        context.setattr(control, "git", lambda root, *args: "" if args[0] == "status" else "a" * 40)
        assert real_binding(tmp_path)["run"] == "123"
        for key, reason in (
            ("GITHUB_SHA", "controller-sha"),
            ("GITHUB_WORKFLOW_SHA", "controller-workflow-sha"),
            ("GITHUB_WORKFLOW_REF", "controller-workflow-ref"),
        ):
            with monkeypatch.context() as mismatch:
                mismatch.setenv(key, "PRIVATE_MISMATCH")
                with pytest.raises(ValueError, match=reason) as caught:
                    real_binding(tmp_path)
                data = control.refusal(caught.value, {"stage": "controller-identity"}, None)
                assert data["reason"] == reason and data["binding"] is None
                assert "PRIVATE" not in json.dumps(data)
        context.setattr(control.platform, "python_version", lambda: "wrong")
        with pytest.raises(ValueError, match="controller-python"):
            real_binding(tmp_path)
        context.setattr(control.platform, "python_version", lambda: "3.12.15")
        context.setattr(
            control, "git", lambda root, *args: "PRIVATE_PATH" if args[0] == "status" else "a" * 40
        )
        with pytest.raises(ValueError, match="controller-dirty"):
            real_binding(tmp_path)

    # A pre-existing output directory is never overwritten or adopted.
    directory = tmp_path / "existing"
    output = directory / "fact-capture-before-evidence"
    output.mkdir(parents=True)
    marker = output / "summary.json"
    marker.write_text("unchanged")
    monkeypatch.setenv("RUNNER_TEMP", str(directory))
    assert control.main() == 2
    assert marker.read_text() == "unchanged"
    assert not (output / "control-refusal.json").exists()

    # Execute the pinned helper unchanged, directly and through the observer.
    # Only uv is a software fixture: no install, network, provider or PG call.
    helper_bytes = (Path(control.__file__).parent / "ci_environment.py").read_bytes()
    assert control.hashlib.sha256(helper_bytes).hexdigest() == control.PREPARE_HELPER_SHA256
    uv_fixture = """#!/usr/bin/env python3
import os,pathlib,subprocess,sys
mode=os.environ['FIXTURE_MODE']
if sys.argv[1:]==['--version']:
 print('PRIVATE_VERSION_ERROR' if mode=='compatibility-refused' else 'uv software fixture')
 sys.exit(6 if mode=='compatibility-refused' else 0)
if sys.argv[1:]==['lock','--check']:
 print('PRIVATE_LOCK_OUTPUT',file=sys.stderr)
 sys.exit(7 if mode=='lock-refused' else 0)
assert sys.argv[1:]==['sync','--frozen','--dev','--compile-bytecode','--reinstall-package','butlers']
if mode=='sync-refused':
 print('PRIVATE_SYNC_OUTPUT',file=sys.stderr);sys.exit(8)
root=pathlib.Path.cwd()
subprocess.run([sys.executable,'-m','venv','--without-pip',str(root/'.venv')],check=True)
site=next((root/'.venv/lib').glob('python*/site-packages'))
target=root/'src' if mode!='own-source-refused' else root/'outside'
(site/'fixture.pth').write_text(str(target)+'\\n')
"""
    for mode, substage in (
        ("healthy", "metadata-write"),
        ("compatibility-refused", "uv-version"),
        ("lock-refused", "lock-check"),
        ("sync-refused", "frozen-sync"),
        ("own-source-refused", "own-source"),
    ):
        executions = []
        for observed in (False, True):
            parent = tmp_path / (mode + str(observed))
            workload = parent / "workload"
            helper = workload / "scripts/ci_environment.py"
            helper.parent.mkdir(parents=True)
            helper.write_bytes(helper_bytes)
            for name in ("uv.lock", "pyproject.toml"):
                (workload / name).write_text("software fixture\n")
            for source in ("src", "outside"):
                (workload / source / "butlers").mkdir(parents=True)
                (workload / source / "butlers/__init__.py").write_text("# fixture\n")
            binary = workload / "bin/uv"
            binary.parent.mkdir()
            binary.write_text(uv_fixture)
            binary.chmod(0o755)
            config = parent / "prepare-config.json"
            config.write_text(json.dumps({"root": str(workload)}))
            command = (
                [sys.executable, str(Path(control.__file__).resolve()), "--prepare-child"]
                if observed
                else [sys.executable, str(helper), "prepare"]
            )
            result = subprocess.run(
                command,
                cwd=workload,
                env={
                    **os.environ,
                    "FIXTURE_MODE": mode,
                    "FACT_CAPTURE_PREPARE_CONFIG": str(config),
                    "PATH": str(binary.parent) + os.pathsep + os.environ["PATH"],
                },
                capture_output=True,
                timeout=40,
            )
            metadata = workload / ".venv/ci-environment.json"
            executions.append(
                (
                    result.returncode,
                    result.stdout,
                    result.stderr,
                    metadata.read_bytes() if metadata.is_file() else None,
                )
            )
            assert helper.read_bytes() == helper_bytes
        assert executions[0] == executions[1]
        assert result.returncode == (0 if mode == "healthy" else 1)
        diagnostic_path = parent / "prepare-observation.json"
        data = control.preparation_observation(diagnostic_path)
        assert data["substage"] == substage
        assert data["error_class"] == ("none" if mode == "healthy" else "subprocess-error")
        assert "PRIVATE" not in diagnostic_path.read_text()
        for key, value in (
            ("substage", "PRIVATE_STAGE"),
            ("helper_exit", True),
            ("helper_sha256", "wrong"),
            ("error_class", "PRIVATE_ERROR"),
        ):
            bad = {**data, key: value}
            diagnostic_path.write_text(json.dumps(bad))
            assert control.preparation_observation(diagnostic_path) is None

    # Refuse before import: fixed-name preload, foreign/missing origin, and
    # timestamp-valid bytecode (including an active symlink/legacy location).
    import importlib.util
    import py_compile
    from types import ModuleType, SimpleNamespace

    for mode in ("preloaded", "wrong-origin", "missing-spec", "cache", "cache-symlink", "legacy"):
        parent = tmp_path / ("import-" + mode)
        helper = parent / "workload/scripts/ci_environment.py"
        helper.parent.mkdir(parents=True)
        helper.write_bytes(helper_bytes)
        config = parent / "prepare-config.json"
        config.write_text(json.dumps({"root": str(helper.parents[1])}))
        marker = parent / "executed"
        expected = "prepare-helper-cache"
        if mode in ("cache", "cache-symlink", "legacy"):
            active = Path(importlib.util.cache_from_source(str(helper)))
            active.parent.mkdir()
            source = parent / "cache-source.py"
            text = f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\ndef main(): return 0\n"
            source.write_text(text + "#" * (len(helper_bytes) - len(text.encode())))
            stamp = helper.stat()
            os.utime(source, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
            target = parent / "cache.pyc" if mode == "cache-symlink" else active
            if mode == "legacy":
                target = helper.with_suffix(".pyc")
            py_compile.compile(str(source), cfile=str(target), dfile=str(helper), doraise=True)
            if mode == "cache-symlink":
                active.symlink_to(target)
            # A fresh interpreter exercises actual Python's cache selection.
            result = subprocess.run(
                [sys.executable, str(Path(control.__file__).resolve()), "--prepare-child"],
                cwd=helper.parents[1],
                env={**os.environ, "FACT_CAPTURE_PREPARE_CONFIG": str(config)},
                capture_output=True,
                timeout=10,
            )
            assert not marker.exists()
            assert result.returncode != 0
            assert expected.encode() in result.stderr
        else:
            with monkeypatch.context() as context:
                context.setenv("FACT_CAPTURE_PREPARE_CONFIG", str(config))
                context.setattr(sys, "path", list(sys.path))
                context.setattr(sys, "argv", ["control", "--prepare-child"])
                if mode == "preloaded":
                    context.setitem(sys.modules, "ci_environment", ModuleType("ci_environment"))
                    expected = "prepare-helper-preloaded"
                else:
                    context.setattr(
                        control.util,
                        "find_spec",
                        lambda name, mode=mode, marker=marker: (
                            None
                            if mode == "missing-spec"
                            else SimpleNamespace(
                                origin=str(marker), submodule_search_locations=None
                            )
                        ),
                    )
                    expected = "prepare-helper-origin"
                context.setattr(
                    control, "import_module", lambda name: pytest.fail("refused import executed")
                )
                with pytest.raises(ValueError, match=expected):
                    control.preparation_child()
                assert not marker.exists()
