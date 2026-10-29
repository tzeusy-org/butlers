"""Bounded incomplete CI observations, separate from every execution verifier.

Hooks enqueue only opaque closed events. This module never selects/reorders items,
changes reports, catches test exceptions, or exports raw diagnostic errors.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import re
import secrets
import stat
import subprocess
import sys
import threading
import time
from collections import Counter
from pathlib import Path

import pytest

SCHEMA = "ci-watchdog-diagnostic.v1"
PROFILE = "original-pr4484-unit1"
SUBJECT = "f574f7283daa99fe1666d0b7acec368b1f4ac096"
TREE = "7a158bf1a98541110a8f904340d7def5737e4c5d"
PARENTS = ["d25a50bfe7f5498d6cd6832ee1257ea5d52054cd", "c4d9fc865d3ff5877e2ce608a8a70f06a18546c7"]
SCHEDULE = (0, 120, 300, 600, 900, 1200, 1440, 1560)
PHASES = ("setup", "call", "teardown")
MAX_BYTES = 16 * 1024 * 1024
MAX_COUNT = 2**31 - 1
SDK_KEYS = {
    "ACTIONS_RUNTIME_TOKEN",
    "ACTIONS_RESULTS_URL",
    "ACTIONS_RUNTIME_URL",
    "ACTIONS_CACHE_URL",
}
BINDING_KEYS = {
    "profile",
    "repository",
    "workflow",
    "run",
    "attempt",
    "provider_head",
    "capture_head",
    "capture_tree",
    "capture_bundle",
    "capture_lock",
    "capture_observer",
    "capture_action",
    "subject_head",
    "subject_tree",
    "subject_parents",
    "inventory_digest",
    "assignment_digest",
    "nonce",
    "child",
    "expected",
    "population_digest",
    "source_refs",
}
GAPS = {
    "observer_failure",
    "queue_overflow",
    "clock_regression",
    "counter_overflow",
    "frames_truncated",
    "stack_unavailable",
    "process_unavailable",
    "collection_mismatch",
    "duplicate_boundary",
    "missing_process",
    "transport_unknown",
}
TRANSPORT = {"pending", "finalized", "failed", "overlap", "unavailable", "cancelled"}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def node_digest(node, nonce):
    # This is the original identity function, not a second selector.
    from ci_shard_observer import node_digest as original

    return original(node, nonce)


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate diagnostic key")
        result[key] = value
    return result


def read_bytes(path: Path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
            raise ValueError("diagnostic carrier unavailable")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("diagnostic carrier oversized")
        return raw
    finally:
        os.close(descriptor)


def parse_bytes(raw):
    return json.loads(
        raw,
        object_pairs_hook=unique,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")),
    )


def read_json(path: Path):
    return parse_bytes(read_bytes(path))


def atomic(path, value, *, limit=MAX_BYTES):
    raw = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if len(raw) > limit:
        raise ValueError("diagnostic carrier oversized")
    temporary = path.with_name(path.name + ".staging")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(fd)
        os.replace(temporary, path)
    finally:
        os.close(fd)
        temporary.unlink(missing_ok=True)


def fields(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError("diagnostic closed schema mismatch")


def number(value):
    if type(value) is not int or not 0 <= value <= MAX_COUNT:
        raise ValueError("diagnostic integer malformed")


def hexvalue(value, length=64):
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{" + str(length) + "}", value) is None:
        raise ValueError("diagnostic digest malformed")


def git(root, *args):
    return (
        subprocess.check_output(["git", *args], cwd=root, stderr=subprocess.DEVNULL)
        .decode()
        .strip()
    )


def source_reference(root):
    action = root / ".github/actions/ci-shard-watchdog-capture"
    return {
        "capture_head": git(root, "rev-parse", "HEAD"),
        "capture_tree": git(root, "rev-parse", "HEAD^{tree}"),
        "capture_bundle": digest(
            {
                str(p.relative_to(action / "dist")): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted((action / "dist").glob("*"))
                if p.is_file()
            }
        )
        if (action / "dist").is_dir()
        else "0" * 64,
        "capture_observer": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "capture_action": hashlib.sha256((action / "src/index.js").read_bytes()).hexdigest()
        if (action / "src/index.js").is_file()
        else "0" * 64,
        "capture_lock": hashlib.sha256((action / "package-lock.json").read_bytes()).hexdigest()
        if (action / "package-lock.json").is_file()
        else "0" * 64,
    }


def reviewed_source_reference(root, head):
    """Resolve externally selected review pins from Git objects, never carrier fields.

    The reader's repository must contain the reviewed commit. Worktree bytes,
    downloaded inventories and snapshot seals cannot attest that commit's tree
    or bundle/lock/observer/action contents.
    """
    hexvalue(head, 40)
    if git(root, "rev-parse", "--verify", head + "^{commit}") != head:
        raise ValueError("reviewed capture commit unavailable")
    action = ".github/actions/ci-shard-watchdog-capture"

    def blob(name):
        record = git(root, "ls-tree", head, "--", name).split()
        if (
            len(record) != 4
            or record[0] not in ("100644", "100755")
            or record[1] != "blob"
            or record[3] != name
        ):
            raise ValueError("reviewed capture member unavailable")
        return subprocess.check_output(
            ["git", "show", f"{head}:{name}"], cwd=root, stderr=subprocess.DEVNULL
        )

    members = git(root, "ls-tree", "-r", "--name-only", head, "--", action + "/dist").splitlines()
    if not members or any(Path(name).parent.as_posix() != action + "/dist" for name in members):
        raise ValueError("reviewed capture bundle unavailable")
    return {
        "capture_head": head,
        "capture_tree": git(root, "rev-parse", head + "^{tree}"),
        "capture_bundle": digest(
            {Path(name).name: hashlib.sha256(blob(name)).hexdigest() for name in members}
        ),
        "capture_observer": hashlib.sha256(blob("scripts/ci_watchdog_diagnostics.py")).hexdigest(),
        "capture_action": hashlib.sha256(blob(action + "/src/index.js")).hexdigest(),
        "capture_lock": hashlib.sha256(blob(action + "/package-lock.json")).hexdigest(),
    }


def binding(data, assignment, *, capture_root):
    files = assignment["shards"]["unit"][0]["files"]
    expected = {node: name for name in files for node in data["lanes"]["unit"][name]}
    identity = data["identity"]
    return {
        "profile": PROFILE,
        "repository": identity["repository"],
        "workflow": identity["workflow"],
        "run": identity["run"],
        "attempt": identity["attempt"],
        "provider_head": os.environ.get("CI_WATCHDOG_PROVIDER_HEAD", identity["head"]),
        **source_reference(capture_root),
        "subject_head": identity["head"],
        "subject_tree": identity["tree"],
        "subject_parents": [],
        "inventory_digest": data["digest"],
        "assignment_digest": assignment["digest"],
        "nonce": data["nonce"],
        "child": "unit-1",
        "expected": expected,
        "population_digest": digest(expected),
        "source_refs": {},
    }


def validate_binding(bound):
    fields(bound, BINDING_KEYS)
    if bound["profile"] != PROFILE or bound["child"] != "unit-1":
        raise ValueError("diagnostic profile mismatch")
    for field in ("capture_head", "capture_tree", "subject_head", "subject_tree", "provider_head"):
        hexvalue(bound[field], 40)
    for field in (
        "capture_bundle",
        "capture_lock",
        "capture_observer",
        "capture_action",
        "inventory_digest",
        "assignment_digest",
        "nonce",
        "population_digest",
    ):
        hexvalue(bound[field])
    for field in ("run", "attempt"):
        if not isinstance(bound[field], str) or not re.fullmatch(
            r"[1-9][0-9]*|local", bound[field]
        ):
            raise ValueError("diagnostic attempt malformed")
    if not isinstance(bound["subject_parents"], list) or len(bound["subject_parents"]) > 2:
        raise ValueError("diagnostic lineage malformed")
    for value in bound["subject_parents"]:
        hexvalue(value, 40)
    for field in ("repository", "workflow"):
        if not isinstance(bound[field], str) or len(bound[field]) > 100:
            raise ValueError("diagnostic tracking malformed")
    if not isinstance(bound["expected"], dict) or not 0 < len(bound["expected"]) <= 50000:
        raise ValueError("diagnostic population unavailable")
    for node, name in bound["expected"].items():
        hexvalue(node)
        if (
            not isinstance(name, str)
            or not re.fullmatch(r"(?:tests|roster)/[A-Za-z0-9_./-]+\.py", name)
            or ".." in name
        ):
            raise ValueError("diagnostic file outside approved population")
    if not isinstance(bound["source_refs"], dict):
        raise ValueError("diagnostic static sources missing")
    for name, ref in bound["source_refs"].items():
        fields(ref, {"blob", "lines"})
        hexvalue(ref["blob"], 40)
        number(ref["lines"])
        if not re.fullmatch(r"[A-Za-z0-9_./-]+\.py", name) or ".." in name:
            raise ValueError("diagnostic static sources malformed")
    if digest(bound["expected"]) != bound["population_digest"]:
        raise ValueError("diagnostic population digest mismatch")


def safe_sources(root):
    result = {}
    raw = subprocess.check_output(["git", "ls-files", "-s", "-z"], cwd=root)
    for entry in raw.decode().split("\0"):
        if not entry:
            continue
        metadata, name = entry.split("\t", 1)
        mode, blob, _ = metadata.split()
        path = root / name
        if (
            mode not in {"100644", "100755"}
            or not name.endswith(".py")
            or not re.fullmatch(r"[A-Za-z0-9_./-]+", name)
            or path.is_symlink()
        ):
            continue
        # Working-source maps are fixed before execution, never read while sampling.
        raw = path.read_bytes()
        result[str(path.resolve())] = {
            "file": name,
            "blob": blob,
            "lines": max(1, len(raw.splitlines())),
        }
    return result


def configure(directory, bound, *, root):
    bound["source_refs"] = {
        v["file"]: {"blob": v["blob"], "lines": v["lines"]} for v in safe_sources(root).values()
    }
    validate_binding(bound)
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "states").mkdir()
    (directory / "checkpoints").mkdir()
    atomic(
        directory / "config.json",
        {
            "binding": bound,
            "root": str(root.resolve()),
            "sources": safe_sources(root),
            "token": secrets.token_hex(32),
        },
    )


def write_inventory(directory, data, assignment):
    directory.mkdir(parents=True, exist_ok=True)
    atomic(directory / "inventory.json", data)
    atomic(directory / "assignment.json", assignment)


def subject_environment(environment):
    return {key: value for key, value in environment.items() if key not in SDK_KEYS}


def observer_environment(directory):
    return {
        "CI_WATCHDOG_CONFIG": str(directory / "config.json"),
        "PYTEST_PLUGINS": ",".join(
            filter(None, [os.environ.get("PYTEST_PLUGINS"), "ci_watchdog_diagnostics"])
        ),
        "PYTHONPATH": os.pathsep.join(
            filter(None, [str(Path(__file__).resolve().parent), os.environ.get("PYTHONPATH")])
        ),
    }


def collect_correspondence(root, original):
    import tempfile

    from ci_partition import collect_inventory, load_advisory_weights, partition

    with tempfile.TemporaryDirectory(prefix="watchdog-collector-") as directory:
        output = Path(directory) / "observed.json"
        previous = dict(os.environ)
        try:
            os.environ.update(
                CI_WATCHDOG_COLLECT=json.dumps(
                    {
                        "output": str(output),
                        "old_nonce": original["inventory_nonce"],
                        "root": str(root.resolve()),
                        "parent": os.getpid(),
                        "parent_start": proc_sample(os.getpid())["start_ticks"],
                    }
                )
            )
            os.environ.update(observer_environment(Path(directory)))
            os.environ.pop("CI_WATCHDOG_CONFIG", None)
            data = collect_inventory(root=root)
        finally:
            os.environ.clear()
            os.environ.update(previous)
        pairs = read_json(output)
    plan = partition(data, load_advisory_weights(root / ".github/ci-test-weights.json"), root=root)
    files = plan["shards"]["unit"][0]["files"]
    expected = {node: name for name in files for node in data["lanes"]["unit"][name]}
    observed = [p for p in pairs if p["new"] in expected]
    if (
        sorted(files) != sorted(original["complete_expected_files"])
        or Counter(p["old"] for p in observed) != Counter(original["complete_expected_nodes"])
        or Counter(p["new"] for p in observed) != Counter(expected.keys())
        or any(expected[p["new"]] != p["file"] for p in observed)
    ):
        raise ValueError("fresh original population differs")
    return data, plan, observed


class CollectionObservation:
    def __init__(self, config):
        self.config = config

    def pytest_collection_finish(self, session):
        # No marker classification: join to the original collector's produced lanes later.
        config = self.config
        nonce = os.environ.get("CI_INVENTORY_NONCE")
        if not nonce:
            return
        pairs = [
            {
                "file": str(item.path.resolve().relative_to(Path(config["root"]).resolve())),
                "old": node_digest(item.nodeid, config["old_nonce"]),
                "new": node_digest(item.nodeid, nonce),
            }
            for item in session.items
        ]
        atomic(Path(config["output"]), pairs)


def proc_sample(pid):
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
        # The command name is discarded immediately and never persisted.
        values = raw[raw.rfind(")") + 2 :].split()
        return {
            "pid": pid,
            "ppid": int(values[1]),
            "start_ticks": int(values[19]),
            "state": values[0] if values[0] in "RSDZTWIPX" else "?",
            "threads": int(values[17]),
            "cpu_ticks": int(values[11]) + int(values[12]),
            "rss_pages": max(0, int(values[21])),
        }
    except (OSError, ValueError, IndexError):
        return None


class Observer:
    def __init__(self, config, private, role):
        self.config = config
        self.private = private
        self.bound = private["binding"]
        self.role = role
        self.pid = os.getpid()
        self.sample = proc_sample(self.pid)
        self.directory = Path(os.environ["CI_WATCHDOG_CONFIG"]).parent
        self.path = self.directory / "states" / f"{self.pid}.json"
        self.events = queue.Queue(maxsize=1024)
        self.stop = threading.Event()
        self.gaps = set()
        self.state = {
            "role": role,
            "worker": config.workerinput["workerid"] if role == "worker" else None,
            "process": self.sample,
            "sequence": 0,
            "elapsed_ms": 0,
            "collections": [],
            "controller_events": {"collections": {}, "departures": {}},
            "nodes": {},
            "frames": [],
            "stack_elapsed_ms": 0,
            "gaps": [],
            "finished": False,
        }
        self.started = time.monotonic_ns()
        self.writer = threading.Thread(target=self.work, daemon=True)
        self.writer.start()
        self.emit("configured")

    def emit(self, kind, node=None, phase=None, outcome=None):
        event = (
            kind,
            node,
            phase,
            outcome,
            max(0, (time.monotonic_ns() - self.started) // 1000000),
        )
        try:
            self.events.put_nowait(event)
        except queue.Full:
            self.gaps.add("queue_overflow")

    def frames(self):
        result = []
        frames = sys._current_frames()
        if len(frames) > 256:
            self.gaps.add("frames_truncated")
        for index, frame in enumerate(list(frames.values())[:256]):
            depth = 0
            while frame and depth < 64:
                approved = self.private["sources"].get(frame.f_code.co_filename)
                if approved and 0 < frame.f_lineno <= approved["lines"]:
                    result.append(
                        {
                            "thread": index,
                            "depth": depth,
                            "file": approved["file"],
                            "blob": approved["blob"],
                            "line": frame.f_lineno,
                        }
                    )
                frame = frame.f_back
                depth += 1
            if frame:
                self.gaps.add("frames_truncated")
        return result

    def work(self):
        last_write = 0
        last_stack = 0
        last_request = -1
        try:
            while not self.stop.is_set() or not self.events.empty():
                try:
                    kind, node, phase, outcome, clock = self.events.get(timeout=0.05)
                    if clock < self.state["elapsed_ms"]:
                        self.gaps.add("clock_regression")
                    self.state["elapsed_ms"] = max(clock, self.state["elapsed_ms"])
                    self.state["sequence"] += 1
                    if self.state["sequence"] > MAX_COUNT:
                        self.gaps.add("counter_overflow")
                        break
                    if kind in {"controller_collection", "controller_departure"}:
                        if self.role != "controller" or node not in {"gw0", "gw1", "gw2"}:
                            self.gaps.add("observer_failure")
                            continue
                        field = "collections" if kind == "controller_collection" else "departures"
                        events = self.state["controller_events"][field]
                        if field == "collections":
                            keys = phase
                            if Counter(keys) != Counter(self.bound["expected"].keys()):
                                self.gaps.add("collection_mismatch")
                            entry = events.setdefault(node, {"count": 0, "nodes": sorted(keys)})
                            entry["count"] += 1
                            if entry["count"] > 1:
                                self.gaps.add("duplicate_boundary")
                        else:
                            events[node] = events.get(node, 0) + 1
                            if events[node] > 1:
                                self.gaps.add("duplicate_boundary")
                        continue
                    if node is not None:
                        if node not in self.bound["expected"]:
                            self.gaps.add("collection_mismatch")
                            continue
                        state = self.state["nodes"].setdefault(
                            node, {"starts": 0, "departures": 0, "phases": {}}
                        )
                        if kind in {"start", "depart"}:
                            field = "starts" if kind == "start" else "departures"
                            state[field] += 1
                            if state[field] > 1:
                                self.gaps.add("duplicate_boundary")
                        elif phase in PHASES:
                            counters = state["phases"].setdefault(
                                phase, {"entries": 0, "passed": 0, "failed": 0, "skipped": 0}
                            )
                            counters["entries" if kind == "phase" else outcome] += 1
                            if phase == "call" and not self.state["frames"]:
                                self.state["frames"] = self.frames()
                                self.state["stack_elapsed_ms"] = clock
                                last_stack = time.monotonic()
                            if (
                                counters["entries"] > 1
                                or sum(counters[p] for p in ("passed", "failed", "skipped")) > 1
                            ):
                                self.gaps.add("duplicate_boundary")
                except queue.Empty:
                    pass
                now = time.monotonic()
                if now - last_write >= 5 or self.stop.is_set():
                    self.state["process"] = proc_sample(self.pid) or self.sample
                    request = last_request
                    try:
                        payload = read_json(self.directory / "stack-request.json")
                        fields(payload, {"sequence"})
                        number(payload["sequence"])
                        if payload["sequence"] in range(8):
                            request = payload["sequence"]
                    except (OSError, ValueError):
                        pass
                    if now - last_stack >= 60 or request > last_request:
                        frames = self.frames()
                        if frames:
                            self.state["frames"] = frames
                        else:
                            self.gaps.add("stack_unavailable")
                        self.state["stack_elapsed_ms"] = max(
                            0, (time.monotonic_ns() - self.started) // 1000000
                        )
                        last_stack = now
                        last_request = request
                    self.state["gaps"] = sorted(self.gaps)
                    atomic(self.path, self.state, limit=4 * 1024 * 1024)
                    last_write = now
            self.state["finished"] = True
            self.state["gaps"] = sorted(self.gaps)
            atomic(self.path, self.state, limit=4 * 1024 * 1024)
        except Exception:
            # Never relay exception text or hide the missing process state.
            self.gaps.add("observer_failure")

    @pytest.hookimpl(optionalhook=True)
    def pytest_configure_node(self, node):
        node.workerinput["watchdog_lease"] = {
            "token": self.private["token"],
            "controller": self.pid,
            "start_ticks": self.sample["start_ticks"],
            "worker": node.gateway.id,
            "pid": node.gateway._io.popen.pid,
            "pid_start": proc_sample(node.gateway._io.popen.pid)["start_ticks"],
        }

    @pytest.hookimpl(optionalhook=True)
    def pytest_xdist_node_collection_finished(self, node, ids):
        keys = [node_digest(value, self.bound["nonce"]) for value in ids]
        self.emit("controller_collection", node.gateway.id, keys)

    @pytest.hookimpl(optionalhook=True)
    def pytest_testnodedown(self, node, error):
        self.emit("controller_departure", node.gateway.id)

    def pytest_collection_finish(self, session):
        if self.role == "worker":
            keys = [node_digest(item.nodeid, self.bound["nonce"]) for item in session.items]
            if Counter(keys) != Counter(self.bound["expected"].keys()):
                self.gaps.add("collection_mismatch")
            self.state["collections"] = sorted(keys)

    def pytest_runtest_logstart(self, nodeid, location):
        if self.role == "worker":
            self.emit("start", node_digest(nodeid, self.bound["nonce"]))

    def pytest_runtest_logfinish(self, nodeid, location):
        if self.role == "worker":
            self.emit("depart", node_digest(nodeid, self.bound["nonce"]))

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_setup(self, item):
        self.emit("phase", node_digest(item.nodeid, self.bound["nonce"]), "setup")
        return (yield)

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_call(self, item):
        self.emit("phase", node_digest(item.nodeid, self.bound["nonce"]), "call")
        return (yield)

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_teardown(self, item):
        self.emit("phase", node_digest(item.nodeid, self.bound["nonce"]), "teardown")
        return (yield)

    def pytest_runtest_logreport(self, report):
        if (
            self.role == "worker"
            and report.when in PHASES
            and report.outcome in {"passed", "failed", "skipped"}
        ):
            self.emit(
                "report",
                node_digest(report.nodeid, self.bound["nonce"]),
                report.when,
                report.outcome,
            )

    def pytest_sessionfinish(self, session, exitstatus):
        self.emit("finish")
        self.stop.set()
        self.writer.join(timeout=1)


def pytest_configure(config):
    try:
        if config.option.collectonly:
            if os.environ.get("CI_WATCHDOG_COLLECT") and os.environ.get("CI_INVENTORY_NONCE"):
                observation = json.loads(os.environ["CI_WATCHDOG_COLLECT"])
                parent = proc_sample(os.getppid())
                if (
                    not parent
                    or os.getppid() != observation["parent"]
                    or parent["start_ticks"] != observation["parent_start"]
                ):
                    return
                fd = os.open(
                    observation["output"] + ".owner",
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                )
                os.close(fd)
                config.pluginmanager.register(
                    CollectionObservation(observation),
                    "watchdog-collection",
                )
            return
        name = os.environ.get("CI_WATCHDOG_CONFIG")
        if not name or not os.environ.get("CI_SHARD_CONTEXT"):
            return
        private = read_json(Path(name))
        validate_binding(private["binding"])
        context = json.loads(os.environ["CI_SHARD_CONTEXT"])
        if (
            context.get("nonce") != private["binding"]["nonce"]
            or Path.cwd().resolve() != Path(private["root"]).resolve()
        ):
            return
        lease = getattr(config, "workerinput", {}).get("watchdog_lease")
        marker = Path(name).parent / "controller.json"
        if lease:
            owner = read_json(marker)
            process = proc_sample(lease["controller"])
            if (
                not process
                or {k: lease[k] for k in ("token", "controller", "start_ticks")} != owner
                or lease.get("worker") != config.workerinput.get("workerid")
                or lease.get("worker") not in {"gw0", "gw1", "gw2"}
                or lease.get("pid") != os.getpid()
                or lease.get("pid_start") != proc_sample(os.getpid())["start_ticks"]
                or lease["token"] != private["token"]
                or process["start_ticks"] != lease["start_ticks"]
            ):
                return
            registration = Path(name).parent / f"registration-{os.getpid()}"
            fd = os.open(registration, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            os.close(fd)
            role = "worker"
        else:
            process = proc_sample(os.getpid())
            owner = {
                "token": private["token"],
                "controller": os.getpid(),
                "start_ticks": process["start_ticks"],
            }
            fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "w") as stream:
                json.dump(owner, stream)
            role = "controller"
        config.pluginmanager.register(Observer(config, private, role), "watchdog-observer")
    except Exception:
        # Observation failure never changes pytest status. Missing state is UNKNOWN.
        return


def validate_process(value, bound):
    fields(
        value,
        {
            "role",
            "worker",
            "process",
            "sequence",
            "elapsed_ms",
            "collections",
            "controller_events",
            "nodes",
            "frames",
            "stack_elapsed_ms",
            "gaps",
            "finished",
        },
    )
    if value["role"] not in {"controller", "worker"} or type(value["finished"]) is not bool:
        raise ValueError("diagnostic registration malformed")
    if (value["role"] == "worker" and value["worker"] not in {"gw0", "gw1", "gw2"}) or (
        value["role"] == "controller" and value["worker"] is not None
    ):
        raise ValueError("diagnostic worker identity malformed")
    number(value["sequence"])
    number(value["elapsed_ms"])
    number(value["stack_elapsed_ms"])
    fields(
        value["process"],
        {"pid", "ppid", "start_ticks", "state", "threads", "cpu_ticks", "rss_pages"},
    )
    for key, item in value["process"].items():
        if key != "state":
            number(item)
        elif item not in set("RSDZTWIPX?"):
            raise ValueError("diagnostic process state malformed")
    if not isinstance(value["collections"], list) or len(value["collections"]) > len(
        bound["expected"]
    ):
        raise ValueError("diagnostic collection malformed")
    if any(node not in bound["expected"] for node in value["collections"]) or len(
        value["collections"]
    ) != len(set(value["collections"])):
        raise ValueError("diagnostic collection identity malformed")
    fields(value["controller_events"], {"collections", "departures"})
    for field, events in value["controller_events"].items():
        if (
            not isinstance(events, dict)
            or set(events) - {"gw0", "gw1", "gw2"}
            or (value["role"] == "worker" and events)
        ):
            raise ValueError("diagnostic controller boundary malformed")
        for entry in events.values():
            if field == "departures":
                number(entry)
            else:
                fields(entry, {"count", "nodes"})
                number(entry["count"])
                if (
                    not isinstance(entry["nodes"], list)
                    or len(entry["nodes"]) > len(bound["expected"])
                    or any(n not in bound["expected"] for n in entry["nodes"])
                ):
                    raise ValueError("diagnostic controller collection malformed")
    if not isinstance(value["nodes"], dict) or set(value["nodes"]) - set(bound["expected"]):
        raise ValueError("diagnostic nodes outside expected")
    total = 0
    for counters in value["nodes"].values():
        fields(counters, {"starts", "departures", "phases"})
        number(counters["starts"])
        number(counters["departures"])
        if (
            counters["starts"] > 1 or counters["departures"] > 1
        ) and "duplicate_boundary" not in value["gaps"]:
            raise ValueError("diagnostic duplicate item boundary")
        if not isinstance(counters["phases"], dict) or set(counters["phases"]) - set(PHASES):
            raise ValueError("diagnostic phase malformed")
        for counts in counters["phases"].values():
            fields(counts, {"entries", "passed", "failed", "skipped"})
            for count in counts.values():
                number(count)
            total += sum(counts.values())
            if (
                counts["entries"] > 1 or sum(counts[p] for p in ("passed", "failed", "skipped")) > 1
            ) and "duplicate_boundary" not in value["gaps"]:
                raise ValueError("diagnostic duplicate phase boundary")
    if total > value["sequence"]:
        raise ValueError("diagnostic event range malformed")
    if not isinstance(value["frames"], list) or len(value["frames"]) > 256 * 64:
        raise ValueError("diagnostic frame population malformed")
    for frame in value["frames"]:
        fields(frame, {"thread", "depth", "file", "blob", "line"})
        for key in ("thread", "depth", "line"):
            number(frame[key])
        hexvalue(frame["blob"], 40)
        if (
            frame["thread"] >= 256
            or frame["depth"] >= 64
            or not isinstance(frame["file"], str)
            or ".." in frame["file"]
            or not re.fullmatch(r"[A-Za-z0-9_./-]+\.py", frame["file"])
        ):
            raise ValueError("diagnostic static frame malformed")
        approved = bound["source_refs"].get(frame["file"])
        if (
            not approved
            or frame["blob"] != approved["blob"]
            or not 0 < frame["line"] <= approved["lines"]
        ):
            raise ValueError("diagnostic unapproved static frame")
    if (
        not isinstance(value["gaps"], list)
        or set(value["gaps"]) - GAPS
        or len(value["gaps"]) != len(set(value["gaps"]))
    ):
        raise ValueError("diagnostic gap malformed")


def validate_snapshot(value, bound):
    fields(
        value,
        {
            "schema",
            "complete",
            "binding",
            "sequence",
            "elapsed_ms",
            "processes",
            "slots",
            "gaps",
            "numeric_samples",
        },
    )
    validate_binding(bound)
    validate_binding(value["binding"])
    if (
        value["schema"] != SCHEMA
        or value["complete"] is not False
        or json.dumps(value["binding"], sort_keys=True) != json.dumps(bound, sort_keys=True)
    ):
        raise ValueError("diagnostic source or incomplete schema mismatch")
    number(value["sequence"])
    number(value["elapsed_ms"])
    if value["sequence"] not in range(8):
        raise ValueError("diagnostic slot outside schedule")
    if not isinstance(value["processes"], list) or len(value["processes"]) > 4:
        raise ValueError("diagnostic process population overflow")
    fields(value["numeric_samples"], {"samples", "truncated"})
    numeric = value["numeric_samples"]
    if (
        type(numeric["truncated"]) is not bool
        or not isinstance(numeric["samples"], list)
        or len(numeric["samples"]) > 256
    ):
        raise ValueError("diagnostic numeric samples overflow")
    samples_seen = set()
    for sample in numeric["samples"]:
        fields(sample, {"pid", "ppid", "start_ticks", "state", "threads", "cpu_ticks", "rss_pages"})
        for field, item in sample.items():
            if field != "state":
                number(item)
            elif item not in set("RSDZTWIPX?"):
                raise ValueError("diagnostic numeric state malformed")
        identity = sample["pid"], sample["start_ticks"]
        if identity in samples_seen:
            raise ValueError("diagnostic numeric sample duplicated")
        samples_seen.add(identity)
    identities = set()
    worker_ids = set()
    starts = Counter()
    for process in value["processes"]:
        validate_process(process, bound)
        key = process["process"]["pid"], process["process"]["start_ticks"]
        if key in identities:
            raise ValueError("diagnostic duplicate process")
        identities.add(key)
        if process["role"] == "worker":
            if process["worker"] in worker_ids:
                raise ValueError("diagnostic duplicate worker lease")
            worker_ids.add(process["worker"])
            starts.update({node: counters["starts"] for node, counters in process["nodes"].items()})
    if sum(p["role"] == "controller" for p in value["processes"]) > 1 or any(
        c > 1 for c in starts.values()
    ):
        raise ValueError("diagnostic duplicate logical registration")
    if not isinstance(value["slots"], list) or len(value["slots"]) != 8:
        raise ValueError("diagnostic slot outcomes missing")
    for index, slot in enumerate(value["slots"]):
        fields(slot, {"sequence", "scheduled_s", "transport", "artifact_id", "digest", "size"})
        if (
            type(slot["sequence"]) is not int
            or slot["sequence"] != index
            or type(slot["scheduled_s"]) is not int
            or slot["scheduled_s"] != SCHEDULE[index]
            or slot["transport"] not in TRANSPORT
        ):
            raise ValueError("diagnostic slot malformed")
        if slot["transport"] == "finalized":
            if type(slot["artifact_id"]) is not int or not 0 < slot["artifact_id"] <= 2**53 - 1:
                raise ValueError("diagnostic artifact id malformed")
            number(slot["size"])
            hexvalue(slot["digest"])
            if slot["artifact_id"] == 0:
                raise ValueError("diagnostic artifact not finalized")
        elif any(slot[k] is not None for k in ("artifact_id", "digest", "size")):
            raise ValueError("diagnostic persistence without finalization")
    if not isinstance(value["gaps"], list) or set(value["gaps"]) - GAPS:
        raise ValueError("diagnostic gap malformed")


def slots():
    return [
        {
            "sequence": index,
            "scheduled_s": when,
            "transport": "pending",
            "artifact_id": None,
            "digest": None,
            "size": None,
        }
        for index, when in enumerate(SCHEDULE)
    ]


def checkpoint(directory, sequence, *, elapsed_ms, transport=None):
    private = read_json(directory / "config.json")
    bound = private["binding"]
    processes = []
    gaps = {"transport_unknown"}
    try:
        paths = sorted((directory / "states").iterdir())
    except OSError:
        paths = []
        gaps.add("observer_failure")
    for path in paths:
        if not re.fullmatch(r"[0-9]+\.json", path.name):
            continue
        if len(processes) >= 4:
            gaps.add("process_unavailable")
            break
        try:
            process = read_json(path)
            validate_process(process, bound)
            processes.append(process)
        except (OSError, ValueError):
            gaps.add("observer_failure")
    if not processes:
        gaps.add("missing_process")
    multiplicities = Counter()
    for process in processes:
        if process["role"] == "worker":
            multiplicities.update(
                {node: state["starts"] for node, state in process["nodes"].items()}
            )
    if any(count > 1 for count in multiplicities.values()):
        gaps.add("duplicate_boundary")
    numeric = {"samples": [], "truncated": False}
    try:
        numeric = read_json(directory / "samples.json")
    except (OSError, ValueError):
        gaps.add("process_unavailable")
    value = {
        "numeric_samples": numeric,
        "schema": SCHEMA,
        "complete": False,
        "binding": bound,
        "sequence": sequence,
        "elapsed_ms": elapsed_ms,
        "processes": processes,
        "slots": transport or slots(),
        "gaps": sorted(gaps),
    }
    validate_snapshot(value, bound)
    target = directory / "checkpoints" / f"slot-{sequence}"
    target.mkdir(exist_ok=False)
    atomic(target / "snapshot.json", value)
    raw = (target / "snapshot.json").read_bytes()
    atomic(
        target / "manifest.json",
        {
            "schema": "ci-watchdog-manifest.v1",
            "complete": False,
            "binding_digest": digest(bound),
            "sequence": sequence,
            "files": {
                "snapshot.json": {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
            },
        },
    )
    return target


def read_checkpoint(directory, bound):
    if directory.is_symlink() or set(p.name for p in directory.iterdir()) != {
        "snapshot.json",
        "manifest.json",
    }:
        raise ValueError("diagnostic sealed members mismatch")
    manifest = read_json(directory / "manifest.json")
    fields(manifest, {"schema", "complete", "binding_digest", "sequence", "files"})
    if (
        manifest["schema"] != "ci-watchdog-manifest.v1"
        or manifest["complete"] is not False
        or manifest["binding_digest"] != digest(bound)
    ):
        raise ValueError("diagnostic seal binding mismatch")
    fields(manifest["files"], {"snapshot.json"})
    fields(manifest["files"]["snapshot.json"], {"sha256", "bytes"})
    raw = read_bytes(directory / "snapshot.json")
    body = parse_bytes(raw)
    if (
        manifest["files"]["snapshot.json"]
        != {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
        or type(manifest["files"]["snapshot.json"]["bytes"]) is not int
        or type(manifest["sequence"]) is not int
        or manifest["sequence"] != body["sequence"]
    ):
        raise ValueError("diagnostic seal corrupted")
    validate_snapshot(body, bound)
    observed = set()
    multiplicities = Counter()
    for process in body["processes"]:
        if process["role"] == "worker":
            multiplicities.update(
                {node: state["starts"] for node, state in process["nodes"].items()}
            )
    gaps = set(body["gaps"])
    for process in body["processes"]:
        gaps.update(process["gaps"])
        if process["role"] != "worker" or "duplicate_boundary" in process["gaps"]:
            continue
        for node, counters in process["nodes"].items():
            if multiplicities[node] != 1:
                continue
            for phase, counts in counters["phases"].items():
                if (
                    counts["entries"] == 1
                    and sum(counts[p] for p in ("passed", "failed", "skipped")) == 1
                ):
                    observed.add(f"{node}:{phase}")
    unknown = sorted(
        f"{node}:{phase}"
        for node in bound["expected"]
        for phase in PHASES
        if f"{node}:{phase}" not in observed
    )
    return {
        "schema": "ci-watchdog-scope.v1",
        "complete": False,
        "diagnostic_only": True,
        "whole_execution": "UNKNOWN",
        "finalizers": "UNKNOWN",
        "required_gates": "UNKNOWN",
        "observed": sorted(observed),
        "observed_phase_count": len(observed),
        "start_multiplicity": {node: multiplicities[node] for node in bound["expected"]},
        "unknown": unknown,
        "gaps": sorted(gaps),
        "sequence": body["sequence"],
    }


def prepare_profile(subject, capture, output, original):
    if git(capture, "status", "--porcelain", "--untracked-files=no"):
        raise ValueError("capture source dirty")
    sys.path.insert(0, str(subject / "scripts"))
    if (
        git(subject, "rev-parse", "HEAD") != SUBJECT
        or git(subject, "rev-parse", "HEAD^{tree}") != TREE
        or git(subject, "show", "-s", "--format=%P").split() != PARENTS
    ):
        raise ValueError("frozen diagnostic subject mismatch")
    if git(subject, "status", "--porcelain", "--untracked-files=no"):
        raise ValueError("frozen diagnostic subject dirty")
    if sys.version_info[:3] != (3, 12, 15) or (subject / ".venv").is_symlink():
        raise ValueError("diagnostic interpreter mismatch")
    import butlers

    if not Path(butlers.__file__).resolve().is_relative_to(subject / "src"):
        raise ValueError("diagnostic editable source mismatch")
    os.environ["GITHUB_SHA"] = SUBJECT
    data, plan, mapping = collect_correspondence(subject, original)
    from ci_partition import check_budgets

    check_budgets(data, root=subject)
    bound = binding(data, plan, capture_root=capture)
    bound["subject_parents"] = PARENTS
    configure(output, bound, root=subject)
    write_inventory(output / "inventory", data, plan)
    atomic(output / "inventory/correspondence.json", mapping)
    atomic(output / "inventory/binding.json", bound)


def initialize(subject, capture, inventory, output):
    if git(capture, "status", "--porcelain", "--untracked-files=no") or git(
        subject, "status", "--porcelain", "--untracked-files=no"
    ):
        raise ValueError("capture or subject source dirty")
    sys.path.insert(0, str(subject / "scripts"))
    from ci_partition import validate_assignment

    if (
        git(subject, "rev-parse", "HEAD") != SUBJECT
        or git(subject, "rev-parse", "HEAD^{tree}") != TREE
        or git(subject, "show", "-s", "--format=%P").split() != PARENTS
    ):
        raise ValueError("frozen subject mismatch")
    bound = read_json(inventory / "binding.json")
    data = read_json(inventory / "inventory.json")
    plan = read_json(inventory / "assignment.json")
    validate_assignment(data, plan, root=subject)
    expected_binding = binding(data, plan, capture_root=capture)
    expected_binding["subject_parents"] = PARENTS
    expected_binding["source_refs"] = {
        v["file"]: {"blob": v["blob"], "lines": v["lines"]} for v in safe_sources(subject).values()
    }
    if bound != expected_binding or sys.version_info[:3] != (3, 12, 15):
        raise ValueError("capture inventory domain mismatch")
    import butlers

    if (subject / ".venv").is_symlink() or not Path(butlers.__file__).resolve().is_relative_to(
        subject / "src"
    ):
        raise ValueError("subject source import mismatch")
    configure(output, bound, root=subject)


def main():
    parser = argparse.ArgumentParser(description="Diagnostic-only masked CI observations")
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    for name in ("subject", "capture", "output", "original"):
        prepare.add_argument("--" + name, type=Path, required=True)
    init = commands.add_parser("initialize")
    for name in ("subject", "capture", "inventory", "output"):
        init.add_argument("--" + name, type=Path, required=True)
    seal = commands.add_parser("checkpoint")
    seal.add_argument("--directory", type=Path, required=True)
    seal.add_argument("--sequence", type=int, required=True)
    seal.add_argument("--elapsed-ms", type=int, required=True)
    seal.add_argument("--transport", type=Path)
    verify = commands.add_parser("check-seal")
    verify.add_argument("--directory", type=Path, required=True)
    verify.add_argument("--binding", type=Path, required=True)
    validate = commands.add_parser("validate")
    for name in ("snapshot-dir", "inventory-dir", "output"):
        validate.add_argument("--" + name, type=Path, required=True)
    for name in (
        "expected-profile",
        "capture-head",
        "subject-head",
        "subject-tree",
        "repository",
        "workflow",
        "run",
        "attempt",
    ):
        validate.add_argument("--" + name, required=True)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            prepare_profile(
                args.subject.resolve(),
                args.capture.resolve(),
                args.output.resolve(),
                read_json(args.original),
            )
        elif args.command == "initialize":
            initialize(
                args.subject.resolve(),
                args.capture.resolve(),
                args.inventory.resolve(),
                args.output.resolve(),
            )
        elif args.command == "checkpoint":
            checkpoint(
                args.directory,
                args.sequence,
                elapsed_ms=args.elapsed_ms,
                transport=read_json(args.transport) if args.transport else None,
            )
        elif args.command == "check-seal":
            read_checkpoint(args.directory, read_json(args.binding))
        else:
            bound = read_json(args.inventory_dir / "binding.json")
            reviewed = reviewed_source_reference(
                Path(__file__).resolve().parents[1], args.capture_head
            )
            if any(bound[field] != value for field, value in reviewed.items()):
                raise ValueError("downloaded capture source mismatch")
            for field, value in (
                ("profile", args.expected_profile),
                ("capture_head", args.capture_head),
                ("subject_head", args.subject_head),
                ("subject_tree", args.subject_tree),
                ("repository", args.repository),
                ("workflow", args.workflow),
                ("run", args.run),
                ("attempt", args.attempt),
            ):
                if bound[field] != value:
                    raise ValueError("download identity mismatch")
            data = read_json(args.inventory_dir / "inventory.json")
            plan = read_json(args.inventory_dir / "assignment.json")
            from ci_partition import body_digest

            if (
                data["digest"] != body_digest(data)
                or plan["digest"] != body_digest(plan)
                or data["digest"] != bound["inventory_digest"]
                or plan["digest"] != bound["assignment_digest"]
                or data["nonce"] != bound["nonce"]
                or plan["inventory_digest"] != data["digest"]
            ):
                raise ValueError("downloaded inventory seal mismatch")
            files = plan["shards"]["unit"][0]["files"]
            expected = {node: file for file in files for node in data["lanes"]["unit"][file]}
            if (
                expected != bound["expected"]
                or bound["subject_head"] != SUBJECT
                or bound["subject_tree"] != TREE
                or bound["subject_parents"] != PARENTS
            ):
                raise ValueError("downloaded frozen population mismatch")
            profile = read_json(
                Path(__file__).resolve().parents[1]
                / ".github/actions/ci-shard-watchdog-capture/profile.json"
            )
            mapping = read_json(args.inventory_dir / "correspondence.json")
            if (
                sorted(files) != sorted(profile["complete_expected_files"])
                or Counter(p["old"] for p in mapping) != Counter(profile["complete_expected_nodes"])
                or {p["new"]: p["file"] for p in mapping} != expected
                or len(mapping) != len(expected)
            ):
                raise ValueError("downloaded correspondence mismatch")
            authentication = read_json(args.inventory_dir / "provider-authenticated.json")
            reports = []
            prior = -1
            prior_body = None
            for entry in authentication:
                fields(
                    entry,
                    {
                        "artifact_id",
                        "name",
                        "repository",
                        "workflow",
                        "run",
                        "attempt",
                        "sequence",
                        "snapshot_sha256",
                    },
                )
                for field in ("repository", "workflow", "run", "attempt"):
                    if entry[field] != bound[field]:
                        raise ValueError("provider identity mismatch")
                if (
                    type(entry["artifact_id"]) is not int
                    or not 0 < entry["artifact_id"] <= 2**53 - 1
                ):
                    raise ValueError("authenticated artifact id malformed")
                number(entry["sequence"])
                if entry["sequence"] >= len(SCHEDULE):
                    raise ValueError("authenticated checkpoint slot malformed")
                name = (
                    f"ci-watchdog-unit-1-{bound['run']}-{bound['attempt']}-"
                    f"{bound['nonce'][:12]}-{entry['sequence']}"
                )
                if entry["artifact_id"] <= 0 or entry["name"] != name or entry["sequence"] <= prior:
                    raise ValueError("provider artifact ambiguity")
                directory = args.snapshot_dir / f"slot-{entry['sequence']}"
                report = read_checkpoint(directory, bound)
                if report["sequence"] != entry["sequence"]:
                    raise ValueError("authenticated checkpoint sequence mismatch")
                if (
                    hashlib.sha256(read_bytes(directory / "snapshot.json")).hexdigest()
                    != entry["snapshot_sha256"]
                ):
                    raise ValueError("authenticated content mismatch")
                body = read_json(directory / "snapshot.json")
                if prior_body:
                    if body["elapsed_ms"] < prior_body["elapsed_ms"]:
                        raise ValueError("diagnostic checkpoint clock regression")
                    before = {
                        (p["process"]["pid"], p["process"]["start_ticks"]): p
                        for p in prior_body["processes"]
                    }
                    for process in body["processes"]:
                        previous = before.get(
                            (process["process"]["pid"], process["process"]["start_ticks"])
                        )
                        if previous and (
                            process["sequence"] < previous["sequence"]
                            or process["elapsed_ms"] < previous["elapsed_ms"]
                        ):
                            raise ValueError("diagnostic process clock regression")
                        if previous:
                            for node, counts in previous["nodes"].items():
                                current = process["nodes"].get(node)
                                if not current or any(
                                    current[key] < counts[key] for key in ("starts", "departures")
                                ):
                                    raise ValueError("diagnostic counter regression")
                                for phase, values in counts["phases"].items():
                                    if phase not in current["phases"] or any(
                                        current["phases"][phase][key] < value
                                        for key, value in values.items()
                                    ):
                                        raise ValueError("diagnostic phase regression")
                reports.append(report)
                prior_body = body
                prior = entry["sequence"]
            if not reports:
                raise ValueError("no authenticated finalized checkpoint")
            atomic(
                args.output,
                {
                    "schema": "ci-watchdog-download-scope.v1",
                    "complete": False,
                    "diagnostic_only": True,
                    "reports": reports,
                    "old_unit1_checkout": "UNKNOWN",
                    "old_unit1_cause": "UNKNOWN",
                },
            )
        return 0
    except Exception:
        print("watchdog diagnostic refused: UNKNOWN", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
