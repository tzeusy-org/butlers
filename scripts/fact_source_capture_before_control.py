"""Finite, separately scoped hosted old-runtime PostgreSQL witness control.

No command/selector/revision inputs. Local software controls are not a PG witness.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import resource
import secrets
import selectors
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

CANDIDATE = "9b46e8e7342411cf3e8f2b75c139d077509233b0"
CANDIDATE_TREE = "bf80be4ed461cd68c8d4dc2b63a3bf0ba2333782"
OLD_COMMIT = "37d3917c38080a0e43aa73d8f114b715c2377568"
RUNTIME = "src/butlers/core/fact_authority.py"
OLD_BLOB = "6fb7494b993a459d58736d91d72c96382c728998"
NEW_BLOB = "1f3287e7583f31eb969ffcdc6d3eeb3a5204d271"
NODE = (
    "tests/integration/test_fact_source_capture_pool_liveness.py::"
    "test_capacity_one_registered_owner_capture_and_concurrent_sources"
)
SUPERVISOR = Path(__file__).resolve().parents[1] / (
    ".github/actions/ci-shard-watchdog-capture/src/supervisor.py"
)
PHASE_KEYS = {
    "schema",
    "binding",
    "phase",
    "collected",
    "starts",
    "phases",
    "exception",
    "nested_acquire",
    "owner_frame",
    "pool_capacity_one",
    "runtime_role",
    "registered_mcp",
    "lookup_reached",
    "capture_tasks_settled",
    "pool_reacquired",
    "durable_partial_reports",
    "memory_partial_reports",
    "process_cleanup",
    "sentinel_preserved",
    "pytest_exit",
    "terminal",
}
CANCELLED = []
AGGREGATE_DEADLINE = None


def digest(data):
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def atomic(path, value):
    wire = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    if len(wire) > 32768:
        raise ValueError("receipt-size")
    temporary = path.with_suffix(".pending")
    with temporary.open("xb") as stream:
        os.chmod(temporary, 0o600)
        stream.write(wire + b"\n")
    temporary.replace(path)


def git(root, *args, binary=False, env=None):
    environment = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null")
    if env is not None:
        environment["GIT_INDEX_FILE"] = env["GIT_INDEX_FILE"]
    timeout = (
        20 if AGGREGATE_DEADLINE is None else min(20, AGGREGATE_DEADLINE - time.monotonic() - 60)
    )
    if timeout <= 0 or CANCELLED:
        raise ValueError("aggregate-deadline")
    result = subprocess.check_output(
        ["git", "-c", "core.hooksPath=/dev/null", *args],
        cwd=root,
        env=environment,
        stderr=subprocess.DEVNULL,
        timeout=timeout,
    )
    return result if binary else result.decode().strip()


def overlay(root, phase):
    if phase not in ("before", "after"):
        raise ValueError("phase")
    blob = OLD_BLOB if phase == "before" else NEW_BLOB
    target = root / RUNTIME
    if target.is_symlink():
        raise ValueError("runtime-link")
    target.write_bytes(git(root, "cat-file", "blob", blob, binary=True))


def workload_state(root, phase):
    if git(root, "rev-parse", "HEAD") != CANDIDATE:
        raise ValueError("workload-head")
    if git(root, "rev-parse", "HEAD^{tree}") != CANDIDATE_TREE:
        raise ValueError("workload-tree")
    if git(root, "write-tree") != CANDIDATE_TREE:
        raise ValueError("workload-index")
    changes = git(root, "diff", "--no-ext-diff", "--name-only", "HEAD", "--").splitlines()
    expected = [RUNTIME] if phase == "before" else []
    if changes != expected or git(root, "ls-files", "--others", "--exclude-standard"):
        raise ValueError("workload-drift")
    blob = OLD_BLOB if phase == "before" else NEW_BLOB
    if (root / RUNTIME).is_symlink() or (root / RUNTIME).read_bytes() != git(
        root, "cat-file", "blob", blob, binary=True
    ):
        raise ValueError("runtime-blob")
    mode = git(root, "ls-tree", CANDIDATE, "--", RUNTIME).split()[0]
    if mode != "100644" or (root / RUNTIME).stat().st_mode & 0o111:
        raise ValueError("runtime-mode")
    manifest = git(root, "ls-tree", "-rz", CANDIDATE, binary=True)
    with tempfile.TemporaryDirectory(prefix="fact-control-index-") as directory:
        environment = {**os.environ, "GIT_INDEX_FILE": str(Path(directory) / "index")}
        git(root, "read-tree", CANDIDATE, env=environment)
        git(root, "update-index", "--cacheinfo", mode, blob, RUNTIME, env=environment)
        physical_tree = git(root, "write-tree", env=environment)
    return {
        "candidate": CANDIDATE,
        "candidate_tree": CANDIDATE_TREE,
        "physical_tree": physical_tree,
        "runtime_blob": blob,
        "runtime_sha256": hashlib.sha256((root / RUNTIME).read_bytes()).hexdigest(),
        "manifest_sha256": hashlib.sha256(manifest).hexdigest(),
        "lock_sha256": hashlib.sha256((root / "uv.lock").read_bytes()).hexdigest(),
        "differences": expected,
    }


def safe_phase(data, name, binding):
    if set(data) != PHASE_KEYS or data["schema"] != "fact-capture-pg-phase.v1":
        raise ValueError("phase-schema")
    if data["binding"] != binding or data["phase"] != name:
        raise ValueError("phase-binding")
    if (
        type(data["collected"]) is not int
        or not 0 <= data["collected"] <= 2
        or type(data["starts"]) is not int
        or not 0 <= data["starts"] <= 2
        or not isinstance(data["phases"], dict)
        or set(data["phases"]) - {"setup", "call", "teardown"}
        or any(v not in ("passed", "failed", "skipped") for v in data["phases"].values())
    ):
        raise ValueError("phase-values")
    if data["exception"] not in ("none", "TimeoutError", "other"):
        raise ValueError("exception-category")
    for key in (
        "nested_acquire",
        "owner_frame",
        "pool_capacity_one",
        "runtime_role",
        "registered_mcp",
        "capture_tasks_settled",
        "pool_reacquired",
        "process_cleanup",
        "sentinel_preserved",
        "terminal",
    ):
        if type(data[key]) is not bool:
            raise ValueError("phase-boolean")
    for key in ("durable_partial_reports", "memory_partial_reports", "pytest_exit"):
        if data[key] is not None and (type(data[key]) is not int or not 0 <= data[key] <= 255):
            raise ValueError("phase-number")
    if data["lookup_reached"] not in ("UNKNOWN", "OBSERVED"):
        raise ValueError("lookup-category")


def require_phase(data, name, binding):
    safe_phase(data, name, binding)
    if set(data) != PHASE_KEYS or data["schema"] != "fact-capture-pg-phase.v1":
        raise ValueError("phase-schema")
    if data["binding"] != binding or data["phase"] != name:
        raise ValueError("phase-binding")
    if type(data["collected"]) is not int or type(data["starts"]) is not int:
        raise ValueError("phase-count-type")
    if data["collected"] != 1 or data["starts"] != 1:
        raise ValueError("phase-population")
    needed = [
        "pool_capacity_one",
        "runtime_role",
        "registered_mcp",
        "capture_tasks_settled",
        "pool_reacquired",
        "process_cleanup",
        "sentinel_preserved",
        "terminal",
    ]
    if any(data[k] is not True for k in needed):
        raise ValueError("phase-incomplete")
    if type(data["durable_partial_reports"]) is not int or data["durable_partial_reports"] != 0:
        raise ValueError("partial-report")
    expected = {
        "setup": "passed",
        "call": "failed" if name == "before" else "passed",
        "teardown": "passed",
    }
    if (
        data["phases"] != expected
        or type(data["pytest_exit"]) is not int
        or data["pytest_exit"] != (1 if name == "before" else 0)
    ):
        raise ValueError("phase-outcome")
    if name == "before":
        if (
            data["exception"] != "TimeoutError"
            or data["nested_acquire"] is not True
            or data["owner_frame"] is not True
            or data["memory_partial_reports"] != 0
            or type(data["memory_partial_reports"]) is not int
        ):
            raise ValueError("before-not-known-red")
    elif data["exception"] != "none":
        raise ValueError("after-not-green")
    if data["lookup_reached"] not in ("UNKNOWN", "OBSERVED"):
        raise ValueError("lookup-reachability")


def require_witness(before, after, binding):
    require_phase(before, "before", binding)
    require_phase(after, "after", binding)
    return "WITNESS_COMPLETE"


def process_start(pid):
    raw = Path(f"/proc/{pid}/stat").read_text()
    return int(raw[raw.rfind(")") + 2 :].split()[19])


def run_owned(command, environment, seconds):
    """Use the existing confined subreaper; only its closed lifecycle crosses stdout."""
    started = time.monotonic()
    deadline = started + seconds
    process = subprocess.Popen(
        [
            sys.executable,
            str(SUPERVISOR),
            str(os.getpid()),
            str(process_start(os.getpid())),
            *command,
        ],
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    wire = bytearray()
    events = []
    stopped = False
    timed_out = False
    overflow = False
    cleanup_deadline = None
    try:
        while True:
            now = time.monotonic()
            if not stopped and (
                CANCELLED or now >= deadline or any(x.get("event") == "outcome" for x in events)
            ):
                timed_out = now >= deadline
                process.terminate()
                stopped = True
                cleanup_deadline = now + 10
            if stopped and now >= cleanup_deadline:
                process.kill()
                break
            for key, _ in selector.select(0.02):
                chunk = os.read(key.fileobj.fileno(), 4096)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                wire.extend(chunk)
                if len(wire) > 16384:
                    overflow = True
                    if not stopped:
                        process.terminate()
                        stopped = True
                        cleanup_deadline = now + 10
                    wire.clear()
                    continue
                while b"\n" in wire:
                    line, _, rest = wire.partition(b"\n")
                    wire[:] = rest
                    try:
                        value = json.loads(line)
                        if not isinstance(value, dict) or len(events) >= 8:
                            raise ValueError("lifecycle")
                        events.append(value)
                    except (ValueError, UnicodeError):
                        overflow = True
            if process.poll() is not None and not selector.get_map():
                break
        process.wait(timeout=5)
    finally:
        selector.close()
        process.stdout.close()
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
    outcomes = [x for x in events if x.get("event") == "outcome"]
    settled = [x for x in events if x.get("event") == "settled"]
    complete = (
        not overflow
        and not wire
        and len(outcomes) == len(settled) == 1
        and settled[0].get("complete") is True
        and process.returncode == 0
    )
    code = outcomes[0].get("code") if len(outcomes) == 1 else None
    return {
        "exit": code,
        "cleanup_complete": complete,
        "timed_out": timed_out,
        "cancelled": bool(CANCELLED),
        "elapsed_s": time.monotonic() - started,
    }


def prepare_uv(root, private, environment, seconds):
    """Invoke the unchanged installer under our aggregate and owned subreaper."""
    installed = run_owned(
        [sys.executable, str(root / "scripts/install_ci_uv.py")],
        {**environment, "GITHUB_PATH": str(private / "installer-path")},
        seconds,
    )
    if (
        installed["exit"] != 0
        or not installed["cleanup_complete"]
        or installed["timed_out"]
        or installed["cancelled"]
    ):
        raise ValueError("uv-install")
    return {
        **environment,
        "PATH": str(Path(environment["HOME"]) / ".local/bin")
        + os.pathsep
        + environment.get("PATH", ""),
    }


def child_environment():
    keep = ("PATH", "HOME", "LANG", "LC_ALL", "SSL_CERT_FILE", "SSL_CERT_DIR")
    return {
        **{key: os.environ[key] for key in keep if key in os.environ},
        "CI": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        "TESTCONTAINERS_RYUK_DISABLED": "true",
    }


def phase_child():
    config = json.loads(Path(os.environ["FACT_CAPTURE_CONTROL_CONFIG"]).read_text())
    root = Path(config["root"])
    raw = Path(config["raw_log"])
    fd = os.open(raw, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.dup2(fd, 1)
    os.dup2(fd, 2)
    os.close(fd)
    resource.setrlimit(resource.RLIMIT_FSIZE, (16 * 1024 * 1024, 16 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    interpreter = root / ".venv/bin/python"
    command = [
        str(interpreter),
        "-m",
        "pytest",
        NODE,
        "-vv",
        "--tb=short",
        "-n",
        "0",
        "-p",
        "fact_source_capture_before_observer",
        "-p",
        "no:cacheprovider",
    ]
    os.chdir(root)
    os.execve(str(interpreter), command, dict(os.environ))


def provider_binding(root):
    env = os.environ
    if (
        env.get("GITHUB_REPOSITORY") != "tzeusy-org/butlers"
        or env.get("GITHUB_EVENT_NAME") != "pull_request"
        or env.get("GITHUB_WORKFLOW") != "Fact Source Capture BEFORE Control"
    ):
        raise ValueError("provider-identity")
    for key in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT"):
        value = env.get(key, "")
        if not value.isdecimal() or not 0 < int(value) < 10**20:
            raise ValueError("provider-run")
    head = git(root, "rev-parse", "HEAD")
    reference = env.get("GITHUB_WORKFLOW_REF", "")
    if (
        env.get("GITHUB_SHA") != head
        or env.get("GITHUB_WORKFLOW_SHA") != head
        or not re.fullmatch(
            r"tzeusy-org/butlers/\.github/workflows/"
            r"fact-source-capture-before-control\.yml@refs/pull/[1-9][0-9]*/merge",
            reference,
        )
        or platform.python_version() != "3.12.15"
        or git(root, "status", "--porcelain")
    ):
        raise ValueError("controller-checkout")
    return {
        "repository": env["GITHUB_REPOSITORY"],
        "workflow": env["GITHUB_WORKFLOW"],
        "run": env["GITHUB_RUN_ID"],
        "attempt": env["GITHUB_RUN_ATTEMPT"],
        "controller_head": head,
        "controller_tree": git(root, "rev-parse", "HEAD^{tree}"),
        "nonce": secrets.token_hex(32),
        "python": platform.python_version(),
    }


def execute(root, output):
    global AGGREGATE_DEADLINE
    binding = provider_binding(root)
    started = float(os.environ["FACT_CAPTURE_CONTROL_STARTED"])
    if not 0 <= time.monotonic() - started < 2480:
        raise ValueError("aggregate-start")
    deadline = started + 2480
    AGGREGATE_DEADLINE = deadline
    receipts = {}
    environment = child_environment()

    def remaining(bound):
        value = min(bound, deadline - time.monotonic() - 120)
        if value < bound or CANCELLED:
            raise ValueError("aggregate-deadline")
        return value

    with tempfile.TemporaryDirectory(prefix="fact-pg-witness-", dir=output.parent) as directory:
        private = Path(directory)
        os.chmod(private, 0o700)
        workload = private / "workload"
        # Clone only local authenticated checkout objects. No remote command or revision input.
        source = root.parent / "workload-source"
        with (private / "clone.log").open("wb") as stream:
            result = subprocess.run(
                [
                    "git",
                    "-c",
                    "core.hooksPath=/dev/null",
                    "clone",
                    "--shared",
                    "--no-hardlinks",
                    "--no-checkout",
                    str(source),
                    str(workload),
                ],
                stdout=stream,
                stderr=stream,
                timeout=30,
            )
        if result.returncode:
            raise ValueError("workload-clone")
        git(workload, "checkout", "--detach", CANDIDATE)
        state = workload_state(workload, "after")
        binding["workload"] = state
        overlay(workload, "before")
        binding["phase_states"] = {"before": workload_state(workload, "before")}
        overlay(workload, "after")
        binding["phase_states"]["after"] = workload_state(workload, "after")
        binding["observer_sha256"] = hashlib.sha256(
            (root / "scripts/fact_source_capture_before_observer.py").read_bytes()
        ).hexdigest()
        environment = prepare_uv(root, private, environment, remaining(640))
        prepared = run_owned(
            [sys.executable, str(workload / "scripts/ci_environment.py"), "prepare"],
            environment,
            remaining(640),
        )
        if prepared["exit"] != 0 or not prepared["cleanup_complete"] or prepared["timed_out"]:
            raise ValueError("environment-prepare")
        sentinel = subprocess.Popen(
            [sys.executable, "-c", "import time;time.sleep(1200)"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            for name in ("before", "after"):
                overlay(workload, name)
                state = workload_state(workload, name)
                config = {
                    "binding": binding,
                    "phase": name,
                    "root": str(workload),
                    "output": str(private / (name + ".json")),
                    "raw_log": str(private / (name + ".private.log")),
                    "runtime_sha256": state["runtime_sha256"],
                }
                config_file = private / (name + "-config.json")
                atomic(config_file, config)
                env = {
                    **environment,
                    "FACT_CAPTURE_CONTROL_CONFIG": str(config_file),
                    "PYTHONPATH": str(root / "scripts"),
                    "PYTHONPYCACHEPREFIX": str(private / (name + "-pycache")),
                }
                lifecycle = run_owned(
                    [sys.executable, str(Path(__file__).resolve()), "--phase-child"],
                    env,
                    remaining(420),
                )
                if not Path(config["output"]).is_file():
                    raise ValueError("missing-phase-receipt")
                data = json.loads(Path(config["output"]).read_text())
                data.update(
                    process_cleanup=lifecycle["cleanup_complete"],
                    sentinel_preserved=sentinel.poll() is None,
                    pytest_exit=lifecycle["exit"],
                )
                if lifecycle["timed_out"] or lifecycle["cancelled"]:
                    data["terminal"] = False
                # Retain a closed nonpass prefix without leaking arbitrary plugin values.
                safe_phase(data, name, binding)
                atomic(output / (name + ".json"), data)
                require_phase(data, name, binding)
                receipts[name] = data
                if workload_state(workload, name) != state:
                    raise ValueError("post-call-source-drift")
        finally:
            sentinel.kill()
            sentinel.wait(timeout=5)
        verdict = require_witness(receipts["before"], receipts["after"], binding)
        atomic(
            output / "summary.json",
            {
                "schema": "fact-capture-pg-control.v1",
                "complete": False,
                "control_verdict": verdict,
                "binding": binding,
                "workload_only": True,
                "phases": {k: digest(v) for k, v in receipts.items()},
            },
        )


def main():
    if sys.argv[1:] == ["--phase-child"]:
        phase_child()
    if len(sys.argv) != 1:
        return 2
    root = Path(__file__).resolve().parents[1]
    output = Path(os.environ.get("RUNNER_TEMP", "/nonexistent")) / "fact-capture-before-evidence"
    try:
        # Refuse before creating output or starting children on an unbound host.
        provider_binding(root)
        output.mkdir(exist_ok=False)
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(
                sig, lambda signum, frame: CANCELLED.append(signum) if not CANCELLED else None
            )
        execute(root, output)
        print("fact-capture control WITNESS_COMPLETE; BEFORE test remains FAILED")
        return 0
    except (OSError, ValueError, subprocess.SubprocessError, KeyError):
        print("fact-capture control UNKNOWN/refused; no complete witness")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
