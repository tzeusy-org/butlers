"""Opt-in closed storage measurements for the existing hosted integration lane.

No cleanup, resource limit, test selection or pytest verdict is changed here.
An unavailable measurement is UNKNOWN, never zero or evidence of free capacity.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import os
import re
import selectors
import signal
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

MAX_OUTPUT = 8192
MAX_REPORT = 131072
MAX_SAMPLES = 73
INTERVAL = 30
_ACTIVE: StorageObserver | None = None


class Unknown(ValueError):
    """Only fixed reason codes leave this module."""


def _unknown(reason: str) -> dict:
    return {"status": "unknown", "reason": reason}


def _capture(argv: list[str], seconds: float = 3) -> bytes:
    """Bound actual child output while it runs; kill/reap only our own group."""
    with subprocess.Popen(
        argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True
    ) as child:
        assert child.stdout is not None
        output = bytearray()
        deadline = time.monotonic() + seconds
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(child.stdout, selectors.EVENT_READ)
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise Unknown("command_timeout")
                    for key, _ in selector.select(min(remaining, 0.1)):
                        chunk = os.read(key.fd, 4096)
                        if not chunk:
                            selector.unregister(key.fileobj)
                        else:
                            output.extend(chunk)
                            if len(output) > MAX_OUTPUT:
                                raise Unknown("command_output_limit")
            # Keep the child unreaped until group cleanup, so its PID cannot be
            # recycled into an unrelated group between exit and our finally.
            while True:
                state = os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
                if state is not None:
                    break
                if time.monotonic() >= deadline:
                    raise Unknown("command_timeout")
                time.sleep(0.01)
            if state.si_code != os.CLD_EXITED or state.si_status != 0:
                raise Unknown("command_failed")
            return bytes(output)
        finally:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=2)


def filesystem(path: str) -> dict:
    info = os.statvfs(path)
    return {
        "status": "ok",
        "total_bytes": info.f_blocks * info.f_frsize,
        "free_bytes": info.f_bavail * info.f_frsize,
        "total_inodes": info.f_files,
        "free_inodes": info.f_favail,
    }


def _size(value: str) -> int:
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)(B|kB|MB|GB|TB)", value)
    if not match:
        raise Unknown("docker_size_shape")
    return int(float(match[1]) * 1000 ** ("B", "kB", "MB", "GB", "TB").index(match[2]))


DOCKER_STAGES = ("system_df", "root_info")
PG_STAGES = ("url", "connect", "readonly", "database_sizes", "wal", "temp", "dispose")


def _elapsed_s(started: float) -> float | None:
    elapsed = time.monotonic() - started
    return round(elapsed, 3) if math.isfinite(elapsed) and 0 <= elapsed <= 7200 else None


def _probe_outcome(error: Exception, stage: str) -> str:
    if isinstance(error, Unknown):
        return {
            "command_timeout": "command_timeout",
            "command_failed": "command_failed",
            "command_output_limit": "command_output_limit",
            "database_not_readonly": "not_readonly",
        }.get(str(error), "other")
    if stage in PG_STAGES:
        original = getattr(error, "orig", error)
        code = getattr(original, "pgcode", None) or getattr(original, "sqlstate", None)
        if isinstance(code, str):
            if code.startswith("08") and re.fullmatch(r"[0-9A-Z]{5}", code):
                return "connection"
            return {
                "57014": "query_canceled",
                "53100": "disk_full",
                "42501": "permission",
            }.get(code, "other")
    return "other"


def _probe_step[T](steps: list[dict] | None, stage: str, call: Callable[[], T]) -> T:
    started = time.monotonic()
    outcome = "ok"
    try:
        result = call()
        if isinstance(result, dict) and result.get("status", "ok") != "ok":
            outcome = "unavailable"
        return result
    except Exception as error:
        outcome = _probe_outcome(error, stage)
        raise
    finally:
        if steps is not None:
            elapsed = _elapsed_s(started)
            steps.append(
                {
                    "stage": stage,
                    "outcome": outcome,
                    "elapsed_ms": None if elapsed is None else round(elapsed * 1000, 3),
                }
            )


def docker_totals(*, steps: list[dict] | None = None) -> dict:
    def system_df():
        raw = _capture(["docker", "system", "df", "--format", "{{json .}}"])
        records = [json.loads(line) for line in raw.splitlines()]
        categories = {
            "Images": "images",
            "Containers": "containers",
            "Local Volumes": "volumes",
            "Build Cache": "build_cache",
        }
        if len(records) != 4 or {r.get("Type") for r in records} != set(categories):
            raise Unknown("docker_totals_shape")
        result: dict[str, Any] = {"status": "ok"}
        for row in records:
            count, active = str(row["TotalCount"]), str(row["Active"])
            if not count.isdecimal() or not active.isdecimal():
                raise Unknown("docker_totals_shape")
            result[categories[row["Type"]]] = {
                "count": int(count),
                "active": int(active),
                "bytes": _size(row["Size"]),
            }
        return result

    def root_info():
        root = _capture(["docker", "info", "--format", "{{.DockerRootDir}}"]).decode().strip()
        if not root.startswith("/") or "\n" in root:
            raise Unknown("docker_root_shape")
        return safe(lambda: filesystem(root))

    result = _probe_step(steps, "system_df", system_df)
    result["filesystem"] = _probe_step(steps, "root_info", root_info)
    if result["filesystem"]["status"] != "ok":
        result["status"] = "unknown_filesystem"
    return result


def safe(call) -> dict:
    try:
        return call()
    except Unknown as error:
        return _unknown(str(error))
    except Exception:
        return _unknown("measurement_unavailable")


def owned_pgdata(container_id: str) -> dict:
    if re.fullmatch(r"[0-9a-f]{64}", container_id) is None:
        raise Unknown("server_identity")
    state = (
        _capture(["docker", "inspect", "--format", "{{.State.Running}} {{.Image}}", container_id])
        .decode()
        .strip()
    )
    match = re.fullmatch(r"(true|false) sha256:([0-9a-f]{64})", state)
    if match is None:
        raise Unknown("server_state_shape")
    mounts = (
        _capture(
            [
                "docker",
                "inspect",
                "--format",
                "{{range .Mounts}}{{.Type}}{{println}}{{end}}",
                container_id,
            ]
        )
        .decode("ascii")
        .strip()
        .splitlines()
    )
    if any(kind not in {"volume", "bind", "tmpfs"} for kind in mounts):
        raise Unknown("server_mount_shape")
    result = {
        "status": "ok",
        "owned_running_servers": int(match[1] == "true"),
        "owned_volumes": mounts.count("volume"),
        "image_sha256": match[2],
    }
    for flag, fields in (
        ("-Pk", ("total_bytes", "free_bytes")),
        ("-Pi", ("total_inodes", "free_inodes")),
    ):
        raw = _capture(["docker", "exec", container_id, "df", flag, "/var/lib/postgresql/data"])
        lines = raw.decode("ascii").splitlines()
        if len(lines) != 2:
            raise Unknown("pgdata_shape")
        row = lines[1].split()
        if len(row) != 6 or not all(row[n].isdecimal() for n in (1, 2, 3)):
            raise Unknown("pgdata_shape")
        multiplier = 1024 if flag == "-Pk" else 1
        result[fields[0]] = int(row[1]) * multiplier
        result[fields[1]] = int(row[3]) * multiplier
    return result


def database_totals(container: Any, *, steps: list[dict] | None = None) -> dict:
    # Credentials/results flow only to the driver. The optional trace contains
    # fixed stages, safe error categories and timing, never call arguments.
    from sqlalchemy import create_engine, text
    from sqlalchemy.pool import NullPool

    url = _probe_step(steps, "url", container.get_connection_url)
    engine = None

    def connect():
        nonlocal engine
        engine = create_engine(
            url,
            poolclass=NullPool,
            connect_args={
                "connect_timeout": 2,
                "options": (
                    "-c default_transaction_read_only=on "
                    "-c statement_timeout=2000 -c lock_timeout=1000"
                ),
            },
        )
        return engine.connect()

    try:
        with _probe_step(steps, "connect", connect) as connection:

            def readonly():
                if connection.execute(text("SHOW transaction_read_only")).scalar_one() != "on":
                    raise Unknown("database_not_readonly")

            _probe_step(steps, "readonly", readonly)
            row = _probe_step(
                steps,
                "database_sizes",
                lambda: (
                    connection.execute(
                        text("""
                WITH sizes AS MATERIALIZED (
                    SELECT datname, pg_database_size(oid) AS bytes FROM pg_database
                )
                SELECT count(*) AS databases,
                    count(*) FILTER (WHERE left(datname, 5) = 'tmpl_') AS template_named_databases,
                    count(*) FILTER (WHERE left(datname, 5) = 'test_') AS test_named_databases,
                    coalesce(sum(bytes), 0) AS database_bytes,
                    coalesce(max(bytes), 0) AS largest_database_bytes,
                    coalesce(sum(bytes) FILTER (WHERE left(datname, 5) = 'tmpl_'), 0)
                        AS template_named_database_bytes,
                    coalesce(sum(bytes) FILTER (WHERE left(datname, 5) = 'test_'), 0)
                        AS test_named_database_bytes
                FROM sizes
            """)
                    )
                    .mappings()
                    .one()
                ),
            )
            result = {key: int(value) for key, value in row.items()}
            result["wal_bytes"] = _probe_step(
                steps,
                "wal",
                lambda: int(
                    connection.execute(
                        text("SELECT coalesce(sum(size), 0) FROM pg_ls_waldir()")
                    ).scalar_one()
                ),
            )
            result["temp_bytes_cumulative"] = _probe_step(
                steps,
                "temp",
                lambda: int(
                    connection.execute(
                        text("SELECT coalesce(sum(temp_bytes), 0) FROM pg_stat_database")
                    ).scalar_one()
                ),
            )
            return {
                "status": "ok",
                "readonly": True,
                "attribution": "prefix_only",
                "clone_current_bytes_status": "unknown_untracked",
                **result,
            }
    finally:
        if engine is not None:
            _probe_step(steps, "dispose", engine.dispose)


def enabled() -> bool:
    return (
        os.environ.get("BUTLERS_TEST_STORAGE_EVIDENCE") == "1"
        and os.environ.get("GITHUB_ACTIONS") == "true"
        and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
        and os.environ.get("BUTLERS_STORAGE_GROUP") == "6"
    )


def identity() -> dict:
    fields = {
        key: os.environ.get(key, "")
        for key in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_SHA")
    }
    if (
        not fields["GITHUB_RUN_ID"].isdecimal()
        or not fields["GITHUB_RUN_ATTEMPT"].isdecimal()
        or re.fullmatch(r"[0-9a-f]{40}", fields["GITHUB_SHA"]) is None
    ):
        raise Unknown("run_identity")
    worker = os.environ.get("PYTEST_XDIST_WORKER", "controller")
    if re.fullmatch(r"(?:gw[0-9]{1,2}|controller)", worker) is None or (
        worker.startswith("gw") and int(worker[2:]) >= 32
    ):
        raise Unknown("worker_identity")
    checkout = _capture(["git", "rev-parse", "HEAD"]).decode().strip()
    tree = _capture(["git", "rev-parse", "HEAD^{tree}"]).decode().strip()
    if (
        checkout != fields["GITHUB_SHA"]
        or re.fullmatch(r"[0-9a-f]{40}", tree) is None
        or os.environ.get("GITHUB_JOB") != "check-integration"
    ):
        raise Unknown("checkout_identity")
    return {
        "tree": tree,
        "run": fields["GITHUB_RUN_ID"],
        "attempt": fields["GITHUB_RUN_ATTEMPT"],
        "source": fields["GITHUB_SHA"],
        "job": "check-integration",
        "group": 6,
        "worker": worker,
    }


def write_report(name: str, record: dict) -> None:
    output = Path(os.environ["TEST_EVIDENCE_DIR"])
    if output.is_symlink() or not output.is_dir():
        raise Unknown("output_directory")
    raw = (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if len(raw) > MAX_REPORT:
        raise Unknown("report_limit")
    fd, temporary = tempfile.mkstemp(prefix=".storage-", dir=output)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
        os.replace(temporary, output / name)
    finally:
        Path(temporary).unlink(missing_ok=True)


class StorageObserver:
    """One fixture-owned server and finite joined metadata thread, no cleanup."""

    def __init__(self, container: Any) -> None:
        self.container = container
        self.binding = identity()
        if self.binding["worker"] == "controller":
            raise Unknown("worker_identity")
        self.container_id = container.get_wrapped_container().id
        if re.fullmatch(r"[0-9a-f]{64}", self.container_id) is None:
            raise Unknown("server_identity")
        self.binding["server"] = hashlib.sha256(
            (json.dumps(self.binding, sort_keys=True) + self.container_id).encode()
        ).hexdigest()
        self.events = {
            "template_created": 0,
            "clone_created": 0,
            "create_failed": 0,
            "storage_error": 0,
        }
        self.rows: list[dict] = []
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.wake_event = threading.Event()
        self.started = time.monotonic()
        self.thread = threading.Thread(target=self._run, name="test-storage-metadata", daemon=True)

    def note(self, event: str, container: Any) -> None:
        if container is self.container and event in self.events:
            with self.lock:
                self.events[event] += 1
            if event in ("create_failed", "storage_error"):
                self.wake_event.set()

    def sample(self, phase: str) -> None:
        with self.lock:
            if len(self.rows) >= MAX_SAMPLES:
                return
            counters = dict(self.events)
        probes: dict[str, list[dict]] = {"docker": [], "postgres": []}
        row = {
            "phase": phase,
            "elapsed_s": _elapsed_s(self.started),
            "events": counters,
            "root": safe(lambda: filesystem("/")),
            "docker": safe(lambda: docker_totals(steps=probes["docker"])),
            "pgdata": safe(lambda: owned_pgdata(self.container_id)),
            "postgres": safe(lambda: database_totals(self.container, steps=probes["postgres"])),
            "probes": probes,
        }
        row["completed_elapsed_s"] = _elapsed_s(self.started)
        with self.lock:
            self.rows.append(row)
        self.flush("observing")

    def flush(self, status: str) -> None:
        with self.lock:
            record = {
                "version": 1,
                "identity": self.binding,
                "status": status,
                "sample_limit_reached": len(self.rows) >= MAX_SAMPLES,
                "samples": list(self.rows),
            }
        # Diagnostics must not change the test's outcome, including at shutdown.
        with contextlib.suppress(Exception):
            write_report("storage-" + self.binding["worker"] + ".json", record)

    def _run(self) -> None:
        self.sample("server_started")
        for _ in range(MAX_SAMPLES - 2):
            woken = self.wake_event.wait(INTERVAL)
            self.wake_event.clear()
            if self.stop_event.is_set():
                return
            self.sample("create_error" if woken else "periodic")

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.wake_event.set()
        self.thread.join(timeout=30)
        if self.thread.is_alive():
            self.flush("unknown_observer_unsettled")
            return
        self.sample("before_server_stop")
        self.flush("observer_joined")


def note(event: str, container: Any) -> None:
    if _ACTIVE is not None:
        with contextlib.suppress(Exception):
            _ACTIVE.note(event, container)


@contextlib.contextmanager
def observe(container: Any):
    global _ACTIVE
    observer = None
    if enabled():
        try:
            observer = StorageObserver(container)
            _ACTIVE = observer
            observer.start()
        except Exception:
            observer = None
    try:
        yield
    finally:
        _ACTIVE = None
        if observer is not None:
            with contextlib.suppress(Exception):
                observer.stop()


def host(phase: str) -> None:
    if enabled():
        steps: list[dict] = []
        record = {
            "version": 1,
            "identity": safe(identity),
            "phase": phase,
            "root": safe(lambda: filesystem("/")),
            "docker": safe(lambda: docker_totals(steps=steps)),
            "probes": {"docker": steps},
        }
        with contextlib.suppress(Exception):
            write_report("storage-" + phase + ".json", record)


def _numbers(record: dict, fields: set[str], **fixed) -> bool:
    return (
        isinstance(record, dict)
        and set(record) == fields | set(fixed)
        and all(record.get(key) == value for key, value in fixed.items())
        and all(type(record[key]) is int and record[key] >= 0 for key in fields)
    )


def _filesystem(record: dict) -> bool:
    return _numbers(
        record, {"total_bytes", "free_bytes", "total_inodes", "free_inodes"}, status="ok"
    )


def _docker(record: dict) -> bool:
    categories = {"images", "containers", "volumes", "build_cache"}
    return (
        isinstance(record, dict)
        and set(record) == categories | {"status", "filesystem"}
        and record["status"] == "ok"
        and _filesystem(record["filesystem"])
        and all(_numbers(record[key], {"count", "active", "bytes"}) for key in categories)
    )


def _bound(actual: dict, expected: dict, worker: str, *, server: bool = False) -> bool:
    keys = set(expected) | ({"server"} if server else set())
    return (
        isinstance(actual, dict)
        and set(actual) == keys
        and all(actual[key] == value for key, value in expected.items() if key != "worker")
        and actual.get("worker") == worker
        and (not server or re.fullmatch(r"[0-9a-f]{64}", actual["server"]) is not None)
    )


def _probe_steps(steps: Any, expected: tuple[str, ...]) -> bool:
    # Only complete, successful, finite evidence may become a sampled inventory.
    return (
        isinstance(steps, list)
        and len(steps) == len(expected)
        and all(
            isinstance(step, dict)
            and set(step) == {"stage", "outcome", "elapsed_ms"}
            and step["stage"] == stage
            and step["outcome"] == "ok"
            and type(step["elapsed_ms"]) in (int, float)
            and math.isfinite(step["elapsed_ms"])
            and 0 <= step["elapsed_ms"] <= 7_200_000
            for step, stage in zip(steps, expected, strict=True)
        )
    )


def _sample(row: dict) -> bool:
    if not isinstance(row, dict) or set(row) != {
        "phase",
        "elapsed_s",
        "completed_elapsed_s",
        "probes",
        "events",
        "root",
        "docker",
        "pgdata",
        "postgres",
    }:
        return False
    pgdata = row["pgdata"]
    return (
        row["phase"] in {"server_started", "periodic", "create_error", "before_server_stop"}
        and type(row["elapsed_s"]) in (int, float)
        and math.isfinite(row["elapsed_s"])
        and type(row["completed_elapsed_s"]) in (int, float)
        and math.isfinite(row["completed_elapsed_s"])
        and 0 <= row["elapsed_s"] <= row["completed_elapsed_s"] <= 7200
        and isinstance(row["probes"], dict)
        and set(row["probes"]) == {"docker", "postgres"}
        and _probe_steps(row["probes"]["docker"], DOCKER_STAGES)
        and _probe_steps(row["probes"]["postgres"], PG_STAGES)
        and _numbers(
            row["events"], {"template_created", "clone_created", "create_failed", "storage_error"}
        )
        and _filesystem(row["root"])
        and _docker(row["docker"])
        and isinstance(pgdata, dict)
        and re.fullmatch(r"[0-9a-f]{64}", pgdata.get("image_sha256", "")) is not None
        and _numbers(
            pgdata,
            {
                "owned_running_servers",
                "owned_volumes",
                "total_bytes",
                "free_bytes",
                "total_inodes",
                "free_inodes",
            },
            status="ok",
            image_sha256=pgdata["image_sha256"],
        )
        and pgdata["owned_running_servers"] == 1
        and _numbers(
            row["postgres"],
            {
                "databases",
                "template_named_databases",
                "test_named_databases",
                "database_bytes",
                "largest_database_bytes",
                "template_named_database_bytes",
                "test_named_database_bytes",
                "wal_bytes",
                "temp_bytes_cumulative",
            },
            status="ok",
            readonly=True,
            attribution="prefix_only",
            clone_current_bytes_status="unknown_untracked",
        )
    )


def _read_report(path: Path) -> dict:
    if path.is_symlink() or not 0 < path.stat().st_size <= MAX_REPORT:
        raise Unknown("report_shape")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise Unknown("report_duplicate_key")
            result[key] = value
        return result

    def nonfinite(_):
        raise Unknown("report_nonfinite")

    return json.loads(path.read_bytes(), object_pairs_hook=pairs, parse_constant=nonfinite)


def _worker_files(output: Path) -> set[int]:
    # Only the 32 admitted writer identities: no arbitrary directory traversal.
    return {
        index
        for index in range(32)
        if (output / f"storage-gw{index}.json").exists()
        or (output / f"storage-gw{index}.json").is_symlink()
    }


def inventory() -> None:
    if not enabled():
        return
    output = Path(os.environ["TEST_EVIDENCE_DIR"])
    expected_raw = os.environ.get("PYTEST_XDIST_AUTO_WORKERS", "3")
    expected = int(expected_raw) if expected_raw.isdecimal() and 0 < int(expected_raw) <= 32 else 0
    binding = safe(identity)
    unexpected = sorted(_worker_files(output) - set(range(expected)))
    hosts = []
    for phase in ("before_cleanup", "after_cleanup"):
        complete = False
        try:
            value = _read_report(output / ("storage-" + phase + ".json"))
            complete = (
                set(value) == {"version", "identity", "phase", "root", "docker", "probes"}
                and value["version"] == 1
                and value["phase"] == phase
                and _bound(value["identity"], binding, "controller")
                and _filesystem(value["root"])
                and _docker(value["docker"])
                and isinstance(value["probes"], dict)
                and set(value["probes"]) == {"docker"}
                and _probe_steps(value["probes"]["docker"], DOCKER_STAGES)
            )
        except Exception:
            pass
        hosts.append({"phase": phase, "status": "sampled" if complete else "unknown"})
    reports = []
    for index in range(expected):
        path = output / f"storage-gw{index}.json"
        try:
            value = _read_report(path)
            rows = value["samples"]
            if not (
                set(value) == {"version", "identity", "status", "sample_limit_reached", "samples"}
                and value["version"] == 1
                and _bound(value["identity"], binding, f"gw{index}", server=True)
                and value["status"] == "observer_joined"
                and value["sample_limit_reached"] is False
                and isinstance(rows, list)
                and 2 <= len(rows) < MAX_SAMPLES
                and all(_sample(row) for row in rows)
                and rows[0]["phase"] == "server_started"
                and rows[-1]["phase"] == "before_server_stop"
                and all(
                    a["elapsed_s"] <= b["elapsed_s"] for a, b in zip(rows, rows[1:], strict=False)
                )
            ):
                raise Unknown("worker_report_incomplete")
            reports.append({"worker": index, "status": "sampled", "bytes": path.stat().st_size})
        except Exception:
            reports.append({"worker": index, "status": "unknown_missing_or_invalid_observer"})
    record = {
        "version": 1,
        "identity": binding,
        "status": "sampled"
        if expected
        and not unexpected
        and all(row["status"] == "sampled" for row in reports + hosts)
        else "unknown",
        "expected_workers": expected,
        "workers": reports,
        "host_phases": hosts,
        "unexpected_workers": unexpected,
        "max_total_bytes": 35 * MAX_REPORT,
    }
    with contextlib.suppress(Exception):
        write_report("storage-inventory.json", record)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("before_cleanup", "after_cleanup", "inventory"))
    args = parser.parse_args()
    # No diagnostic failure may replace the existing pytest/cleanup verdict.
    with contextlib.suppress(Exception):
        inventory() if args.phase == "inventory" else host(args.phase)


if __name__ == "__main__":
    main()
