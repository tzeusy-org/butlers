#!/usr/bin/env python3
"""Produce branch/mutation evidence in owned copies, without editing live tests."""

from __future__ import annotations

import argparse
import ast
import copy
import fcntl
import hashlib
import io
import json
import os
import re
import secrets
import select
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# The owned launcher must not run project startup hooks before the external
# request-access observer starts. Restore only this exact installed script home.
if __name__ == "__main__" and sys.flags.isolated:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from check_condensation_ledger import (
    PROTECTED,
    REQUEST_TRACE_OPTIONS,
    EvidenceError,
    canonical,
    execution_request_inputs,
    fields,
    fresh_proof_refusal,
    git,
    git_environment,
    input_record,
    named_coverage_residue,
    production_dependencies,
    public_path,
    same_owner_context_accounts,
    source_records,
    strict_json,
    test_path,
    tool_record,
    tracked_paths,
)
from pre_push import Refusal, await_owned_group_exit, recovery_signals


def publish(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def owned_run(
    command: list[str],
    *,
    cwd: Path,
    env: dict,
    timeout: float,
    process_record: Path | None = None,
    cancellation: threading.Event | None = None,
    observer_cancel=None,
) -> tuple[int, bool]:
    """Use the landed R1 completion admission for this exact owned child group."""
    launched = [sys.executable, "-I", str(Path(__file__).resolve()), "_launch", *command]
    child = subprocess.Popen(
        launched,
        cwd=cwd,
        env=env,
        start_new_session=True,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        if process_record is not None:
            identity = process_identity(child.pid)
            if identity is None or identity["group"] != child.pid:
                raise EvidenceError("owned-launch-identity-unknown")
            publish(process_record, {"identity": identity, "command_sha256": canonical(command)})
        child.stdin.write(b"1")
        child.stdin.close()
        try:
            if cancellation is None:
                return child.wait(timeout=timeout), True
            deadline = time.monotonic() + timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise EvidenceError("proof-timeout")
                try:
                    # Retain an actual completed child's result before acting on
                    # sibling cancellation. The existing finite wait quantum
                    # still bounds cancellation of a child that is running.
                    return child.wait(timeout=min(0.1, remaining)), True
                except subprocess.TimeoutExpired:
                    if cancellation.is_set():
                        completed = child.poll()
                        if completed is not None:
                            return completed, True
                        if observer_cancel is not None:
                            # End only independently bound tracees first. Let the
                            # observer record their actual exits inside the original
                            # deadline; the ordinary group finally still settles all.
                            observer_cancel(child.pid)
                            try:
                                child.wait(timeout=min(0.5, max(0, deadline - time.monotonic())))
                            except subprocess.TimeoutExpired:
                                pass
                        raise InterruptedError
        except subprocess.TimeoutExpired:
            raise EvidenceError("proof-timeout") from None
    finally:
        if child.stdin is not None and not child.stdin.closed:
            child.stdin.close()
        with recovery_signals():
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                await_owned_group_exit(child.pid)
                child.wait(timeout=5)
                if process_record is not None and process_record.is_file():
                    record = strict_json(process_record)
                    publish(process_record, {**record, "cleanup": True})
            except (Refusal, subprocess.TimeoutExpired):
                raise EvidenceError("owned-group-completion-unknown") from None


def process_identity(pid: int) -> dict | None:
    try:
        fields = (Path("/proc") / str(pid) / "stat").read_text().rsplit(") ", 1)[1].split()
        return {"pid": pid, "group": int(fields[2]), "start": fields[19], "state": fields[0]}
    except (FileNotFoundError, ProcessLookupError):
        return None


def _recover(output: Path) -> dict:
    """Restore only the journal's owned copy; recovery never upgrades UNKNOWN to PASS."""
    output = output.resolve()
    with (output / "run.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise EvidenceError("proof-owner-still-running") from None
        journal = strict_json(output / "journal.json")
        owned = Path(journal["owned_copy"])
        if (
            owned.parent != output
            or not owned.name.startswith("condense-owned-")
            or owned.is_symlink()
        ):
            raise EvidenceError("unowned-recovery-path")
        if not owned.is_dir():
            raise EvidenceError("owned-copy-already-absent-requires-completed-receipt")
        for process in sorted(output.glob("*.process.json")):
            record = strict_json(process)
            identity = record["identity"]
            current = process_identity(identity["pid"])
            if current and current["state"] not in {"Z", "X"}:
                # Recovery never signals a later numeric PID/group. The original owner
                # performs cleanup; an interrupted group must independently settle.
                raise EvidenceError("recovery-process-not-settled-no-signal")
            # Never kill a group with an absent/reused leader merely by its number.
            try:
                await_owned_group_exit(identity["group"])
            except Refusal:
                raise EvidenceError("recovery-owned-group-completion-unknown") from None
        for mutation in journal["mutations"]:
            for side in ("removed", "survivors"):
                record = mutation.get("current", mutation) if side == "survivors" else mutation
                name = public_path(record["path"])
                before = output / public_path(record["before_file"])
                if (
                    before.is_symlink()
                    or hashlib.sha256(before.read_bytes()).hexdigest() != record["before_sha256"]
                ):
                    raise EvidenceError("recovery-journal-body-mismatch")
                target = owned / side / name
                if target.is_symlink() or owned not in target.resolve().parents:
                    raise EvidenceError("indirect-recovery-target")
                target.write_bytes(before.read_bytes())
                target.chmod(record["before_mode"])
                if (
                    target.read_bytes() != before.read_bytes()
                    or target.stat().st_mode & 0o777 != record["before_mode"]
                ):
                    raise EvidenceError("recovery-restore-unknown")
        journal["status"] = "RESTORED"
        publish(output / "journal.json", journal)
        receipt = {
            "status": "UNKNOWN",
            "restored": True,
            "cleanup": True,
            "category": "recovered-needs-fresh-proof",
        }
        publish(output / "recovery.json", receipt)
        return receipt


def recover(output: Path) -> dict:
    try:
        return _recover(output)
    except EvidenceError:
        raise
    except (OSError, KeyError, TypeError, ValueError):
        raise EvidenceError("recovery-input-unknown") from None


def worker(request: Path) -> int:
    """Raw pytest parameter identities remain only in this process's memory."""
    import coverage
    import pytest

    config = strict_json(request)
    root = Path(config["root"]).resolve()
    sys.path[:0] = [str(root / "src"), str(root)]
    output = Path(config["output"])
    selected = config["selection"]
    nonce = config["nonce"]
    cases = {}
    complete_collection = False
    deselected = 0
    cov = coverage.Coverage(
        branch=True,
        include=[str(root / public_path(p)) for p in config["scope"]],
        data_file=str(output.with_suffix(".coverage")),
        config_file=False,
    )
    control_inputs = {str(root / public_path(p)) for p in config.get("control_inputs", [])}
    control_read = False
    descendant_execution = False

    def guard_control_input(event, args):
        nonlocal control_read, descendant_execution
        if control_inputs and event in {
            "subprocess.Popen",
            "os.system",
            "os.fork",
            "os.forkpty",
            "os.exec",
            "os.posix_spawn",
        }:
            # This worker's open audit cannot observe descendant reads. Keep
            # their actual execution/lifecycle intact, but never certify an
            # excluded request dependency without that isolation evidence.
            descendant_execution = True
        if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
            if os.path.realpath(os.fsdecode(args[0])) in control_inputs:
                control_read = True
                raise RuntimeError("execution-request-used-as-runtime-data")

    class ProofPlugin:
        def pytest_deselected(self, items):
            nonlocal deselected
            deselected += len(items)

        def pytest_collection_finish(self, session):
            nonlocal complete_collection
            for item in session.items:
                key = hashlib.sha256((nonce + "\0" + item.nodeid).encode()).hexdigest()
                cases[item.nodeid] = {
                    "key": key,
                    "node": item.nodeid.split("[", 1)[0],
                    "phases": {},
                }
            complete_collection = bool(cases) and len(cases) == len(session.items)

        @pytest.hookimpl(hookwrapper=True)
        def pytest_runtest_setup(self, item):
            cov.switch_context(cases[item.nodeid]["key"] + "|setup")
            yield

        @pytest.hookimpl(hookwrapper=True)
        def pytest_runtest_call(self, item):
            cov.switch_context(cases[item.nodeid]["key"] + "|call")
            yield

        @pytest.hookimpl(hookwrapper=True)
        def pytest_runtest_teardown(self, item):
            cov.switch_context(cases[item.nodeid]["key"] + "|teardown")
            yield
            cov.switch_context("")

        @pytest.hookimpl(hookwrapper=True)
        def pytest_runtest_makereport(self, item, call):
            outcome = yield
            report = outcome.get_result()
            result = report.outcome
            if report.failed:
                result = (
                    "assertion-failed"
                    if (
                        call.when == "call"
                        and call.excinfo is not None
                        and issubclass(call.excinfo.type, AssertionError)
                    )
                    else "nonsemantic-failed"
                )
            cases[item.nodeid]["phases"][report.when] = result

    previous_core = os.environ.get("COVERAGE_CORE")
    try:
        os.environ["COVERAGE_CORE"] = "ctrace"
        cov.start()
        tracer = dict(cov.sys_info()).get("core", "UNKNOWN")
        # Establish the installed tracer first: its sys_info reads platform
        # metadata through an owned subprocess. No subject has been imported or
        # invoked yet. Guard every subsequent event, before pytest's collection,
        # configuration, fixture or call execution; there is no executor bypass.
        if control_inputs:
            # This fixed read separates installed tracer setup from subject entry.
            # Its own descriptor is observed externally; a caller flag is no proof.
            with output.with_suffix(".subject-probe").open("rb") as probe:
                if probe.read() != nonce.encode():
                    raise EvidenceError("subject-observer-probe-mismatch")
        sys.addaudithook(guard_control_input)
        args = [
            *selected,
            "-n",
            "0",
            "-p",
            "no:cov",
            "-p",
            "no:terminal",
            "-o",
            "addopts=",
            "--import-mode=importlib",
            "-m",
            "not nightly and not bench and not perf",
            "--ignore=tests/benchmarks",
        ]
        code = int(pytest.main(args, plugins=[ProofPlugin()]))
    finally:
        cov.stop()
        cov.save()
        if previous_core is None:
            os.environ.pop("COVERAGE_CORE", None)
        else:
            os.environ["COVERAGE_CORE"] = previous_core
    data = cov.get_data()
    arcs = {}
    for row in cases.values():
        case_arcs = set()
        for phase in ("setup", "call", "teardown"):
            data.set_query_context(row["key"] + "|" + phase)  # literal exact SQL query, never regex
            for filename in data.measured_files():
                path = Path(filename).resolve()
                if root in path.parents:
                    case_arcs.update(
                        (str(path.relative_to(root)), a, b) for a, b in (data.arcs(filename) or [])
                    )
        arcs[row["key"]] = sorted(case_arcs)
    public_cases = list(cases.values())
    complete = (
        complete_collection
        and not control_read
        and deselected == 0
        and tracer == "CTracer"
        and all(
            set(r["phases"]) == {"setup", "call", "teardown"}
            and r["phases"]["setup"] == "passed"
            and r["phases"]["teardown"] == "passed"
            and r["phases"]["call"] in {"passed", "assertion-failed"}
            for r in public_cases
        )
    )
    publish(
        output,
        {
            "id": config["id"],
            "complete": complete,
            "cleanup": False,
            "selected": len(public_cases),
            "deselected": deselected,
            "cases": public_cases,
            "arcs": arcs,
            "core": "ctrace" if tracer == "CTracer" else "UNKNOWN",
            "tracer": tracer,
            "exit": code,
            "selection": selected,
            "command_flags": args[len(selected) :],
            "measurement_root": str(root),
            "scope": config["scope"],
            "scope_inputs": [input_record(root, p) for p in config["scope"]],
            "request_boundary": {
                "inputs": sorted(config.get("control_inputs", [])),
                "runtime_read": control_read,
                "descendant_execution": descendant_execution,
            },
            "tools": tool_record(),
        },
    )
    return 0 if complete else 2


def generate_mutants(sources: dict[str, bytes]) -> list[dict]:
    """Deterministic finite actual AST mutations; each changes one public source span."""
    result = []
    for path, body in sorted(sources.items()):
        source = body.decode()
        lines = body.splitlines(keepends=True)
        for node in ast.walk(ast.parse(source)):
            replacement = None
            operator = None
            if isinstance(node, ast.If) and any(isinstance(n, ast.Return) for n in node.body):
                replacement, operator = "pass", "drop-guard-return"
            elif isinstance(node, ast.Compare) and len(node.ops) == 1:
                changes = {
                    ast.Eq: ast.NotEq,
                    ast.NotEq: ast.Eq,
                    ast.Lt: ast.GtE,
                    ast.Gt: ast.LtE,
                    ast.LtE: ast.Gt,
                    ast.GtE: ast.Lt,
                }
                changed = changes.get(type(node.ops[0]))
                if changed:
                    candidate = copy.deepcopy(node)
                    candidate.ops = [changed()]
                    replacement, operator = ast.unparse(candidate), "swap-comparison"
            elif isinstance(node, ast.If):
                target = node.test
                replacement, operator = "not (" + ast.unparse(target) + ")", "negate-condition"
                node = target
            elif (
                isinstance(node, ast.Return)
                and node.value is not None
                and not (isinstance(node.value, ast.Constant) and node.value.value is None)
            ):
                replacement, operator = "return None", "return-none"
            elif (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value
                in {"accepted", "duplicate", "error", "pending", "approved", "rejected", "ok"}
            ):
                replacement, operator = (
                    repr("error" if node.value != "error" else "accepted"),
                    "replace-status",
                )
            if replacement is None:
                continue
            start = sum(len(line) for line in lines[: node.lineno - 1]) + node.col_offset
            end = sum(len(line) for line in lines[: node.end_lineno - 1]) + node.end_col_offset
            changed_source = (body[:start] + replacement.encode() + body[end:]).decode()
            try:
                compile(changed_source, path, "exec")
            except SyntaxError:
                continue
            identity = canonical(
                {
                    "path": path,
                    "line": node.lineno,
                    "column": node.col_offset,
                    "operator": operator,
                    "before": hashlib.sha256(source.encode()).hexdigest(),
                    "after": hashlib.sha256(changed_source.encode()).hexdigest(),
                }
            )
            result.append(
                {
                    "id": identity,
                    "path": path,
                    "line": node.lineno,
                    "column": node.col_offset,
                    "operator": operator,
                    "body": changed_source.encode(),
                }
            )
    return sorted(result, key=lambda r: r["id"])


def mutants(root: Path, scope: list[str]) -> list[dict]:
    return generate_mutants({p: (root / public_path(p)).read_bytes() for p in scope})


def mutation_batches(identifiers: list[str]) -> list[dict]:
    """Partition the complete ordered population; a batch never grants partial admission."""
    if not identifiers or len(set(identifiers)) != len(identifiers):
        raise EvidenceError("empty-or-aliased-mutation-population")
    batches = []
    for offset in range(0, len(identifiers), 64):
        row = {"index": len(batches), "mutants": identifiers[offset : offset + 64]}
        batches.append({**row, "id": canonical(row), "status": "UNKNOWN"})
    return batches


def suite(
    copy_root: Path,
    selection: list[str],
    scope: list[str],
    nonce: str,
    output: Path,
    id_: str,
    timeout: float,
    control_inputs: set[str] | None = None,
    cancellation: threading.Event | None = None,
) -> dict:
    request = output.with_suffix(".request.json")
    publish(
        request,
        {
            "root": str(copy_root),
            "selection": selection,
            "scope": scope,
            "nonce": nonce,
            "output": str(output),
            "id": id_,
            "control_inputs": sorted(control_inputs or []),
        },
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(copy_root / "src"), str(copy_root), str(Path(__file__).resolve().parent)]
    )
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["COVERAGE_CORE"] = "ctrace"
    command = [sys.executable, str(Path(__file__).resolve()), "_worker", str(request)]
    if control_inputs:
        output.with_suffix(".subject-probe").write_bytes(nonce.encode())
    try:
        code, cleaned, access = request_access_run(
            command, copy_root, request, output, env, timeout, control_inputs or set(), cancellation
        )
    finally:
        # Preserve the actual worker result when observation fails after publication.
        # The closed incomplete observation remains diagnostic, never proof authority.
        primary = sys.exc_info()[1]
        observation = output.with_suffix(".access.json")
        try:
            if output.is_file() and observation.is_file():
                result = strict_json(output)
                access = strict_json(observation)
                result["request_access"] = access
                result["complete"] = (
                    primary is None
                    and result["complete"]
                    and access["complete"]
                    and access["accesses"] == 0
                )
                publish(output, result)
        except BaseException:
            if primary is None:
                raise
    if code != 0 or not output.is_file():
        raise EvidenceError("worker-incomplete")
    result = strict_json(output)
    if not access["complete"] or access["accesses"]:
        raise EvidenceError("worker-incomplete")
    if result["complete"] is not True or result["core"] != "ctrace":
        raise EvidenceError("worker-phases-or-tracer-unknown")
    result["cleanup"] = cleaned
    publish(output, result)
    return result


# Full syscall streams stay in RAM. Only fixed counters, source body identities,
# nonce-hashed actor identities, installed tool hashes and closed status escape.


def request_input_versions(root, inputs):
    rows = []
    for name in sorted(inputs):
        path = root / public_path(name)
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        except FileNotFoundError:
            # A source-bound original snapshot may predate request creation.
            # Keep its absent pathname observed; creation/access still refuses.
            if path.exists() or path.is_symlink():
                raise EvidenceError("request-input-object-unproved") from None
            rows.append({"path": name, "present": False, "sha256": None, "mode": None})
            continue
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise EvidenceError("request-input-object-unproved")
            body = bytearray()
            while chunk := os.read(descriptor, 65536):
                body.extend(chunk)
            after = os.fstat(descriptor)

            def identity(value):
                return (
                    value.st_dev,
                    value.st_ino,
                    value.st_mode,
                    value.st_nlink,
                    value.st_size,
                    value.st_mtime_ns,
                    value.st_ctime_ns,
                )

            if identity(info) != identity(after) or identity(
                path.stat(follow_symlinks=False)
            ) != identity(after):
                raise EvidenceError("request-input-object-changed")
            rows.append(
                {
                    "path": name,
                    "present": True,
                    "sha256": hashlib.sha256(body).hexdigest(),
                    "mode": "100755" if info.st_mode & 0o111 else "100644",
                }
            )
        finally:
            os.close(descriptor)
    return rows


class RequestAccessStream:
    """Reconcile complete installed strace records without retaining syscall payloads.

    Kernel -yy annotations resolve cwd/dirfd/inherited/reused descriptors. A
    matching pathname/alias operation is a refusal even before a successful read;
    unresolved regular-file descriptors, PID reuse and unfinished histories are
    UNKNOWN rather than zero. No executable or import identity grants an exemption.
    """

    def __init__(self, root, inputs, request, output, nonce):
        self.targets = {os.fsencode(root / public_path(name)) for name in inputs}
        self.objects = {}
        self.absent = set()
        for path in self.targets:
            try:
                info = os.stat(path, follow_symlinks=False)
            except FileNotFoundError:
                self.absent.add(path)
            else:
                self.objects[path] = (info.st_dev, info.st_ino)
        self.probe = os.fsencode(request)
        self.subject = os.fsencode(output.with_suffix(".subject-probe"))
        self.nonce = nonce
        self.pending = {}
        self.actors = set()
        self.parents = {}
        self.exits = set()
        self.executables = {}
        self.live = {}
        self.accesses = self.reads = self.probes = self.subject_reads = 0
        self.subject_children = 0
        self.failed = False

    @staticmethod
    def paths(value):
        return [
            bytes.fromhex(token.replace("\\x", ""))
            for token in re.findall(r"(?:\\x[0-9a-f]{2})+", value)
        ]

    def consume(self, raw):
        try:
            line = raw.decode("ascii")
            match = re.fullmatch(r"(\d+) +(.+)", line)
            if match is None:
                self.failed = True
                return
            actor, statement = int(match[1]), match[2]
            self.actors.add(actor)
            if actor not in self.live:
                identity = process_identity(actor)
                if identity is not None:
                    self.live[actor] = identity
            if statement.startswith("+++ "):
                if actor in self.exits or not re.fullmatch(
                    r"\+\+\+ (exited with \d+|killed by SIG[A-Z0-9]+(?: \(core dumped\))?) \+\+\+",
                    statement,
                ):
                    self.failed = True
                self.exits.add(actor)
                return
            if actor in self.exits:
                self.failed = True
                return
            if statement.endswith(" <unfinished ...>"):
                if actor in self.pending:
                    self.failed = True
                self.pending[actor] = statement[: -len(" <unfinished ...>")]
                return
            resumed = re.match(r"<\.\.\. (\w+) resumed>(.*)", statement)
            if resumed:
                prefix = self.pending.pop(actor, None)
                if prefix is None or not prefix.startswith(resumed[1] + "("):
                    self.failed = True
                    return
                statement = prefix + resumed[2]
            elif actor in self.pending:
                self.failed = True
                return
            call = re.fullmatch(r"(\w+)\((.*)\) += (.+)", statement)
            if call is None:
                self.failed = True
                return
            name, args, result = call.groups()
            success = re.match(r"(-?\d+)", result)
            if name in {"clone", "clone3", "fork", "vfork"} and success and int(success[1]) > 0:
                child = int(success[1])
                if child in self.parents:
                    self.failed = True
                self.parents[child] = actor
                if self.subject_reads:
                    self.subject_children += 1
            if name in {"unshare", "setns", "setsid", "setpgid", "chroot", "pivot_root"}:
                self.failed = True
            paths = self.paths(args + " " + result)
            if name in {"execve", "execveat"} and success and int(success[1]) == 0:
                if not paths or not paths[0].startswith(b"/"):
                    self.failed = True
                else:
                    executable = Path(os.fsdecode(paths[0]))
                    info = executable.stat()
                    if not executable.is_file():
                        self.failed = True
                    self.executables[actor] = {
                        "sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
                        "mode": stat.S_IMODE(info.st_mode),
                    }
            matched = bool(self.targets.intersection(paths))
            for path in paths:
                if path.startswith(b"/"):
                    try:
                        info = os.stat(path, follow_symlinks=False)
                    except OSError:
                        continue
                    if (info.st_dev, info.st_ino) in self.objects.values():
                        matched = True
            if name in {
                "rename",
                "renameat",
                "renameat2",
                "link",
                "linkat",
                "symlink",
                "symlinkat",
                "mount",
                "umount",
                "umount2",
            }:
                matched |= any(
                    target.startswith(path.rstrip(b"/") + b"/")
                    for path in paths
                    for target in self.targets
                    if path.startswith(b"/")
                )
            # Relative pathname candidates are conservative until kernel descriptor
            # resolution proves otherwise. They never establish absence by spelling.
            if not matched:
                matched = any(
                    not path.startswith(b"/") and target.endswith(b"/" + path)
                    for path in paths
                    for target in self.targets
                    if path
                )
            if matched:
                self.accesses += 1
            reading = name in {
                "read",
                "readv",
                "pread64",
                "preadv",
                "preadv2",
                "mmap",
                "mmap2",
                "sendfile",
                "splice",
                "copy_file_range",
            }
            if (
                reading
                and success
                and (int(success[1]) > 0 or (name in {"mmap", "mmap2"} and result.startswith("0x")))
            ):
                if not re.search(r"\d+<[^>]+>", args) and (
                    name not in {"mmap", "mmap2"} or "MAP_ANONYMOUS" not in args
                ):
                    self.failed = True
                if matched:
                    self.reads += 1
                if name not in {"mmap", "mmap2"} and self.probe in paths:
                    self.probes += 1
                if name not in {"mmap", "mmap2"} and self.subject in paths:
                    self.subject_reads += 1
            # A renamed/deleted regular descriptor cannot borrow its old spelling.
            # File alias mutations involving an input already count as accesses;
            # otherwise an unresolved/deleted descriptor refuses the observation.
            if " (deleted)" in args or name in {
                "io_uring_setup",
                "io_uring_enter",
                "io_uring_register",
            }:
                self.failed = True
        except (OSError, ValueError, UnicodeError):
            self.failed = True

    def cancel_owned_tracees(self, group):
        # Numeric PIDs alone grant no signaling authority. A current same-group
        # process must match the original observed start identity exactly.
        for actor, original in list(self.live.items()):
            current = process_identity(actor)
            if current is None or current["state"] in {"Z", "X"}:
                continue
            if current["group"] != group or current["start"] != original["start"]:
                self.failed = True
                continue
            try:
                os.kill(actor, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def finish(self):
        roots = self.actors - self.parents.keys()
        complete = (
            not self.failed
            and not self.pending
            and len(roots) == 1
            and self.actors == self.exits
            and set(self.parents.values()) <= self.actors
            and set(self.parents) <= self.actors
            and self.probes > 0
            and self.subject_reads > 0
        )
        for path, identity in self.objects.items():
            try:
                info = os.stat(path, follow_symlinks=False)
                if (info.st_dev, info.st_ino) != identity:
                    complete = False
            except OSError:
                complete = False
        for path in self.absent:
            if os.path.lexists(path):
                complete = False
        rows = []
        for actor in sorted(self.actors):
            original = actor
            seen = set()
            while (
                original not in self.executables
                and original in self.parents
                and original not in seen
            ):
                seen.add(original)
                original = self.parents[original]
            executable = self.executables.get(original)
            if executable is None:
                complete = False

            def key(pid):
                return hashlib.sha256((self.nonce + "\0" + str(pid)).encode()).hexdigest()

            rows.append(
                {
                    "key": key(actor),
                    "parent": key(self.parents[actor]) if actor in self.parents else None,
                    "executable": executable,
                    "exited": actor in self.exits,
                }
            )
        return complete, rows


def request_access_run(command, root, request, output, env, timeout, inputs, cancellation=None):
    access = {
        "observer": "strace" if inputs else "not-required",
        "inputs": sorted(inputs),
        "accesses": 0,
        "probe_accesses": 0,
        "complete": not inputs,
        "protocol": "full-stream-v1",
        "reads": 0,
        "subject_probes": 0,
        "subject_descendants": 0,
        "actors": [],
        "input_versions": [],
        "options_sha256": canonical(REQUEST_TRACE_OPTIONS),
        "tool_sha256": None,
        "version_sha256": None,
        "finalization_failures": [],
    }
    carrier = output.with_suffix(".access.json")
    publish(carrier, access)
    process_record = output.with_suffix(".process.json")
    if not inputs:
        return (
            *owned_run(
                command,
                cwd=root,
                env=env,
                timeout=timeout,
                process_record=process_record,
                cancellation=cancellation,
            ),
            access,
        )
    tool = shutil.which("strace")
    if tool is None:
        raise EvidenceError("request-access-observer-unavailable")
    tool = str(Path(tool).resolve())
    access["tool_sha256"] = hashlib.sha256(Path(tool).read_bytes()).hexdigest()
    version = subprocess.run([tool, "--version"], capture_output=True, timeout=5, check=True)
    access["version_sha256"] = hashlib.sha256(version.stdout).hexdigest()
    initial = request_input_versions(root, inputs)
    access["input_versions"] = initial
    nonce = strict_json(request)["nonce"]
    stream = RequestAccessStream(root, inputs, request, output, nonce)
    pipe = output.with_suffix(".access-pipe")
    os.mkfifo(pipe)
    descriptor = os.open(pipe, os.O_RDWR | os.O_NONBLOCK)
    stopped = threading.Event()

    def collect():
        pending = b""
        try:
            while True:
                ready, _, _ = select.select([descriptor], [], [], 0.05)
                if not ready:
                    if stopped.is_set():
                        break
                    continue
                chunk = os.read(descriptor, 65536)
                pending += chunk
                if len(pending) > 1024 * 1024:
                    stream.failed = True
                    pending = b""
                while b"\n" in pending:
                    line, pending = pending.split(b"\n", 1)
                    if line:
                        stream.consume(line)
            if pending:
                stream.failed = True
        except (OSError, ValueError):
            stream.failed = True

    observer = threading.Thread(target=collect, name="owned-request-access", daemon=True)
    observer.start()
    cleaned = False
    try:
        code, cleaned = owned_run(
            [tool, *REQUEST_TRACE_OPTIONS, "-o", str(pipe), *command],
            cwd=root,
            env=env,
            timeout=timeout,
            process_record=process_record,
            cancellation=cancellation,
            observer_cancel=stream.cancel_owned_tracees,
        )
        return_code = code
    finally:
        primary = sys.exc_info()[1]
        finalization_error = None

        def attempt(stage, operation):
            nonlocal finalization_error
            try:
                return operation()
            except BaseException as error:
                # Finalization cannot replace the actual owned-run exception.
                # Retain the first secondary object and only closed stage codes.
                if finalization_error is None:
                    finalization_error = error
                if stage not in access["finalization_failures"]:
                    access["finalization_failures"].append(stage)
                return None

        stopped.set()
        attempt("join", lambda: observer.join(timeout=2))
        attempt("close", lambda: os.close(descriptor))
        attempt("unlink", pipe.unlink)
        finished = attempt("finish", stream.finish)
        complete, actors = finished if finished is not None else (False, [])
        access.update(
            accesses=stream.accesses,
            reads=stream.reads,
            probe_accesses=stream.probes,
            subject_probes=stream.subject_reads,
            subject_descendants=stream.subject_children,
            actors=actors,
        )
        unchanged = attempt(
            "input-binding", lambda: request_input_versions(root, inputs) == initial
        )
        if not cleaned and process_record.is_file():
            process = attempt("process-record", lambda: strict_json(process_record))
            cleaned = process is not None and process.get("cleanup") is True
        access["complete"] = (
            cleaned
            and complete
            and not observer.is_alive()
            and unchanged is True
            and not access["finalization_failures"]
        )
        attempt("publication", lambda: publish(carrier, access))
        if "publication" in access["finalization_failures"]:
            access["complete"] = False
            # A failure after atomic replacement may have left a prior view.
            # Attempt one closed incomplete publication; never treat retry as proof.
            attempt("publication", lambda: publish(carrier, access))
        if finalization_error is not None:
            access["complete"] = False
            if primary is None:
                raise finalization_error
    return return_code, cleaned, access


def snapshot_base(root: Path, base: str, destination: Path) -> None:
    """Copy regular objects first, then confined Git links without dereferencing."""
    archive = subprocess.check_output(
        ["git", "-C", str(root), "archive", base], env=git_environment()
    )
    with tarfile.open(fileobj=io.BytesIO(archive)) as carrier:
        members = carrier.getmembers()
        links = {m.name for m in members if m.issym()}
        paths = []
        for member in members:
            name = public_path(member.name.rstrip("/"))
            target = destination / name
            if any(str(parent) in links for parent in name.parents):
                raise EvidenceError("indirect-base-source-parent")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                paths.append(str(name))
                target.parent.mkdir(parents=True, exist_ok=True)
                with carrier.extractfile(member) as stream:
                    target.write_bytes(stream.read())
                target.chmod(member.mode & 0o777)
            elif not member.issym():
                raise EvidenceError("indirect-base-source")
        for member in members:
            if member.issym():
                name = public_path(member.name)
                target = destination / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.symlink_to(member.linkname)
                paths.append(str(name))
        source_records(destination, sorted(paths))


def snapshot_current(root: Path, destination: Path, inputs: list[dict]) -> None:
    """Preserve link bodies/modes and complete target closure in an owned copy."""
    for row in inputs:
        if row["mode"] == "120000":
            continue
        target = destination / public_path(row["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / row["path"], target)
    for row in inputs:
        if row["mode"] == "120000":
            target = destination / public_path(row["path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(row["link"])
    if source_records(destination, [r["path"] for r in inputs]) != inputs:
        raise EvidenceError("owned-copy-source-mismatch")


def artifact(path: Path, output: Path) -> dict:
    return {
        "path": str(path.relative_to(output)),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "bytes": path.stat().st_size,
    }


def prove(root: Path, config: dict | Path, output: Path, *, timeout: float = 60) -> dict:
    if output.exists():
        raise EvidenceError("output-already-exists")
    output.mkdir(parents=True)
    receipt = {
        "status": "UNKNOWN",
        "restored": False,
        "cleanup": False,
        "category": "proof-started",
    }
    publish(output / "receipt.json", receipt)
    lock = (output / "run.lock").open("a+")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    deadline = None
    previous_handlers = {}
    journal = []
    owned = None
    nonce = secrets.token_hex(32)
    cancellation = threading.Event()

    def interrupted(_signum, _frame):
        cancellation.set()
        raise InterruptedError

    for signum in [signal.SIGTERM, signal.SIGINT]:
        previous_handlers[signum] = signal.signal(signum, interrupted)
    try:
        if type(timeout) not in {int, float} or not 0 < timeout <= 300:
            raise EvidenceError("invalid-proof-total-timeout")
        deadline = time.monotonic() + timeout
        if isinstance(config, Path):
            config = strict_json(config)
        required = {
            "base",
            "scope",
            "removed",
            "survivors",
            "cluster",
            "bead",
            "contract",
            "mapping",
        }
        fields(config, required | ({"lineage"} if "lineage" in config else set()))
        paired = config.get("lineage")
        if paired is not None:
            import condensation_lineage as lineage

            lineage.request(paired)
        if any(
            not isinstance(config[k], str) or not config[k] for k in ("cluster", "bead", "base")
        ):
            raise EvidenceError("config-owner-or-base")
        contract = config["contract"]
        fields(contract, {"class", "cites"})
        if (
            contract["class"] not in PROTECTED
            or contract["class"] == "migration"
            or not isinstance(contract["cites"], list)
            or not contract["cites"]
            or any(not isinstance(c, str) or not c for c in contract["cites"])
            or len(set(contract["cites"])) != len(contract["cites"])
        ):
            raise EvidenceError("unsupported-or-malformed-protected-contract")
        initial_head = git(root, "rev-parse", "HEAD")
        initial_tree = git(root, "rev-parse", "HEAD^{tree}")
        paths = tracked_paths(root)
        inputs = source_records(root, paths)
        scope = config["scope"]
        if not isinstance(scope, list) or not scope or len(set(scope)) != len(scope):
            raise EvidenceError("empty-or-aliased-scope")
        for path in scope:
            public_path(path)
            if (
                path not in paths
                or not path.endswith(".py")
                or any(c in path for c in "*?[]")
                or test_path(path)
                or Path(path).name.startswith("test_")
                or Path(path).name == "conftest.py"
                or path
                in {
                    "scripts/condense_evidence.py",
                    "scripts/check_condensation_ledger.py",
                    "scripts/condensation_lineage.py",
                }
                or input_record(root, path)["mode"] == "120000"
            ):
                raise EvidenceError("scope-not-owned-production-python-source")
        selections = {kind: config[kind] for kind in ["removed", "survivors"]}
        if any(
            not isinstance(rows, list)
            or not rows
            or len(set(rows)) != len(rows)
            or any(not isinstance(n, str) or "[" in n for n in rows)
            for rows in selections.values()
        ):
            raise EvidenceError("selection-needs-static-whole-case-owners")
        overlap = set(selections["removed"]) & set(selections["survivors"])
        if set(config["mapping"]) != set(selections["removed"]):
            raise EvidenceError("unmapped-removed-owner")
        for node, row in config["mapping"].items():
            if (
                set(row) != {"survivors", "reason"}
                or not row["reason"]
                or not row["survivors"]
                or not set(row["survivors"]) <= set(selections["survivors"])
            ):
                raise EvidenceError("unmapped-survivor")
            if node in overlap and row["survivors"] != [node]:
                raise EvidenceError("same-owner-context-needs-exact-self-mapping")
        base = git(root, "rev-parse", config["base"])
        if overlap:
            same_owner_context_accounts(root, base, overlap)
        runs, baseline = [], {}
        killed = {"removed": [], "survivors": []}
        with tempfile.TemporaryDirectory(prefix="condense-owned-", dir=output) as directory:
            owned = Path(directory)
            copies = {kind: owned / kind for kind in selections}
            for copy_root in copies.values():
                copy_root.mkdir()
            snapshot_base(root, base, copies["removed"])
            snapshot_current(root, copies["survivors"], inputs)
            if paired is None and any(
                input_record(copies["removed"], p) != input_record(copies["survivors"], p)
                for p in scope
            ):
                raise EvidenceError("changed-production-scope-needs-independent-lineage-proof")
            base_paths = tracked_paths(root, base)
            before_inputs = source_records(copies["removed"], base_paths)
            scopes = {"removed": paired["scope"] if paired else scope, "survivors": scope}
            if paired:
                for path in scopes["removed"]:
                    if (
                        path not in base_paths
                        or test_path(path)
                        or Path(path).name.startswith("test_")
                        or Path(path).name == "conftest.py"
                        or input_record(copies["removed"], path)["mode"] == "120000"
                    ):
                        raise EvidenceError("before-scope-not-owned-production-python-source")
                if any(r["current"]["path"] not in scope for r in paired["functions"]):
                    raise EvidenceError("unbound-current-function-lineage")
                points = lineage.alignment(
                    {p: (copies["removed"] / p).read_bytes().decode() for p in scopes["removed"]},
                    {p: (copies["survivors"] / p).read_bytes().decode() for p in scope},
                    paired["functions"],
                )
            old_links = {
                r["path"]: r
                for r in source_records(copies["removed"], base_paths)
                if r["mode"] == "120000"
            }
            new_links = {r["path"]: r for r in inputs if r["mode"] == "120000"}
            if old_links != new_links:
                raise EvidenceError("changed-source-link-needs-independent-lineage-proof")
            control_inputs = execution_request_inputs(root, inputs)
            production_dependencies(before_inputs, inputs, scope, paired, control_inputs)
            publish(
                output / "journal.json",
                {"owned_copy": str(owned), "status": "UNKNOWN", "mutations": journal},
            )

            def execute(kind, label):
                path = output / (label + ".json")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise EvidenceError("proof-total-timeout")
                run = suite(
                    copies[kind],
                    selections[kind],
                    scopes[kind],
                    nonce,
                    path,
                    label,
                    remaining,
                    control_inputs,
                    cancellation,
                )
                run["side"] = kind
                publish(path, run)
                run["artifact"] = artifact(path, output)
                run["coverage_artifact"] = artifact(path.with_suffix(".coverage"), output)
                return run

            def execute_sides(label):
                # The two source copies, requests, SQLite files and process groups
                # are disjoint. Every phase still executes freshly under the same
                # total deadline; joining both sides also completes failed cleanup.
                with ThreadPoolExecutor(max_workers=2) as pool:
                    pending = {kind: pool.submit(execute, kind, label(kind)) for kind in selections}
                    try:
                        sides = {future: kind for kind, future in pending.items()}
                        observed = {
                            sides[future]: future.result() for future in as_completed(sides)
                        }
                    except BaseException:
                        cancellation.set()
                        raise
                # Completion order only controls prompt failure observation;
                # successful carriers retain the original deterministic side order.
                completed = {kind: observed[kind] for kind in selections}
                runs.extend(completed.values())
                return completed

            baselines = execute_sides(lambda kind: kind + "-baseline")
            for kind in selections:
                run = baselines[kind]
                if run["exit"] != 0:
                    raise EvidenceError("baseline-not-healthy")
                baseline[kind] = run
                if {r["node"] for r in run["cases"]} != set(selections[kind]):
                    raise EvidenceError("missing-selected-case-owner")
            for owner in overlap:
                populations = [
                    {(r["key"], r["node"]) for r in baseline[side]["cases"] if r["node"] == owner}
                    for side in ("removed", "survivors")
                ]
                if populations[0] != populations[1]:
                    raise EvidenceError("same-owner-case-population-change")
            generated = mutants(copies["removed"], scopes["removed"])
            if not generated:
                raise EvidenceError("empty-or-unbounded-mutation-scope")
            sources = []
            for path in scopes["removed"]:
                saved = output / ("source-" + canonical(path) + ".before")
                saved.write_bytes((copies["removed"] / path).read_bytes())
                sources.append({"path": path, "artifact": artifact(saved, output)})
            pairs = (
                lineage.paired_mutants(points, generated, mutants(copies["survivors"], scope))
                if paired
                else [(m, m) for m in generated]
            )
            batches = mutation_batches([old["id"] for old, _ in pairs])
            for batch in batches:
                if deadline - time.monotonic() <= 0:
                    raise EvidenceError("proof-total-timeout")
                offset = batch["index"] * 64
                for mutant, current_mutant in pairs[offset : offset + 64]:
                    mutations = {"removed": mutant, "survivors": current_mutant}
                    originals, records = {}, {}
                    for kind, change in mutations.items():
                        target = copies[kind] / change["path"]
                        body, mode = target.read_bytes(), target.stat().st_mode & 0o777
                        originals[kind] = (body, mode)
                        saved = output / (mutant["id"] + "-" + kind + ".before")
                        saved.write_bytes(body)
                        records[kind] = {k: v for k, v in change.items() if k != "body"} | {
                            "before_file": saved.name,
                            "before_sha256": hashlib.sha256(body).hexdigest(),
                            "after_sha256": hashlib.sha256(change["body"]).hexdigest(),
                            "before_mode": mode,
                            "status": "UNKNOWN",
                        }
                    journal.append(
                        records["removed"] | ({"current": records["survivors"]} if paired else {})
                    )
                    publish(
                        output / "journal.json",
                        {
                            "owned_copy": str(owned),
                            "status": "UNKNOWN",
                            "mutations": journal,
                            "batches": batches,
                        },
                    )
                    try:
                        for kind, copy_root in copies.items():
                            change = mutations[kind]
                            (copy_root / change["path"]).write_bytes(change["body"])
                        measured = execute_sides(lambda kind: mutant["id"] + "-" + kind)
                        for kind in selections:
                            run = measured[kind]
                            if {(r["key"], r["node"]) for r in run["cases"]} != {
                                (r["key"], r["node"]) for r in baseline[kind]["cases"]
                            }:
                                raise EvidenceError("changed-case-population")
                            if run["exit"] == 1 and any(
                                r["phases"]["call"] == "assertion-failed" for r in run["cases"]
                            ):
                                killed[kind].append(mutant["id"])
                            elif run["exit"] != 0:
                                raise EvidenceError("nonsemantic-mutation-outcome")
                    finally:
                        for kind, copy_root in copies.items():
                            body, mode = originals[kind]
                            path = copy_root / mutations[kind]["path"]
                            path.write_bytes(body)
                            path.chmod(mode)
                            if path.read_bytes() != body or path.stat().st_mode & 0o777 != mode:
                                raise EvidenceError("mutation-restore-unknown")
                    restorations = execute_sides(
                        lambda kind: mutant["id"] + "-" + kind + "-restored"
                    )
                    for kind in selections:
                        restored = restorations[kind]
                        if restored["exit"] != 0 or restored["cases"] != baseline[kind]["cases"]:
                            raise EvidenceError("restored-baseline-not-healthy")
                    journal[-1]["status"] = "RESTORED"
                    if paired:
                        journal[-1]["current"]["status"] = "RESTORED"
                    publish(
                        output / "journal.json",
                        {
                            "owned_copy": str(owned),
                            "status": "UNKNOWN",
                            "mutations": journal,
                            "batches": batches,
                        },
                    )
                batch["status"] = "RESTORED"
                publish(
                    output / "journal.json",
                    {
                        "owned_copy": str(owned),
                        "status": "UNKNOWN",
                        "mutations": journal,
                        "batches": batches,
                    },
                )
            mapping = {owner: row["survivors"] for owner, row in config["mapping"].items()}
            residue = (
                lineage.residue(baseline["removed"], baseline["survivors"], mapping, points)
                if paired
                else named_coverage_residue(
                    baseline["removed"],
                    baseline["survivors"],
                    mapping,
                )
            )
            lost = sorted(set(killed["removed"]) - set(killed["survivors"]))
            # Check each removed owner against its named survivors, beyond the union of kills.
            for mutant_id in killed["removed"]:
                old_run = next(r for r in runs if r["id"] == mutant_id + "-removed")
                new_run = next(r for r in runs if r["id"] == mutant_id + "-survivors")
                old_owners = {
                    r["node"] for r in old_run["cases"] if r["phases"]["call"] == "assertion-failed"
                }
                new_owners = {
                    r["node"] for r in new_run["cases"] if r["phases"]["call"] == "assertion-failed"
                }
                if any(not new_owners & set(config["mapping"][n]["survivors"]) for n in old_owners):
                    lost.append(mutant_id)
            binding = {
                "base": base,
                "head": initial_head,
                "tree": initial_tree,
                "inputs": inputs,
                "inputs_hash": canonical(inputs),
                "nonce": nonce,
                "selection": selections,
                "scope": scope,
                "tools": baseline["removed"]["tools"],
            }
            if paired:
                binding["lineage"] = {**paired, "inputs": before_inputs}
            receipt = {
                "status": "PASS" if not residue and not lost and killed["removed"] else "REFUSED",
                "mode": "cov+mut",
                "restored": True,
                "cleanup": True,
                "coverage": {
                    "core": "ctrace",
                    "branch": True,
                    "residue_arcs": len(residue),
                    "residue": residue,
                    "waived": [],
                },
                "mutation": {
                    "seed": 0,
                    "generated": len(generated),
                    "sources": sources,
                    "batches": batches,
                    "mutants": journal,
                    "killed_by_removed": killed["removed"],
                    "killed_by_survivors": killed["survivors"],
                    "lost": sorted(set(lost)),
                    "unknown": [],
                },
                "runs": runs,
                "binding": binding,
            }
            if (
                git(root, "rev-parse", "HEAD") != initial_head
                or git(root, "rev-parse", "HEAD^{tree}") != initial_tree
                or tracked_paths(root) != paths
                or any(input_record(root, row["path"]) != row for row in inputs)
            ):
                raise EvidenceError("live-source-changed-during-proof")
            publish(
                output / "journal.json",
                {
                    "owned_copy": str(owned),
                    "status": "RESTORED",
                    "mutations": journal,
                    "batches": batches,
                },
            )
        ledger = {
            "schema": "condensation/v1",
            "cluster": config["cluster"],
            "bead": config["bead"],
            "contract": config["contract"],
            "binding": binding,
            "proof": {k: v for k, v in receipt.items() if k != "binding"},
            "seconds": {"before": None, "after": None},
            "removed": [
                {
                    "node": n,
                    "params": sum(r["node"] == n for r in baseline["removed"]["cases"]),
                    **config["mapping"][n],
                }
                for n in selections["removed"]
            ],
            "survivors": [
                {"node": n, "params": sum(r["node"] == n for r in baseline["survivors"]["cases"])}
                for n in selections["survivors"]
            ],
        }
        publish(output / "ledger.json", ledger)
    except (
        EvidenceError,
        InterruptedError,
        KeyboardInterrupt,
        OSError,
        KeyError,
        TypeError,
        ValueError,
        SyntaxError,
    ) as exc:
        restored = owned is not None and not owned.exists()
        cleanup = all(strict_json(p).get("cleanup") is True for p in output.glob("*.process.json"))
        receipt = {
            "status": "UNKNOWN",
            "restored": restored,
            "cleanup": cleanup,
            "category": str(exc) if isinstance(exc, EvidenceError) else type(exc).__name__,
        }
        raise EvidenceError("proof-unknown-see-durable-receipt") from exc
    finally:
        publish(output / "receipt.json", receipt)
        if os.environ.get("TEST_EVIDENCE_DIR") and receipt.get("status") != "PASS":
            # A direct Python caller may fail before the required reader runs.
            # Publish only the shared closed diagnostic, preserving the proof's
            # original result/error and ordinary CLI stdout contract.
            try:
                fresh_proof_refusal(
                    config,
                    output,
                    {
                        "producer_binding": {
                            "head": locals().get("initial_head"),
                            "tree": locals().get("initial_tree"),
                            "program_sha256": hashlib.sha256(
                                Path(__file__).read_bytes()
                            ).hexdigest(),
                            "nonce": nonce,
                        }
                    },
                    EvidenceError("direct-producer-refusal"),
                    publication_fd=-1,
                    emit=False,
                )
            except (OSError, ValueError, TypeError, RecursionError, UnicodeError):
                # Unavailable diagnostic custody cannot replace the primary
                # proof refusal or change restoration/group-exit admission.
                pass
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
        lock.close()
    return receipt


def main(argv=None):
    arguments = sys.argv[1:] if argv is None else argv
    if arguments and arguments[0] == "_launch":
        if sys.stdin.buffer.read(1) != b"1":
            return 2
        os.execvpe(arguments[1], arguments[1:], os.environ)
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    run = sub.add_parser("prove")
    run.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    run.add_argument("--config", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--timeout", type=float, default=60)
    recovery = sub.add_parser("recover")
    recovery.add_argument("output", type=Path)
    child = sub.add_parser("_worker")
    child.add_argument("request", type=Path)
    args = parser.parse_args(argv)
    if args.mode == "_worker":
        return worker(args.request)
    if args.mode == "recover":
        try:
            print(json.dumps(recover(args.output)))
            return 0
        except EvidenceError as exc:
            print(json.dumps({"status": "UNKNOWN", "category": str(exc)}))
            return 2
    if not 0 < args.timeout <= 300:
        parser.error("timeout must be finite and in (0,300]")
    try:
        receipt = prove(
            args.repo_root.resolve(),
            args.config,
            args.output.resolve(),
            timeout=args.timeout,
        )
    except EvidenceError as exc:
        print(json.dumps({"status": "UNKNOWN", "category": str(exc)}))
        return 2
    print(
        json.dumps(
            {
                "status": receipt["status"],
                "residue_arcs": receipt["coverage"]["residue_arcs"],
                "lost": receipt["mutation"]["lost"],
            }
        )
    )
    return 0 if receipt["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
