"""The real ratchet's Counter, parser, process and source-publication boundaries."""

from __future__ import annotations

import copy
import importlib.util
import json
import signal
import sys
from collections import Counter
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def runtime(monkeypatch):
    account = _load("check_pyright_baseline")
    monkeypatch.setitem(sys.modules, "check_pyright_baseline", account)
    return _load("pyright_runtime")


def _report(root, *, severity="error", rule="reportAssignmentType", message="synthetic sentinel"):
    path = root / "src" / "sample.py"
    path.parent.mkdir(exist_ok=True)
    path.write_text('number: int = "string"\n')
    diagnostic = dict(
        file=str(path),
        message=message,
        severity=severity,
        range=dict(start=dict(line=0, character=0), end=dict(line=0, character=1)),
    )
    if rule is not ...:
        diagnostic["rule"] = rule
    summary = dict(filesAnalyzed=1, errorCount=0, warningCount=0, informationCount=0, timeInSec=0.1)
    summary[severity + "Count"] = 1
    return dict(
        version="1.1.414", time="synthetic", generalDiagnostics=[diagnostic], summary=summary
    )


def _bytes(value):
    return json.dumps(value).encode()


def _baseline(errors):
    return _bytes(dict(format=1, checker="1.1.414", errors=dict(errors)))


@pytest.mark.parametrize(
    "transition",
    [
        "unchanged",
        "new",
        "duplicate",
        "count-neutral",
        "amnesty",
        "stale-removal",
        "exact-shrink",
        "equal-key-replacement",
        "optional-rule",
        "null-rule",
    ],
)
def test_counter_ratchet_preserves_multiplicity_and_exact_shrink(runtime, tmp_path, transition):
    account = runtime.account
    report = _report(tmp_path)
    if transition == "optional-rule":
        del report["generalDiagnostics"][0]["rule"]
    elif transition == "null-rule":
        report["generalDiagnostics"][0]["rule"] = None
    trusted = account.errors(_bytes(report), tmp_path, 1)
    current = trusted.copy()
    committed = trusted.copy()
    key = next(iter(trusted))
    changed = copy.deepcopy(report)
    changed["generalDiagnostics"][0]["message"] = "different synthetic identity"
    different = account.errors(_bytes(changed), tmp_path, 1)
    if transition == "new":
        current += different
    elif transition == "duplicate":
        current[key] += 1
    elif transition == "count-neutral":
        current = different
    elif transition == "amnesty":
        current += different
        committed = current.copy()
    elif transition == "stale-removal":
        current.clear()
    elif transition == "exact-shrink":
        current.clear()
        committed.clear()
    # Same tuple/multiplicity is deliberately indistinguishable; this is not location tracking.
    restored = account.baseline(_baseline(committed))
    if transition in {"new", "duplicate", "count-neutral", "amnesty", "stale-removal"}:
        with pytest.raises(ValueError, match="baseline-stale-or-growth"):
            account.compare(current, restored, trusted)
    else:
        account.compare(current, restored, trusted)
    assert "synthetic sentinel" not in _baseline(trusted).decode()
    assert str(tmp_path) not in _baseline(trusted).decode()


@pytest.mark.parametrize(
    "poison",
    [
        "truncated",
        "invalid-utf8",
        "nonobject",
        "duplicate",
        "nan",
        "version",
        "extra",
        "negative-count",
        "bool-count",
        "summary-mismatch",
        "bad-range",
        "bad-message",
        "bad-rule",
        "relative-parent",
        "outside",
        "missing-file",
        "exit-zero-with-error",
        "exit-one-without-error",
        "fatal",
        "signal",
        "baseline-count-zero",
        "baseline-count-bool",
        "baseline-format-bool",
        "baseline-extra",
        "baseline-duplicate",
        "key-parent",
        "key-dot",
        "key-empty",
        "key-absolute",
        "key-rule",
        "key-digest",
        "key-noncanonical",
    ],
)
def test_strict_reports_and_baselines_refuse_incomplete_or_ambiguous_input(
    runtime, tmp_path, poison
):
    account = runtime.account
    report = _report(tmp_path)
    code = 1
    if poison.startswith("baseline-") or poison.startswith("key-"):
        entries = account.errors(_bytes(report), tmp_path, 1)
        value = json.loads(_baseline(entries))
        key = next(iter(entries))
        if poison.startswith("key-"):
            parts = json.loads(key)
            replacements = {
                "key-parent": "src/../bad.py",
                "key-dot": "src/./bad.py",
                "key-empty": "src//bad.py",
                "key-absolute": "/src/bad.py",
            }
            parts[0] = replacements.get(poison, parts[0])
            if poison == "key-rule":
                parts[1] = ""
            if poison == "key-digest":
                parts[2] = "not-a-digest"
            replacement = json.dumps(parts)
            if poison == "key-noncanonical":
                replacement = json.dumps(parts, separators=(",", ":"))
            value["errors"] = {replacement: 1}
        elif poison == "baseline-count-zero":
            value["errors"][key] = 0
        elif poison == "baseline-count-bool":
            value["errors"][key] = True
        elif poison == "baseline-format-bool":
            value["format"] = True
        elif poison == "baseline-extra":
            value["extra"] = "synthetic"
        raw = _bytes(value)
        if poison == "baseline-duplicate":
            raw = raw.replace(b'"format": 1', b'"format": 1, "format": 1')
        with pytest.raises((ValueError, TypeError)):
            account.baseline(raw)
        return
    if poison == "version":
        report["version"] = "wrong"
    elif poison == "extra":
        report["unrecognized"] = 1
    elif poison == "negative-count":
        report["summary"]["filesAnalyzed"] = -1
    elif poison == "bool-count":
        report["summary"]["filesAnalyzed"] = True
    elif poison == "summary-mismatch":
        report["summary"]["errorCount"] = 0
    elif poison == "bad-range":
        report["generalDiagnostics"][0]["range"]["start"]["line"] = False
    elif poison == "bad-message":
        report["generalDiagnostics"][0]["message"] = None
    elif poison == "bad-rule":
        report["generalDiagnostics"][0]["rule"] = ""
    elif poison in {"outside", "relative-parent", "missing-file"}:
        report["generalDiagnostics"][0]["file"] = {
            "outside": "/etc/hosts",
            "relative-parent": "../outside.py",
            "missing-file": str(tmp_path / "absent.py"),
        }[poison]
    elif poison == "exit-zero-with-error":
        code = 0
    elif poison == "exit-one-without-error":
        report["generalDiagnostics"] = []
        report["summary"]["errorCount"] = 0
    elif poison in {"fatal", "signal"}:
        code = {"fatal": 2, "signal": -signal.SIGTERM}[poison]
    raw = _bytes(report)
    if poison == "invalid-utf8":
        raw = b"\xff"
    elif poison == "truncated":
        raw = raw[:-1]
    elif poison == "nonobject":
        raw = b"[]"
    elif poison == "duplicate":
        raw = raw.replace(b'"version":', b'"version": "1.1.414", "version":', 1)
    elif poison == "nan":
        raw = raw.replace(b'"timeInSec": 0.1', b'"timeInSec": NaN')
    with pytest.raises((ValueError, TypeError, OSError)):
        account.errors(raw, tmp_path, code)


@pytest.mark.parametrize(
    "mode", ["normal", "nonzero", "timeout", "overflow", "signal", "holder", "resistant"]
)
def test_actual_owned_process_capture_is_finite_and_private(
    runtime, tmp_path, monkeypatch, request, mode
):
    program = {
        "normal": 'print("synthetic output")',
        "nonzero": "raise SystemExit(2)",
        "timeout": "import time;time.sleep(30)",
        "resistant": "import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(30)",
        "overflow": 'print("x"*4096)',
        "signal": "import os,signal;os.kill(os.getpid(), signal.SIGTERM)",
        "holder": "import os,time;pid=os.fork();time.sleep(30) if pid==0 else None",
    }[mode]
    owned = []
    real_popen = runtime.subprocess.Popen

    def start(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        owned.append(process)
        return process

    def cleanup():
        for process in owned:
            if runtime.live_group(process.pid):
                runtime.os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=2)

    request.addfinalizer(cleanup)
    monkeypatch.setattr(runtime.subprocess, "Popen", start)
    if mode in {"timeout", "overflow", "holder", "resistant"}:
        with pytest.raises(runtime.Refusal) as caught:
            runtime.capture(
                [sys.executable, "-I", "-c", program],
                cwd=tmp_path,
                environment={},
                timeout=0.15,
                limit=1024,
            )
        assert str(caught.value) in {"timeout", "output-limit", "unfinished-child"}
    else:
        code, output = runtime.capture(
            [sys.executable, "-I", "-c", program],
            cwd=tmp_path,
            environment={},
            timeout=2,
            limit=1024,
        )
        assert code == {"normal": 0, "nonzero": 2, "signal": -signal.SIGTERM}[mode]
        assert output == (b"synthetic output\n" if mode == "normal" else b"")
    assert len(owned) == 1 and owned[0].poll() is not None
    assert not runtime.live_group(owned[0].pid)


def _checkout(root):
    for name in ("src", "roster", "scripts"):
        (root / name).mkdir()
    for name in ("pyright_runtime.py", "check_pyright_baseline.py"):
        (root / "scripts" / name).write_text("synthetic wiring")
    (root / "pyproject.toml").write_text("synthetic config")
    (root / "uv.lock").write_text("synthetic lock")


@pytest.mark.parametrize(
    "change",
    [
        "none",
        "source",
        "configuration",
        "wiring",
        "checker",
        "base",
        "baseline",
        "concurrent",
        "growth",
        "stale",
        "shrink",
        "local-regrowth",
        "root-stub",
        "root-module",
        "root-namespace",
        "root-typed",
        "root-mode",
    ],
)
def test_real_runner_refuses_snapshot_races_and_publishes_only_exact_shrink(
    runtime, tmp_path, monkeypatch, capsys, change
):
    _checkout(tmp_path)
    current = runtime.account.errors(_bytes(_report(tmp_path)), tmp_path, 1)
    trusted_baseline = _baseline(current)
    original = _baseline(Counter()) if change == "local-regrowth" else trusted_baseline
    path = tmp_path / "pyright-baseline.json"
    path.write_bytes(original)
    if change.startswith("root-"):
        (tmp_path / "typings/probe_dependency").mkdir(parents=True)
        (tmp_path / "typings/probe_dependency/__init__.pyi").write_text("value: int\n")
        (tmp_path / "root_dependency.py").write_text("value = 1\n")
        (tmp_path / "namespace_dependency").mkdir()
        (tmp_path / "namespace_dependency/py.typed").write_text("")
    identity = {"checker": "1.1.414"}
    monkeypatch.setattr(runtime, "prerequisites", lambda root: ({}, identity.copy()))
    monkeypatch.setattr(runtime, "trusted_base", lambda root: "a" * 40)
    monkeypatch.setattr(runtime, "initial", lambda *args: False)
    monkeypatch.setattr(
        runtime,
        "git",
        lambda *args: (
            trusted_baseline if args[-1].endswith(":pyright-baseline.json") else b"a" * 40
        ),
    )
    held_lock = None
    if change == "concurrent":
        import fcntl

        (tmp_path / ".tmp").mkdir()
        held_lock = (tmp_path / ".tmp/pyright-update.lock").open("a")
        fcntl.flock(held_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def compiler(root, env):
        if change == "root-stub":
            (root / "typings/probe_dependency/__init__.pyi").write_text("value: str\n")
        elif change == "root-module":
            (root / "root_dependency.py").write_text("value = 'changed'\n")
        elif change == "root-namespace":
            (root / "untracked_namespace").mkdir()
        elif change == "root-typed":
            (root / "namespace_dependency/py.typed").unlink()
        elif change == "root-mode":
            (root / "root_dependency.py").chmod(0o755)
        elif change == "source":
            (root / "src/sample.py").write_text("changed")
        elif change == "configuration":
            (root / "pyproject.toml").write_text("changed")
        elif change == "wiring":
            (root / "scripts/pyright_runtime.py").write_text("changed")
        elif change == "checker":
            identity["checker"] = "changed"
        elif change == "base":
            monkeypatch.setattr(runtime, "trusted_base", lambda root: "b" * 40)
        elif change == "baseline":
            path.write_bytes(b"concurrent published account")
        result = current.copy()
        if change == "growth":
            result[next(iter(result))] += 1
        elif change in {"shrink", "stale"}:
            result.clear()
        return result

    monkeypatch.setattr(runtime, "compiler", compiler)
    mode = "check" if change in {"none", "stale"} else "update"
    try:
        if change not in {"none", "shrink"}:
            with pytest.raises((runtime.Refusal, ValueError)):
                runtime.run(tmp_path, mode)
            assert path.read_bytes() == (
                b"concurrent published account" if change == "baseline" else original
            )
        else:
            runtime.run(tmp_path, mode)
            runtime.run(tmp_path, "check")
            assert runtime.account.baseline(path.read_bytes()) == (
                Counter() if change == "shrink" else current
            )
        assert "synthetic sentinel" not in capsys.readouterr().out
        assert not list(tmp_path.glob(".pyright-baseline-*"))
    finally:
        if held_lock:
            held_lock.close()


def test_installed_command_and_ci_preserve_a4_and_readable_fallback(tmp_path):
    import yaml

    makefile = (ROOT / "Makefile").read_text()
    lint = makefile.split("lint:\n", 1)[1].split("\n\n", 1)[0]
    assert "uv run ruff check src/ tests/ roster/ scripts/" in lint
    assert "uv run --no-sync lint-imports" in lint
    assert "secrets-scan lint-workflows lint-tools-self-test" in lint
    assert "$(MAKE) --no-print-directory typecheck" in lint
    jobs = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())["jobs"]
    steps = jobs["guards"]["steps"]
    lint_index = next(i for i, step in enumerate(steps) if step.get("id") == "lint")
    assert steps[lint_index]["run"] == "make lint"
    assert steps[lint_index - 1]["run"] == "make prepare-typecheck"
    assert len((ROOT / "scripts/check_pyright_baseline.py").read_text().splitlines()) < 80
    # Exercise the real Make recipe with closed spies: even its unchanged uv invocation
    # inherits no-sync, so ordinary lint cannot fetch/install the newly pinned checker.
    import os
    import subprocess

    binary = tmp_path / "bin"
    binary.mkdir()
    (tmp_path / ".venv/bin").mkdir(parents=True)
    record = tmp_path / "commands.jsonl"
    spy = """#!/usr/bin/python3
import json, os, sys
with open(os.environ['RECORD'], 'a') as output:
    output.write(json.dumps([os.path.basename(sys.argv[0]), sys.argv[1:],
                             os.environ.get('UV_NO_SYNC')]) + '\\n')
"""
    for target in (binary / "uv", binary / "python3", tmp_path / ".venv/bin/python"):
        target.write_text(spy)
        target.chmod(0o755)
    (tmp_path / "Makefile").write_bytes((ROOT / "Makefile").read_bytes())
    result = subprocess.run(
        ["make", "lint"],
        cwd=tmp_path,
        env={"PATH": str(binary) + os.pathsep + os.defpath, "RECORD": str(record)},
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0
    commands = [json.loads(line) for line in record.read_text().splitlines()]
    assert [row[1] for row in commands[:2]] == [
        ["run", "ruff", "check", "src/", "tests/", "roster/", "scripts/"],
        ["run", "--no-sync", "lint-imports"],
    ]
    assert all(row[2] == "1" for row in commands)
    assert commands[-1][1] == ["-B", "scripts/pyright_runtime.py", "check"]


@pytest.mark.parametrize("kind", ["tool", "bundle", "node", "scope", "environment"])
def test_missing_or_mismatched_prerequisites_refuse_before_checker(
    runtime, tmp_path, monkeypatch, kind
):
    # These exercise the real prerequisite consumer, without installing or invoking a tool.
    root = tmp_path / "checkout"
    root.mkdir()
    (root / ".venv/bin").mkdir(parents=True)
    (root / ".venv/bin/python").touch()
    config = runtime.CONFIG.copy()
    if kind == "scope":
        config["exclude"] = ["roster"]
    monkeypatch.setattr(
        runtime.tomllib,
        "loads",
        lambda text: (
            {"package": [{"name": "pyright", "version": "1.1.414"}]}
            if text == "lock"
            else {"tool": {"pyright": config}}
        ),
    )
    (root / "uv.lock").write_text("lock")
    (root / "pyproject.toml").write_text("config")
    monkeypatch.setattr(
        runtime,
        "capture",
        lambda *args, **kwargs: (0, (str(root / "src/butlers/__init__.py") + "\n").encode()),
    )
    metadata = root / ".venv/lib/python3.12/site-packages/pyright-1.1.414.dist-info"
    metadata.mkdir(parents=True)

    class Distribution:
        files = []
        version = "wrong" if kind == "tool" else "1.1.414"

        def locate_file(self, name):
            return root / name

    monkeypatch.setattr(runtime.importlib.metadata.Distribution, "at", lambda path: Distribution())
    bundle = root / "pyright/dist"
    bundle.mkdir(parents=True)
    (bundle / "index.js").touch()
    (bundle / "package.json").write_text('{"version":"1.1.414"}')
    if kind == "bundle":
        (bundle / "index.js").unlink()
    monkeypatch.setattr(runtime.shutil, "which", lambda name: None)
    if kind == "environment":
        (root / ".venv/bin/python").unlink()
    with pytest.raises(runtime.Refusal) as caught:
        runtime.prerequisites(root)
    assert (
        str(caught.value)
        == {
            "tool": "checker-version",
            "bundle": "checker-bundle",
            "node": "node-unavailable",
            "scope": "scope-config",
            "environment": "own-environment",
        }[kind]
    )


@pytest.mark.parametrize(
    "event",
    [
        "pull_request",
        "merge_group",
        "push",
        "schedule",
        "workflow_dispatch",
        "unsupported",
        "checkout-mismatch",
        "malformed",
    ],
)
def test_public_authority_is_actual_frozen_ci_identity(runtime, tmp_path, monkeypatch, event):
    head, base = "a" * 40, "b" * 40
    payload = {
        "pull_request": {"base": {"sha": base}},
        "merge_group": {"base_sha": base},
        "before": base,
    }
    if event == "malformed":
        payload["pull_request"]["base"]["sha"] = "../arbitrary-source"
    path = tmp_path / "event.json"
    path.write_text(json.dumps(payload))
    name = "pull_request" if event in {"checkout-mismatch", "malformed"} else event
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_EVENT_NAME", name)
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(path))
    monkeypatch.setenv("GITHUB_SHA", head)
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    monkeypatch.setattr(
        runtime,
        "git",
        lambda root, *args: (
            (("c" * 40 if event == "checkout-mismatch" else head) + "\n").encode()
            if args[0] == "rev-parse"
            else b""
        ),
    )
    if event in {"unsupported", "checkout-mismatch", "malformed"}:
        with pytest.raises(runtime.Refusal):
            runtime.trusted_base(tmp_path)
    else:
        assert runtime.trusted_base(tmp_path) == (
            head if event in {"schedule", "workflow_dispatch"} else base
        )


def test_refusal_never_prints_child_exception_or_poisoned_diagnostic(runtime, monkeypatch, capsys):
    sentinel = "synthetic private poison /absolute/unknown/path"

    def fail(*args):
        raise ValueError(sentinel)

    monkeypatch.setattr(runtime, "run", fail)
    monkeypatch.setattr(sys, "argv", ["pyright_runtime.py", "check"])
    assert runtime.main() == 1
    captured = capsys.readouterr()
    assert sentinel not in captured.out + captured.err
    assert captured.err == "pyright: refused (invalid-input-or-checker-output)\n"


@pytest.mark.parametrize("change", ["contents", "member", "mode"])
def test_public_archive_is_git_source_authority_not_a_prepared_marker(runtime, tmp_path, change):
    root, public = tmp_path / "checkout", tmp_path / "public"
    root.mkdir()
    public.mkdir()
    for directory in (root, public):
        for name in ("src", "roster"):
            (directory / name).mkdir()
        (directory / "src/sample.py").write_text("value = 1\n")
        (directory / "roster/.gitkeep").write_text("")
        (directory / "pyproject.toml").write_text("same configuration")
        (directory / "uv.lock").write_text("same locked environment")
    import subprocess

    def git(*args):
        return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)

    git("init", "-q")
    git("add", ".")
    git(
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-qm",
        "source",
    )
    base = git("rev-parse", "HEAD").stdout.decode().strip()
    runtime.verify_public(root, base, public)
    if change == "contents":
        (public / "src/sample.py").write_text("changed")
    elif change == "member":
        (public / "roster/unaccounted.py").write_text("changed")
    else:
        (public / "src/sample.py").chmod(0o755)
    with pytest.raises(runtime.Refusal, match="public-source-drift"):
        runtime.verify_public(root, base, public)


@pytest.mark.parametrize("transition", ["first", "deleted"])
def test_public_baseline_deletion_cannot_reopen_bootstrap(runtime, tmp_path, transition):
    import subprocess

    def git(*args):
        return subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)

    git("init", "-q")
    (tmp_path / "source.py").touch()
    git("add", ".")
    git(
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-qm",
        "source",
    )
    if transition == "deleted":
        (tmp_path / "pyright-baseline.json").write_text("old public baseline presence")
        git("add", ".")
        git(
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "account",
        )
        git("rm", "pyright-baseline.json")
        git(
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "delete",
        )
    base = git("rev-parse", "HEAD").stdout.decode().strip()
    if transition == "deleted":
        with pytest.raises(runtime.Refusal, match="deleted-public-baseline"):
            runtime.initial(tmp_path, base)
    else:
        assert runtime.initial(tmp_path, base)


def test_checker_runtime_override_is_neutralized_without_echo(runtime, tmp_path, monkeypatch):
    for key in (
        "PYRIGHT_PYTHON_PYLANCE_VERSION",
        "PYRIGHT_PYTHON_FORCE_VERSION",
        "PYRIGHT_PYTHON_USE_BUNDLED_PYRIGHT",
        "NODE_OPTIONS",
        "NODE_PATH",
        "PYTHONPATH",
    ):
        monkeypatch.setenv(key, "synthetic conflicting override")
    selected = runtime.environment(tmp_path / "node")
    assert selected["PYRIGHT_PYTHON_FORCE_VERSION"] == "1.1.414"
    assert selected["PYRIGHT_PYTHON_USE_BUNDLED_PYRIGHT"] == "1"
    assert selected["PYRIGHT_PYTHON_GLOBAL_NODE"] == "1"
    assert selected["PYRIGHT_PYTHON_NODEJS_WHEEL"] == "0"
    assert selected["PATH"].split(runtime.os.pathsep)[0] == str(tmp_path)
    assert all(
        key not in selected
        for key in ("NODE_OPTIONS", "NODE_PATH", "PYTHONPATH", "PYRIGHT_PYTHON_PYLANCE_VERSION")
    )


@pytest.mark.parametrize("poison", ["none", "wrapper", "javascript", "record", "empty"])
def test_installed_wrapper_and_javascript_are_both_bound_by_actual_records(
    runtime, tmp_path, poison
):
    import base64
    from types import SimpleNamespace

    class Entry:
        def __init__(self, name, data):
            self.name = name
            self.hash = SimpleNamespace(
                mode="sha256",
                value=base64.urlsafe_b64encode(runtime.hashlib.sha256(data).digest())
                .decode()
                .rstrip("="),
            )

        def __str__(self):
            return self.name

    files = []
    for name in ("pyright/cli.py", "pyright/dist/index.js"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"closed synthetic package file")
        files.append(Entry(name, path.read_bytes()))
    distribution = SimpleNamespace(files=files, locate_file=lambda entry: tmp_path / str(entry))
    expected = runtime.bundle_identity(distribution)
    if poison == "wrapper":
        (tmp_path / "pyright/cli.py").write_bytes(b"changed wrapper")
    elif poison == "javascript":
        (tmp_path / "pyright/dist/index.js").write_bytes(b"changed JavaScript")
    elif poison == "record":
        files[0].hash = None
    elif poison == "empty":
        distribution.files = []
    if poison == "none":
        assert runtime.bundle_identity(distribution) == expected
    else:
        with pytest.raises(runtime.Refusal, match="checker-record"):
            runtime.bundle_identity(distribution)


@pytest.mark.parametrize(
    "change",
    [
        "stub",
        "module",
        "namespace",
        "typed",
        "directory",
        "mode",
        "file-link",
        "directory-link",
        "compiled-so",
        "compiled-pyd",
        "compiled-dylib",
        "compiled-pyc",
        "cache-module",
    ],
)
def test_native_root_inputs_are_bound_to_snapshot_and_public_git(runtime, tmp_path, change):
    import subprocess

    root, public = tmp_path / "checkout", tmp_path / "public"
    for directory in (root, public):
        directory.mkdir()
        _checkout(directory)
        (directory / "src/sample.py").write_text("value = 1\n")
        (directory / "roster/.gitkeep").write_text("")
        (directory / "typings/probe_dependency").mkdir(parents=True)
        (directory / "typings/probe_dependency/__init__.pyi").write_text("value: int\n")
        (directory / "root_dependency.py").write_text("value = 1\n")
        (directory / "namespace_dependency").mkdir()
        (directory / "namespace_dependency/member.py").write_text("value = 1\n")
        (directory / "namespace_dependency/py.typed").write_text("")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "native inputs",
        ],
        check=True,
    )
    base = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"]).decode().strip()
    runtime.verify_public(root, base, public)
    before = runtime.snapshot(public, include_environment=True)
    if change == "stub":
        (public / "typings/probe_dependency/__init__.pyi").write_text("value: str\n")
    elif change == "module":
        (public / "root_dependency.py").write_text("value = 'changed'\n")
    elif change == "namespace":
        (public / "namespace_dependency/untracked.pyi").write_text("value: str\n")
    elif change == "typed":
        (public / "namespace_dependency/py.typed").unlink()
    elif change == "directory":
        (public / "untracked_namespace").mkdir()
    elif change == "mode":
        (public / "root_dependency.py").chmod(0o755)
    elif change == "file-link":
        (public / "root_dependency.py").unlink()
        (public / "root_dependency.py").symlink_to(root / "root_dependency.py")
    elif change == "cache-module":
        (public / "__pycache__").mkdir()
        (public / "__pycache__/probe.py").write_text("value: str = 'changed'\n")
    elif change.startswith("compiled-"):
        (public / ("extension_dependency." + change.removeprefix("compiled-"))).write_bytes(
            b"synthetic nonexecuted native import presence"
        )
    else:
        (public / "linked_namespace").symlink_to(
            root / "namespace_dependency", target_is_directory=True
        )
    if change.endswith("link"):
        with pytest.raises(runtime.Refusal, match="source-link"):
            runtime.snapshot(public, include_environment=True)
    else:
        assert runtime.snapshot(public, include_environment=True) != before
    with pytest.raises(runtime.Refusal, match="public-source-drift|source-link"):
        runtime.verify_public(root, base, public)


@pytest.mark.parametrize(
    "target", ["source", "missing", "external", "hidden", "env-directory", "env-mode", "env-link"]
)
def test_literal_pth_paths_stay_inside_the_captured_input_closure(runtime, tmp_path, target):
    _checkout(tmp_path)
    site = tmp_path / ".venv/lib/python3.12/site-packages"
    site.mkdir(parents=True)
    if target.startswith("env-"):
        carrier = site / "bound.py"
        carrier.write_text("value = 1\n")
        before = runtime.snapshot(tmp_path, include_environment=True)
        if target == "env-directory":
            (site / "new_namespace").mkdir()
        elif target == "env-mode":
            carrier.chmod(0o755)
        else:
            external = tmp_path.parent / "external-native-env"
            external.mkdir(exist_ok=True)
            (site / "unbound_namespace").symlink_to(external, target_is_directory=True)
            with pytest.raises(runtime.Refusal, match="source-link"):
                runtime.snapshot(tmp_path, include_environment=True)
            return
        assert runtime.snapshot(tmp_path, include_environment=True) != before
        return
    destination = {
        "source": tmp_path / "src",
        "missing": tmp_path / "missing",
        "external": tmp_path.parent / "external-native-input",
        "hidden": tmp_path / ".hidden-native-input",
    }[target]
    if target != "missing":
        destination.mkdir(exist_ok=True)
    (site / "probe.pth").write_text(str(destination) + "\n")
    if target in {"external", "hidden"}:
        with pytest.raises(runtime.Refusal, match="external-import-path"):
            runtime.snapshot(tmp_path, include_environment=True)
    else:
        assert runtime.snapshot(tmp_path, include_environment=True)
