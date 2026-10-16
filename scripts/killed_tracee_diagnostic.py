"""Temporary bu-0nz5zg diagnostic. NEVER MERGE this workflow or instrumentation.

Repeat only the original owner; capture real parser inputs before test cleanup.
Raw data is uploaded only inside an encrypted envelope. No source repair occurs.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import platform
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import time
from pathlib import Path

import pytest

NODE = (
    "tests/scripts/test_condense_evidence_settlement.py::"
    "test_killed_tracees_record_their_exits_without_signal_records"
)
ITERATIONS = 300
RAW_LIMIT = 32 * 1024 * 1024
WORKER_LIMIT = 256 * 1024 * 1024
STREAMS = []
CURRENT_BYTES = 0
CAPTURE_OVERFLOW = False
RETAINED_BYTES = 0
RESULTS = []
START = None
ORIGINAL_CLASS = None


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")


def output(config):
    return Path(config.getoption("--killed-tracee-output"))


def pytest_addoption(parser):
    parser.addoption("--killed-tracee-output", required=True)


def pytest_sessionstart(session):
    global ORIGINAL_CLASS
    # The subclass intercepts instance dispatch even when the unchanged owner
    # temporarily replaces the class's consume method to omit one terminal.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import condense_evidence as evidence

    original_class = evidence.RequestAccessStream
    ORIGINAL_CLASS = original_class

    class CapturingStream(original_class):
        def __init__(self, root, inputs, request, result, nonce):
            super().__init__(root, inputs, request, result, nonce)
            self.diagnostic_constructor = {
                "root": str(root),
                "inputs": sorted(inputs),
                "request": str(request),
                "output": str(result),
                "nonce": nonce,
            }
            self.diagnostic_raw = []
            self.diagnostic_transitions = []
            STREAMS.append(self)

        def __getattribute__(self, name):
            method = super().__getattribute__(name)
            if name != "consume":
                return method

            def capture(raw):
                global CURRENT_BYTES, CAPTURE_OVERFLOW
                CURRENT_BYTES += len(raw) + 1
                if CURRENT_BYTES <= RAW_LIMIT:
                    self.diagnostic_raw.append(raw + b"\n")
                else:
                    CAPTURE_OVERFLOW = True
                failed = self.failed
                pending = dict(self.pending)
                method(raw)
                if self.failed != failed:
                    self.diagnostic_transitions.append(
                        {
                            "line": len(self.diagnostic_raw),
                            "failed_before": failed,
                            "failed_after": self.failed,
                            "pending_before": pending,
                            "pending_after": dict(self.pending),
                        }
                    )

            return capture

    evidence.RequestAccessStream = CapturingStream


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(session, config, items):
    if len(items) != 1 or items[0].nodeid != NODE:
        raise pytest.UsageError("diagnostic accepts exactly the one original killed-tracee node")
    item = items[0]
    items[:] = [
        pytest.Function.from_parent(
            item.parent,
            name=f"{item.originalname}[diagnostic-{index:03d}]",
            originalname=item.originalname,
            callobj=item.obj,
        )
        for index in range(ITERATIONS)
    ]


def pytest_runtest_setup(item):
    global CURRENT_BYTES, CAPTURE_OVERFLOW, START
    STREAMS.clear()
    CURRENT_BYTES = 0
    CAPTURE_OVERFLOW = False
    START = time.monotonic()


def pytest_collection_finish(session):
    if len(session.items) != ITERATIONS:
        raise pytest.UsageError("diagnostic repetition population is incomplete")
    worker = os.environ.get("PYTEST_XDIST_WORKER", "controller")
    write_json(
        output(session.config) / f"selection-{worker}.json",
        {"original_node": NODE, "iterations": ITERATIONS, "selected": len(session.items)},
    )


def retain_failure(item, call):
    global RETAINED_BYTES
    target = (
        output(item.config) / "raw" / os.environ.get("PYTEST_XDIST_WORKER", "master") / item.name
    )
    if target.exists():
        return
    target.mkdir(parents=True)
    metadata = []
    for index, stream in enumerate(STREAMS):
        raw = b"".join(stream.diagnostic_raw)
        RETAINED_BYTES += len(raw)
        if RETAINED_BYTES > WORKER_LIMIT:
            raise RuntimeError("diagnostic private capture capacity exhausted")
        (target / f"stream-{index}.strace").write_bytes(raw)
        constructor = stream.diagnostic_constructor
        replay = ORIGINAL_CLASS(
            Path(constructor["root"]),
            set(constructor["inputs"]),
            Path(constructor["request"]),
            Path(constructor["output"]),
            constructor["nonce"],
        )
        omitted = 0
        for line in raw.splitlines():
            if Path(constructor["output"]).name == "missing-terminal.json" and line.endswith(
                b"+++ killed by SIGTERM +++"
            ):
                omitted += 1
            else:
                replay.consume(line)
        replay_complete, replay_actors = replay.finish()
        metadata.append(
            {
                "constructor": stream.diagnostic_constructor,
                "failed": stream.failed,
                "pending": stream.pending,
                "actors": sorted(stream.actors),
                "parents": stream.parents,
                "exits": sorted(stream.exits),
                "executables": stream.executables,
                "accesses": stream.accesses,
                "reads": stream.reads,
                "probes": stream.probes,
                "subject_reads": stream.subject_reads,
                "subject_children": stream.subject_children,
                "failure_transitions": stream.diagnostic_transitions,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "offline_replay_before_cleanup": {
                    "failed": replay.failed,
                    "pending": replay.pending,
                    "complete": replay_complete,
                    "actors": replay_actors,
                    "omitted_terminals": omitted,
                    "accesses": replay.accesses,
                    "reads": replay.reads,
                    "probes": replay.probes,
                    "subject_reads": replay.subject_reads,
                    "subject_children": replay.subject_children,
                },
            }
        )
    # These are locals of the unchanged synthetic owner, never arbitrary env.
    frames = []
    if call.excinfo is not None:
        for entry in call.excinfo.traceback:
            if Path(str(entry.path)).name == "test_condense_evidence_settlement.py":
                locals_ = entry.frame.f_locals
                frames.append(
                    {
                        "line": entry.lineno + 1,
                        "function": entry.name,
                        "locals": {
                            key: locals_[key]
                            for key in ("species", "code", "cleaned", "access", "omitted")
                            if key in locals_
                        },
                    }
                )
    source = item.funcargs.get("tmp_path")
    copied = []
    if source is not None:
        source = Path(source)
        source.resolve().relative_to(item.config._tmp_path_factory.getbasetemp().resolve())
        for path in sorted(source.rglob("*")):
            if not stat.S_ISREG(path.lstat().st_mode):
                continue
            data = path.read_bytes()
            RETAINED_BYTES += len(data)
            if RETAINED_BYTES > WORKER_LIMIT:
                raise RuntimeError("diagnostic private capture capacity exhausted")
            relative = path.relative_to(source)
            dest = target / "synthetic-tmp" / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            copied.append({"path": str(relative), "sha256": hashlib.sha256(data).hexdigest()})
    write_json(
        target / "state.json",
        {"streams": metadata, "frames": frames, "copied": copied, "overflow": CAPTURE_OVERFLOW},
    )


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.failed:
        retain_failure(item, call)
    RESULTS.append(
        {
            "iteration": int(item.name.rsplit("-", 1)[1][:-1]),
            "phase": call.when,
            "outcome": report.outcome,
            "started_s": START,
            "completed_s": time.monotonic(),
            "capture_overflow": CAPTURE_OVERFLOW,
        }
    )


def pytest_sessionfinish(session, exitstatus):
    worker = os.environ.get("PYTEST_XDIST_WORKER", "controller")
    if worker != "controller":
        write_json(
            output(session.config) / f"{worker}.json",
            {"worker": worker, "exit": int(exitstatus), "phases": RESULTS},
        )


def descendants():
    rows = {}
    for path in Path("/proc").glob("[0-9]*/stat"):
        try:
            fields = path.read_text().rsplit(") ", 1)[1].split()
            rows[int(path.parent.name)] = (int(fields[1]), fields[19], fields[0])
        except (FileNotFoundError, ProcessLookupError):
            continue
    owned = {os.getpid()}
    while True:
        expanded = owned | {pid for pid, row in rows.items() if row[0] in owned}
        if expanded == owned:
            break
        owned = expanded
    return {pid: rows[pid] for pid in owned - {os.getpid()}}


def settle_children(child):
    # Subreaper custody retains descendants even if a worker or strace exits.
    # Match each observed start identity before signaling; never touch a host peer.
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        owned = descendants()
        for pid, (_, start, state) in owned.items():
            if state == "Z":
                continue
            current = descendants().get(pid)
            if current is not None and current[1] == start:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        child.poll()
        while True:
            try:
                reaped, _ = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                break
            if reaped == 0:
                break
        if not descendants():
            return True
        time.sleep(0.05)
    return False


def run():
    """One finite attempt; owned group cleanup, then encrypt before upload."""
    destination = Path(sys.argv[2]).resolve()
    destination.mkdir(parents=True, exist_ok=False)
    if ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) != 0:
        raise RuntimeError("diagnostic requires Linux child-subreaper custody")
    command = [
        sys.executable,
        "-m",
        "pytest",
        NODE,
        "-p",
        "scripts.killed_tracee_diagnostic",
        "--killed-tracee-output",
        str(destination),
        "--basetemp",
        str(destination / "runtime"),
        "-n",
        "3",
        "--dist=load",
        "--max-worker-restart=0",
        "--tb=no",
        "-q",
        "-o",
        "console_output_style=count",
    ]
    log = destination / "pytest-private.log"
    start = time.monotonic()
    timed_out = False
    code = 125

    def interrupted(signum, frame):
        raise TimeoutError("diagnostic interrupted")

    signal.signal(signal.SIGTERM, interrupted)
    with log.open("wb") as carrier:
        child = subprocess.Popen(command, stdout=carrier, stderr=carrier, start_new_session=True)
        try:
            code = child.wait(timeout=1800)
        except (subprocess.TimeoutExpired, TimeoutError):
            timed_out = True
            code = 124
        finally:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            cleaned = settle_children(child)
            child.wait(timeout=30)
    workers = [json.loads(p.read_text()) for p in sorted(destination.glob("gw*.json"))]
    calls = [p for worker in workers for p in worker["phases"] if p["phase"] == "call"]
    identities = [p["iteration"] for p in calls]
    complete = (
        len(workers) == 3
        and sorted(identities) == list(range(ITERATIONS))
        and len(identities) == len(set(identities))
        and all(
            sorted(
                p["phase"]
                for worker in workers
                for p in worker["phases"]
                if p["iteration"] == index
            )
            == ["call", "setup", "teardown"]
            for index in range(ITERATIONS)
        )
        and not any(p["capture_overflow"] for worker in workers for p in worker["phases"])
        and not timed_out
        and cleaned
    )
    # Controller monotonic clocks are common to the three same-host workers.
    events = sorted(
        (t, delta) for p in calls for t, delta in ((p["started_s"], 1), (p["completed_s"], -1))
    )
    current = peak = 0
    for _, delta in events:
        current += delta
        peak = max(peak, current)
    source_paths = [
        NODE.split("::")[0],
        "scripts/condense_evidence.py",
        "scripts/check_condensation_ledger.py",
        "scripts/killed_tracee_diagnostic.py",
        ".github/workflows/killed-tracee-diagnostic.yml",
        ".github/diagnostics/killed-tracee-capture.pem",
        "uv.lock",
        "pyproject.toml",
        "conftest.py",
        "tests/quarantine.py",
        "tests/quarantine.json",
        "tests/log_isolation.py",
        ".github/workflows/ci.yml",
        ".github/workflows/ci-coverage.yml",
        "scripts/ci-job-timeouts.json",
    ]
    summary = {
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "run_id": os.environ.get("GITHUB_RUN_ID"),
        "attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
        "affinity_count": len(os.sched_getaffinity(0)),
        "strace": subprocess.check_output(["strace", "--version"], text=True).splitlines()[0],
        "openssl": subprocess.check_output(["openssl", "version"], text=True).strip(),
        "source_sha256": {
            name: hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in source_paths
        },
        "requested": ITERATIONS,
        "calls": len(calls),
        "passed": sum(p["outcome"] == "passed" for p in calls),
        "failed": sum(p["outcome"] == "failed" for p in calls),
        "failed_iterations": len(
            {
                p["iteration"]
                for worker in workers
                for p in worker["phases"]
                if p["outcome"] == "failed"
            }
        ),
        "phase_outcomes": {
            phase: {
                status: sum(
                    p["phase"] == phase and p["outcome"] == status
                    for worker in workers
                    for p in worker["phases"]
                )
                for status in ("passed", "failed", "skipped")
            }
            for phase in ("setup", "call", "teardown")
        },
        "complete": complete,
        "peak_simultaneous_calls": peak,
        "worker_calls": {
            w["worker"]: sum(p["phase"] == "call" for p in w["phases"]) for w in workers
        },
        "exit": code,
        "timed_out": timed_out,
        "owned_cleanup": cleaned,
        "elapsed_s": time.monotonic() - start,
        "command": command,
        "raw_failure_directories": len(list((destination / "raw").glob("*/*"))),
    }
    # Public Actions artifact: only encrypted raw captures and content-blind summary.
    private = destination / "capture.tar.gz"
    with tarfile.open(private, "w:gz") as archive:
        for path in [
            log,
            *sorted(destination.glob("gw*.json")),
            *sorted(destination.glob("selection-*.json")),
            destination / "raw",
        ]:
            if path.exists():
                archive.add(path, arcname=path.name)
    encrypted = destination / "capture.cms"
    subprocess.run(
        [
            "openssl",
            "cms",
            "-encrypt",
            "-binary",
            "-aes-256-cbc",
            "-in",
            str(private),
            "-out",
            str(encrypted),
            "-outform",
            "DER",
            ".github/diagnostics/killed-tracee-capture.pem",
        ],
        check=True,
        timeout=30,
    )
    summary["encrypted_sha256"] = hashlib.sha256(encrypted.read_bytes()).hexdigest()
    write_json(destination / "summary.json", summary)
    for path in destination.iterdir():
        if path.name not in {"capture.cms", "summary.json"}:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
    print(
        json.dumps(
            {
                k: summary[k]
                for k in (
                    "complete",
                    "calls",
                    "passed",
                    "failed",
                    "exit",
                    "peak_simultaneous_calls",
                )
            }
        )
    )
    return code if complete and peak == 3 else 125


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "run":
        raise SystemExit("usage: killed_tracee_diagnostic.py run NEW_OUTPUT")
    raise SystemExit(run())
