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
    if stage in PG_STAGES or stage in ALLOCATION_STAGES:
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


def _owned_server(container_id: str) -> dict:
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
    return result


def owned_pgdata(container_id: str) -> dict:
    result = _owned_server(container_id)
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


MAX_DATABASES = 4096
ALLOCATION_STAGES = ("metadata_before", "allocation", "metadata_after", "wal_temp")
_METADATA_SQL = (
    "SELECT oid::bigint, datname, dattablespace::bigint FROM pg_database ORDER BY oid LIMIT 4097"
)
_WAL_TEMP_SQL = """SELECT (SELECT coalesce(sum(size), 0)::bigint FROM pg_ls_waldir()) AS wal_bytes,
    (SELECT coalesce(sum(temp_bytes), 0)::bigint FROM pg_stat_database) AS temp_bytes_cumulative"""

# One fixed command; per-OID paths and names never reach the host output. No
# symlinks are followed, no tablespace outside this server's base is scanned.
_ALLOCATION_SCRIPT = r"""
set -eu
data=/var/lib/postgresql/data
mapping=$1
for tool in du awk df find; do command -v "$tool" >/dev/null || exit 2; done
[ -d "$data/base" ] && [ ! -L "$data" ] && [ ! -L "$data/base" ]
[ -d "$data/pg_tblspc" ] && [ ! -L "$data/pg_tblspc" ]
links=$(find "$data/base" -type l -print -quit) || exit 2
spaces=$(find "$data/pg_tblspc" -mindepth 1 -print -quit) || exit 2
[ -z "$links" ] && [ -z "$spaces" ] || exit 2
(
 for flag in -Pk -Pi; do
   df_rows=$(df "$flag" "$data") || exit 2
   if printf '%s\n' "$df_rows" | awk -v flag="$flag" '
     NR==2 && NF==6 && $2~/^[0-9]+$/ && $4~/^[0-9]+$/ {
       printf "__DF__ %s %s %s\n",flag,$2,$4; good=1
     }
     END {if(NR!=2 || !good)exit 2}'; then :; else exit 2; fi
 done
 if du -l -x -B1 -s -- "$data/base"/* 2>/dev/null; then
   printf '__DU_EXIT__ 0\n'
 else printf '__DU_EXIT__ 1\n'; fi
) | awk -v mapping="$mapping" '
BEGIN {
 n=split(mapping,pairs,",");
 if(mapping=="")n=0;
 for(i=1;i<=n;i++) {
  if(split(pairs[i],kv,":")!=2 || kv[1]!~/^[0-9]+$/ ||
     kv[2]!~/^[TCFP]$/ || kv[1] in cls){invalid=1;continue}
  cls[kv[1]]=kv[2];expected++;
 }
}
$1=="__DF__" {
 if(NF!=4 || ($2!="-Pk" && $2!="-Pi") || $3!~/^[0-9]+$/ ||
    $4!~/^[0-9]+$/ || $2 in dfseen){invalid=1;next}
 dfseen[$2]=1;total[$2]=$3;free[$2]=$4;next
}
$1=="__DU_EXIT__" {if(NF!=2 || $2!~/^[01]$/)invalid=1;footer++;command_exit=$2;next}
{
 if($1!~/^[0-9]+$/ || NF!=2){invalid=1;next}
 records++;if(records>4096){invalid=1;exit 2}
 oid=$2;sub(/^.*\//,"",oid);
 if(oid!~/^[0-9]+$/){other_bytes+=$1;other_count++;next}
 if(oid in seen){invalid=1;next}
 seen[oid]=1;
 if(oid in cls){count[cls[oid]]++;bytes[cls[oid]]+=$1;matched++}
 else{unmapped++;unmapped_bytes+=$1}
}
END {
 missing=0;for(oid in cls)if(!(oid in seen))missing++;
 ok=(!invalid && footer==1 && command_exit==0 && missing==0 && matched==expected &&
     dfseen["-Pk"] && dfseen["-Pi"]);
 printf "scan_complete=%d\nexpected=%d\nmatched=%d\nmissing=%d\n",ok,expected,matched,missing;
 printf "unmapped_oids=%d\nunmapped_bytes=%.0f\n",unmapped,unmapped_bytes;
 printf "other_paths=%d\nother_bytes=%.0f\n",other_count,other_bytes;
 printf "total_bytes=%.0f\nfree_bytes=%.0f\n",total["-Pk"]*1024,free["-Pk"]*1024;
 printf "total_inodes=%.0f\nfree_inodes=%.0f\n",total["-Pi"],free["-Pi"];
 split("T C F P",classes," ");
 for(i=1;i<=4;i++){
  c=classes[i];printf "%s_count=%d\n%s_allocated_namespace_bytes=%.0f\n",c,count[c],c,bytes[c]
 }
 if(!ok)exit 2;
}'
"""


def _metadata(rows) -> dict[str, int]:
    if len(rows) > MAX_DATABASES:
        raise Unknown("database_metadata_limit")
    result = {}
    oids = set()
    for oid, name, tablespace in rows:
        if (
            type(oid) is not int
            or not 0 < oid < 2**32
            or oid in oids
            or not isinstance(name, str)
            or not name
            or len(name.encode()) > 63
            or name in result
            or type(tablespace) is not int
            or tablespace != 1663
        ):
            raise Unknown("database_metadata_shape")
        result[name] = oid
        oids.add(oid)
    return result


def _allocation_filesystem(container_id: str, mapping: dict[int, str]) -> dict:
    if len(mapping) > MAX_DATABASES or any(
        type(oid) is not int or not 0 < oid < 2**32 or kind not in {"T", "C", "F", "P"}
        for oid, kind in mapping.items()
    ):
        raise Unknown("allocation_mapping")
    result = _owned_server(container_id)
    if result["owned_running_servers"] != 1:
        raise Unknown("server_not_running")
    encoded = ",".join(f"{oid}:{kind}" for oid, kind in sorted(mapping.items()))
    raw = _capture(
        ["docker", "exec", container_id, "sh", "-c", _ALLOCATION_SCRIPT, "storage", encoded]
    )
    keys = {
        "scan_complete",
        "expected",
        "matched",
        "missing",
        "unmapped_oids",
        "unmapped_bytes",
        "other_paths",
        "other_bytes",
        "total_bytes",
        "free_bytes",
        "total_inodes",
        "free_inodes",
    }
    keys |= {
        f"{kind}_{suffix}" for kind in "TCFP" for suffix in ("count", "allocated_namespace_bytes")
    }
    parsed = {}
    for line in raw.decode("ascii").splitlines():
        key, separator, value = line.partition("=")
        if separator != "=" or key not in keys or key in parsed or not value.isdecimal():
            raise Unknown("allocation_output_shape")
        number = int(value)
        if number > 2**53 - 1:
            raise Unknown("allocation_output_range")
        parsed[key] = number
    if (
        set(parsed) != keys
        or parsed["scan_complete"] != 1
        or parsed["expected"] != len(mapping)
        or parsed["matched"] != len(mapping)
        or parsed["missing"] != 0
        or sum(parsed[f"{kind}_count"] for kind in "TCFP") != len(mapping)
    ):
        raise Unknown("allocation_output_incomplete")
    return {**result, **parsed}


def owned_database_allocation(
    observer: StorageObserver, *, steps: list[dict] | None = None, reached: dict | None = None
) -> dict:
    """Fixed-count readonly metadata + one full-cohort allocated namespace scan.

    The per-command and per-statement bounds are independent, not a whole-sample
    two-second guarantee. Credentials and database names remain driver-local.
    """
    from sqlalchemy import create_engine, text
    from sqlalchemy.pool import NullPool

    sequence, overflow, registry = observer.database_snapshot()
    if overflow:
        raise Unknown("database_registry_limit")
    url = _probe_step(steps, "url", observer.container.get_connection_url)
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
            if (
                _probe_step(
                    steps,
                    "readonly",
                    lambda: connection.execute(text("SHOW transaction_read_only")).scalar_one(),
                )
                != "on"
            ):
                raise Unknown("database_not_readonly")
            before = _probe_step(
                steps,
                "metadata_before",
                lambda: _metadata(connection.execute(text(_METADATA_SQL)).all()),
            )
            mapping = {
                before[name]: kind if state == "ready" else "P"
                for name, (kind, state, _) in registry.items()
                if name in before
            }
            pgdata = _probe_step(
                steps, "allocation", lambda: _allocation_filesystem(observer.container_id, mapping)
            )
            physical = {key: observer.binding.get(key, "") for key in ("run", "attempt")}
            physical["container"] = observer.container_id
            physical_server = hashlib.sha256(
                json.dumps(physical, sort_keys=True).encode()
            ).hexdigest()
            # Retain only independently validated scan evidence before later SQL
            # or disposal can fail. Cohort revalidation has not completed yet.
            if reached is not None:
                reached.update(
                    {
                        "pgdata": {
                            key: pgdata[key]
                            for key in (
                                "status",
                                "image_sha256",
                                "owned_running_servers",
                                "owned_volumes",
                                "total_bytes",
                                "free_bytes",
                                "total_inodes",
                                "free_inodes",
                            )
                        },
                        "owned_allocation": {
                            **{
                                key: value
                                for key, value in pgdata.items()
                                if key
                                not in {
                                    "status",
                                    "image_sha256",
                                    "owned_running_servers",
                                    "owned_volumes",
                                }
                            },
                            "status": "unknown",
                            "reason": "cohort_not_validated",
                            "physical_server": physical_server,
                            "registered": len(registry),
                            "mapped": len(mapping),
                            "cohort_complete": False,
                            "accounting": "allocated_namespace_not_logical_or_unique_extents",
                            "unattributed_pgdata_status": "unknown",
                        },
                    }
                )
            after = _probe_step(
                steps,
                "metadata_after",
                lambda: _metadata(connection.execute(text(_METADATA_SQL)).all()),
            )
            wal_temp = _probe_step(
                steps,
                "wal_temp",
                lambda: dict(connection.execute(text(_WAL_TEMP_SQL)).mappings().one()),
            )
            if set(wal_temp) != {"wal_bytes", "temp_bytes_cumulative"} or any(
                type(value) is not int or value < 0 for value in wal_temp.values()
            ):
                raise Unknown("allocation_wal_temp_shape")
            current_sequence, current_overflow, _ = observer.database_snapshot()
            complete = (
                sequence == current_sequence
                and not current_overflow
                and before == after
                and len(mapping) == len(registry)
                and not pgdata["P_count"]
            )
            allocation = {
                "status": "ok" if complete else "unknown",
                "reason": "complete_owned_cohort" if complete else "cohort_changed_or_pending",
                "physical_server": physical_server,
                "registered": len(registry),
                "mapped": len(mapping),
                "cohort_complete": complete,
                "accounting": "allocated_namespace_not_logical_or_unique_extents",
                "unattributed_pgdata_status": "unknown",
                **{
                    key: value
                    for key, value in pgdata.items()
                    if key
                    not in {"status", "image_sha256", "owned_running_servers", "owned_volumes"}
                },
            }
            return {
                "owned_allocation": allocation,
                "pgdata": {
                    key: pgdata[key]
                    for key in (
                        "status",
                        "image_sha256",
                        "owned_running_servers",
                        "owned_volumes",
                        "total_bytes",
                        "free_bytes",
                        "total_inodes",
                        "free_inodes",
                    )
                },
                "postgres": {
                    "status": "unknown",
                    "reason": "logical_sizes_not_sampled",
                    "readonly": True,
                    **wal_temp,
                },
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
        # Internal names never leave this process. Hooks only update metadata.
        self.databases: dict[str, tuple[str, str, int]] = {}
        self.migrating_databases: set[str] = set()
        self.database_sequence = 0
        self.database_overflow = False
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

    def database_event(self, event: str, container: Any, name: str, kind: str) -> None:
        if container is not self.container or kind not in {"T", "C", "F"}:
            return
        if event not in {
            "intent",
            "created",
            "pending",
            "ready",
            "migration_intent",
            "migrated_ready",
        }:
            return
        if not isinstance(name, str) or not name or len(name.encode()) > 63:
            return
        with self.lock:
            previous = self.databases.get(name)
            if event == "migration_intent":
                if name not in self.databases and len(self.databases) >= MAX_DATABASES:
                    self.database_overflow = True
                    self.database_sequence += 1
                    return
                self.migrating_databases.add(name)
                event = "intent"
            elif event == "ready" and name in self.migrating_databases:
                return
            elif event == "migrated_ready":
                self.migrating_databases.discard(name)
                event = "ready"
            # A template declares its class before using the fresh bootstrap path.
            if previous is not None and previous[0] != kind:
                if previous[0] == "T" and kind == "F" and event == "created":
                    kind = "T"
                else:
                    return
            if previous is None and len(self.databases) >= MAX_DATABASES:
                self.database_overflow = True
                self.database_sequence += 1
                return
            if previous is not None and previous[1] == event:
                return
            self.database_sequence += 1
            self.databases[name] = (kind, event, self.database_sequence)

    def database_snapshot(self):
        with self.lock:
            return self.database_sequence, self.database_overflow, dict(self.databases)

    def sample(self, phase: str) -> None:
        with self.lock:
            if len(self.rows) >= MAX_SAMPLES:
                return
            counters = dict(self.events)
        probes: dict[str, list[dict]] = {"docker": [], "postgres": []}
        elapsed = _elapsed_s(self.started)
        root = safe(lambda: filesystem("/"))
        docker = safe(lambda: docker_totals(steps=probes["docker"]))
        allocation = _unknown("not_observed")
        if phase in {"periodic", "create_error"} and self.database_snapshot()[2]:
            reached: dict = {}
            outcome = safe(
                lambda: owned_database_allocation(self, steps=probes["postgres"], reached=reached)
            )
            measured = {**reached, **outcome}
            allocation = measured.get("owned_allocation", measured)
            pgdata = measured.get("pgdata", _unknown("allocation_unavailable"))
            postgres = measured.get("postgres", _unknown("logical_sizes_not_sampled"))
        else:
            pgdata = safe(lambda: owned_pgdata(self.container_id))
            postgres = safe(lambda: database_totals(self.container, steps=probes["postgres"]))
        row = {
            "phase": phase,
            "elapsed_s": elapsed,
            "events": counters,
            "root": root,
            "docker": docker,
            "pgdata": pgdata,
            "postgres": postgres,
            "owned_allocation": allocation,
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


def database_event(event: str, container: Any, name: str, kind: str) -> None:
    if _ACTIVE is not None:
        with contextlib.suppress(Exception):
            _ACTIVE.database_event(event, container, name, kind)


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
