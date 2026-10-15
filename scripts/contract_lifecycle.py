"""Kernel-owned descendant custody for exact authorized contract execution only.

PTRACE_O_EXITKILL follows descendants across sessions. Process records, not I/O,
are observed; raw syscall arguments never leave the in-memory reader.
"""

from __future__ import annotations

import hashlib
import os
import re
import select
import shutil
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

import check_condensation_ledger as ledger
import condense_evidence as producer

OPTIONS = [
    "-q",
    "-f",
    "--kill-on-exit",
    "-s",
    "4096",
    "-xx",
    "-e",
    "signal=none",
    "-e",
    "trace=clone,clone3,fork,vfork,execve,execveat,exit,exit_group,setsid,setpgid",
]


def tool_identity():
    tool = shutil.which("strace")
    if tool is None:
        raise ledger.EvidenceError("contract-lifecycle-tool-unavailable")
    tool = str(Path(tool).resolve())
    help_ = subprocess.run([tool, "--help"], capture_output=True, timeout=5, check=True)
    if b"--kill-on-exit" not in help_.stdout:
        raise ledger.EvidenceError("contract-lifecycle-exitkill-unavailable")
    return tool, {
        "sha256": hashlib.sha256(Path(tool).read_bytes()).hexdigest(),
        "options_sha256": ledger.canonical(OPTIONS),
    }


def executable_record(path, *, header=False):
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ledger.EvidenceError("nonregular-contract-executable")
        prefix = stream.read(256)
        stream.seek(0)
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
        after = os.fstat(stream.fileno())
        named = os.stat(path, follow_symlinks=False)

    def stable(info):
        return (
            info.st_dev,
            info.st_ino,
            info.st_mode,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
        )

    if stable(before) != stable(after) or stable(after) != stable(named):
        raise ledger.EvidenceError("unstable-contract-executable")
    record = {"path": path, "mode": before.st_mode, "sha256": digest}
    return (record, prefix) if header else record


def interpreter(prefix):
    if prefix.startswith(b"\x7fELF"):
        return None
    shebang = re.match(rb"^#![ \t]*(/[^ \t\r\n\x00]+)(?:[ \t]+[^\r\n]*)?\n", prefix)
    if shebang is None:
        raise ledger.EvidenceError("unproved-contract-native-image")
    return str(Path(os.fsdecode(shebang[1])).resolve(strict=True))


class Stream:
    def __init__(self):
        self.actors, self.exits, self.parents, self.pending = set(), set(), {}, {}
        self.identities, self.tracers = {}, {}
        self.executables, self.execs = {}, {}
        self.failed = self.eof = False

    def consume(self, raw):
        try:
            match = re.fullmatch(rb"(\d+) +(.+)", raw)
            if match is None:
                raise ValueError
            actor, statement = int(match[1]), match[2].decode("ascii")
            self.actors.add(actor)
            if len(self.actors) > 100000:
                raise ValueError
            if actor not in self.identities:
                identity = producer.process_identity(actor)
                if identity is not None:
                    self.identities[actor] = identity
                    status = Path(f"/proc/{actor}/status").read_text()
                    self.tracers[actor] = int(re.search(r"^TracerPid:\s+(\d+)$", status, re.M)[1])
            if statement.startswith("+++ "):
                if actor in self.exits or not re.fullmatch(
                    r"\+\+\+ (exited with \d+|killed by SIG[A-Z0-9]+(?: \(core dumped\))?) \+\+\+",
                    statement,
                ):
                    raise ValueError
                self.pending.pop(actor, None)  # interrupted exit_group has no syscall result
                self.exits.add(actor)
                return
            if actor in self.exits:
                raise ValueError
            if statement.endswith(" <unfinished ...>"):
                if actor in self.pending:
                    raise ValueError
                self.pending[actor] = statement[: -len(" <unfinished ...>")]
                return
            resumed = re.match(r"<\.\.\. (\w+) resumed>(.*)", statement)
            if resumed:
                prefix = self.pending.pop(actor)
                if not prefix.startswith(resumed[1] + "("):
                    raise ValueError
                statement = prefix + resumed[2]
            elif actor in self.pending:
                raise ValueError
            call = re.fullmatch(r"(\w+)\((.*)\) += (.+)", statement)
            if call is None:
                raise ValueError
            name, args, result = call.groups()
            number = re.match(r"(-?\d+)", result)
            if name == "execveat" and number and int(number[1]) == 0:
                raise ValueError  # dirfd/AT_EMPTY_PATH execution is unsupported
            if name == "execve" and number and int(number[1]) == 0:
                pathname = re.match(r'"((?:\\x[0-9a-f]{2})*)",', args)
                if pathname is None or not pathname[1]:
                    raise ValueError
                path = Path(os.fsdecode(bytes.fromhex(pathname[1].replace("\\x", ""))))
                if not path.is_absolute():
                    path = Path(f"/proc/{actor}/cwd").resolve(strict=True) / path
                observed = self.executable(path)
                try:
                    native = Path(f"/proc/{actor}/exe").resolve(strict=True)
                except FileNotFoundError:
                    pass  # ELF or exact kernel shebang interpreter is already bound
                else:
                    observed |= self.executable(native)
                self.execs.setdefault(actor, []).append(sorted(observed))
            if name in {"clone", "clone3", "fork", "vfork"} and number and int(number[1]) > 0:
                child = int(number[1])
                if child in self.parents:
                    raise ValueError
                self.parents[child] = actor
        except (OSError, ValueError, KeyError, TypeError, UnicodeError):
            self.failed = True

    def executable(self, path, seen=None):
        path = str(path.resolve(strict=True))
        seen = set() if seen is None else seen
        if path in seen or len(seen) >= 5:
            raise ledger.EvidenceError("unproved-contract-native-image")
        seen.add(path)
        record, prefix = executable_record(path, header=True)
        if path in self.executables and self.executables[path] != record:
            raise ValueError
        self.executables[path] = record
        native = interpreter(prefix)
        if native is not None:
            self.executable(Path(native), seen)
        return seen

    def rows(self, nonce):
        def key(pid):
            return hashlib.sha256((nonce + "\0" + str(pid)).encode()).hexdigest()

        return [
            {
                "key": key(pid),
                "parent": key(self.parents[pid]) if pid in self.parents else None,
                "exited": pid in self.exits,
                "execs": self.execs.get(pid, []),
            }
            for pid in sorted(self.actors)
        ]

    def gone(self):
        return all(
            (current := producer.process_identity(pid)) is None
            or current["start"] != identity["start"]
            or current["state"] in {"Z", "X"}
            for pid, identity in self.identities.items()
        )


def run(command, root, request, output, env, timeout, inputs, cancellation=None):
    if inputs:
        raise ledger.EvidenceError("contract-lifecycle-is-not-read-exclusion")
    tool, identity = tool_identity()
    carrier = output.with_suffix(".lifecycle.json")
    nonce = ledger.strict_json(request)["nonce"]
    record = {
        "nonce": nonce,
        "protocol": "exitkill-process-v1",
        "tool": identity,
        "actors": [],
        "executables": [],
        "tracer": None,
        "root": None,
        "eof": False,
        "complete": False,
    }
    producer.publish(carrier, record)
    pipe = output.with_suffix(".lifecycle-pipe")
    os.mkfifo(pipe)
    descriptor = os.open(pipe, os.O_RDONLY | os.O_NONBLOCK)
    stopped, stream = threading.Event(), Stream()

    def collect():
        pending, started = b"", False
        try:
            while True:
                ready, _, _ = select.select([descriptor], [], [], 0.05)
                if not ready:
                    if stopped.is_set():
                        break
                    continue
                chunk = os.read(descriptor, 65536)
                if not chunk:
                    if started:
                        stream.eof = True
                        break
                    if stopped.wait(0.01):
                        break
                    continue
                started = True
                pending += chunk
                if len(pending) > 1024 * 1024:
                    raise ValueError
                while b"\n" in pending:
                    line, pending = pending.split(b"\n", 1)
                    stream.consume(line)
            if pending:
                stream.failed = True
        except (OSError, ValueError):
            stream.failed = True

    observer = threading.Thread(target=collect, name="contract-lifecycle", daemon=True)
    observer.start()
    process_record = output.with_suffix(".process.json")
    argv = [tool, *OPTIONS, "-o", str(pipe), *command]
    try:
        code, cleaned = producer.owned_run(
            argv,
            cwd=root,
            env=env,
            timeout=timeout,
            process_record=process_record,
            cancellation=cancellation,
        )
    finally:
        primary = sys.exc_info()[1]
        try:
            # owned_run has terminated the owned tracer first. EXITKILL settles all
            # tracees even when their sessions differ. Never stop a live tracer early.
            observer.join(timeout=2)
            stopped.set()
            observer.join(timeout=0.1)
            os.close(descriptor)
            pipe.unlink()
            deadline = time.monotonic() + 1
            while not stream.gone() and time.monotonic() < deadline:
                time.sleep(0.01)
            process = ledger.strict_json(process_record)
            roots = stream.actors - stream.parents.keys()
            if len(roots) == 1:
                root_pid = next(iter(roots))
                record["root"] = {
                    "key": hashlib.sha256((nonce + "\0" + str(root_pid)).encode()).hexdigest(),
                    "identity": stream.identities.get(root_pid),
                    "tracer_pid": stream.tracers.get(root_pid),
                }
            record.update(
                executables=sorted(stream.executables.values(), key=lambda r: r["path"]),
                actors=stream.rows(ledger.strict_json(request)["nonce"]),
                tracer=process,
                eof=stream.eof,
            )
            stable_tools = all(executable_record(p) == r for p, r in stream.executables.items())
            record["complete"] = (
                stable_tools
                and bool(stream.executables)
                and not stream.failed
                and stream.eof
                and not observer.is_alive()
                and not stream.pending
                and len(roots) == 1
                and stream.actors == stream.exits
                and set(stream.parents.values()) <= stream.actors
                and set(stream.parents) <= stream.actors
                and stream.gone()
                and process.get("cleanup") is True
                and all(stream.tracers.get(pid) == process["identity"]["pid"] for pid in roots)
            )
            producer.publish(carrier, record)
        except BaseException:
            if primary is None:
                raise ledger.EvidenceError("contract-lifecycle-finalization-unknown") from None
    if not record["complete"]:
        raise ledger.EvidenceError("contract-lifecycle-incomplete")
    access = producer.request_access_record(set())
    producer.publish(output.with_suffix(".access.json"), access)
    return code, cleaned, access


def validate(output, command, nonce):
    """Independently consume the closed custody carrier and exact tool command."""
    record = ledger.strict_json(output.with_suffix(".lifecycle.json"))
    ledger.fields(
        record,
        {"nonce", "protocol", "tool", "actors", "executables", "tracer", "root", "eof", "complete"},
    )
    tool, identity = tool_identity()
    process = ledger.strict_json(output.with_suffix(".process.json"))
    expected = [tool, *OPTIONS, "-o", str(output.with_suffix(".lifecycle-pipe")), *command]
    if (
        record["nonce"] != nonce
        or record["protocol"] != "exitkill-process-v1"
        or record["tool"] != identity
        or record["tracer"] != process
        or process.get("cleanup") is not True
        or process["command_sha256"] != ledger.canonical(expected)
        or record["eof"] is not True
        or record["complete"] is not True
    ):
        raise ledger.EvidenceError("unproved-contract-lifecycle")
    tools = record["executables"]
    ledger.identities(tools, "path")
    tool_paths = {r["path"] for r in tools}
    for row in tools:
        ledger.fields(row, {"path", "mode", "sha256"})
        actual, prefix = executable_record(row["path"], header=True)
        native = interpreter(prefix)
        if actual != row or (native is not None and native not in tool_paths):
            raise ledger.EvidenceError("changed-contract-executable")
    if not tools:
        raise ledger.EvidenceError("changed-contract-executable")
    rows = record["actors"]
    keys = ledger.identities(rows, "key")
    if len([r for r in rows if r["parent"] is None]) != 1:
        raise ledger.EvidenceError("unproved-contract-lifecycle")
    root = record["root"]
    ledger.fields(root, {"key", "identity", "tracer_pid"})
    source = root["identity"]
    ledger.fields(source, {"pid", "start", "state", "group"})
    if (
        type(source["pid"]) is not int
        or type(root["tracer_pid"]) is not int
        or root["tracer_pid"] != process["identity"]["pid"]
        or root["key"] != hashlib.sha256((nonce + "\0" + str(source["pid"])).encode()).hexdigest()
        or root["key"] != next(r["key"] for r in rows if r["parent"] is None)
    ):
        raise ledger.EvidenceError("unproved-contract-lifecycle")
    current = producer.process_identity(source["pid"])
    if (
        current is not None
        and current["start"] == source["start"]
        and current["state"] not in {"Z", "X"}
    ):
        raise ledger.EvidenceError("unproved-contract-lifecycle")
    parents, observed = {}, set()
    for row in rows:
        ledger.fields(row, {"key", "parent", "exited", "execs"})
        if not ledger.HEX.fullmatch(row["key"]) or row["exited"] is not True:
            raise ledger.EvidenceError("unproved-contract-lifecycle")
        if row["parent"] is not None and row["parent"] not in keys:
            raise ledger.EvidenceError("unproved-contract-lifecycle")
        if not isinstance(row["execs"], list):
            raise ledger.EvidenceError("unproved-contract-lifecycle")
        for event in row["execs"]:
            if not isinstance(event, list) or not event or len(set(event)) != len(event):
                raise ledger.EvidenceError("unproved-contract-lifecycle")
            observed.update(event)
        if row["parent"] is None and not row["execs"]:
            raise ledger.EvidenceError("unproved-contract-lifecycle")
        parents[row["key"]] = row["parent"]
    for key in keys:
        seen = set()
        while key is not None:
            if key in seen:
                raise ledger.EvidenceError("unproved-contract-lifecycle")
            seen.add(key)
            key = parents[key]

    if observed != tool_paths:
        raise ledger.EvidenceError("incomplete-contract-executable-population")
