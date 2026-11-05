"""Closed storage diagnostics never supply false capacity or change pytest outcomes."""

import json
import sys
from types import SimpleNamespace

import pytest

from butlers.testing import storage

pytestmark = pytest.mark.unit


def test_filesystem_reports_bytes_and_inodes_and_refuses_unavailable(monkeypatch):
    monkeypatch.setattr(
        storage.os,
        "statvfs",
        lambda _: SimpleNamespace(f_blocks=11, f_frsize=4096, f_bavail=3, f_files=20, f_favail=7),
    )
    assert storage.filesystem("/") == {
        "status": "ok",
        "total_bytes": 45056,
        "free_bytes": 12288,
        "total_inodes": 20,
        "free_inodes": 7,
    }

    def denied(_):
        raise PermissionError("PRIVATE-SYNTHETIC-SENTINEL")

    monkeypatch.setattr(storage.os, "statvfs", denied)
    assert storage.safe(lambda: storage.filesystem("/")) == {
        "status": "unknown",
        "reason": "measurement_unavailable",
    }


@pytest.mark.parametrize(
    "kind", ["positive", "overflow", "timeout", "closed_pipe_timeout", "failure"]
)
def test_real_child_capture_is_bounded_and_content_blind(kind):
    code = {
        "positive": "import os; os.write(1,b'owned-metadata')",
        "overflow": "import os\nwhile True: os.write(1,b'x'*4096)",
        "timeout": "import time; time.sleep(10)",
        "closed_pipe_timeout": "import os,time; os.close(1); time.sleep(10)",
        "failure": "import sys; sys.exit(7)",
    }[kind]
    if kind == "positive":
        assert storage._capture([sys.executable, "-B", "-c", code], 0.5) == b"owned-metadata"
    else:
        with pytest.raises(
            storage.Unknown,
            match={
                "overflow": "command_output_limit",
                "timeout": "command_timeout",
                "closed_pipe_timeout": "command_timeout",
                "failure": "command_failed",
            }[kind],
        ):
            storage._capture([sys.executable, "-B", "-c", code], 0.5)


def test_owned_pgdata_has_exact_identity_and_real_df_units(monkeypatch):
    calls = []

    def capture(argv):
        calls.append(argv)
        if "{{range .Mounts}}{{.Type}}{{println}}{{end}}" in argv:
            return b"volume\nvolume\nbind\n\n"
        if argv[1] == "inspect":
            return ("true sha256:" + "b" * 64).encode()
        return b"Filesystem total used free capacity mount\nvolume 100 70 30 70% /data\n"

    monkeypatch.setattr(storage, "_capture", capture)
    assert storage.owned_pgdata("a" * 64) == {
        "status": "ok",
        "owned_running_servers": 1,
        "owned_volumes": 2,
        "image_sha256": "b" * 64,
        "total_bytes": 102400,
        "free_bytes": 30720,
        "total_inodes": 100,
        "free_inodes": 30,
    }
    assert calls[0][-1] == "a" * 64
    assert all(call[:3] == ["docker", "exec", "a" * 64] for call in calls[2:])
    with pytest.raises(storage.Unknown, match="server_identity"):
        storage.owned_pgdata("another-server")
    assert len(calls) == 4


def test_disabled_observer_is_resource_free_and_original_exception_survives(monkeypatch):
    monkeypatch.delenv("BUTLERS_TEST_STORAGE_EVIDENCE", raising=False)

    def forbidden(*args):
        raise AssertionError("observer started")

    monkeypatch.setattr(storage, "StorageObserver", forbidden)
    with pytest.raises(RuntimeError, match="original"):
        with storage.observe(object()):
            raise RuntimeError("original")
    assert storage._ACTIVE is None


def test_atomic_report_refuses_overcap_and_preserves_prior_receipt(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_EVIDENCE_DIR", str(tmp_path))
    storage.write_report("storage-gw0.json", {"status": "unknown"})
    assert json.loads((tmp_path / "storage-gw0.json").read_bytes()) == {"status": "unknown"}
    with pytest.raises(storage.Unknown, match="report_limit"):
        storage.write_report("storage-gw0.json", {"oversize": "x" * storage.MAX_REPORT})
    assert json.loads((tmp_path / "storage-gw0.json").read_bytes()) == {"status": "unknown"}
    assert (tmp_path / "storage-gw0.json").stat().st_mode & 0o777 == 0o600
    assert len(list(tmp_path.iterdir())) == 1


@pytest.mark.parametrize(
    "invalid",
    [
        "missing_host",
        "missing_worker",
        "wrong_source",
        "nested_extra",
        "unknown_measurement",
        "truncated",
        "duplicate_key",
        "wrong_worker",
        "sample_cap",
        "invalid_worker_count",
        "extra_worker",
        "missing_probe_stage",
        "wrong_probe_stage",
        "unknown_probe_outcome",
        "nonfinite_probe_time",
        "overbound_probe_time",
        "sample_completion_before_start",
    ],
)
def test_inventory_refuses_incomplete_evidence_with_complete_positive(
    invalid, tmp_path, monkeypatch
):
    monkeypatch.setenv("TEST_EVIDENCE_DIR", str(tmp_path))
    monkeypatch.setenv("PYTEST_XDIST_AUTO_WORKERS", "1")
    monkeypatch.setattr(storage, "enabled", lambda: True)
    identity = {"source": "a" * 40, "worker": "controller"}
    monkeypatch.setattr(storage, "identity", lambda: dict(identity))
    fs = dict(status="ok", total_bytes=100, free_bytes=30, total_inodes=20, free_inodes=10)
    docker = {
        "status": "ok",
        "filesystem": fs,
        **{
            key: dict(count=1, active=1, bytes=10)
            for key in ("images", "containers", "volumes", "build_cache")
        },
    }
    probes = {
        "docker": [
            dict(stage=stage, outcome="ok", elapsed_ms=0) for stage in ("system_df", "root_info")
        ],
        "postgres": [
            dict(stage=stage, outcome="ok", elapsed_ms=0)
            for stage in ("url", "connect", "readonly", "database_sizes", "wal", "temp", "dispose")
        ],
    }
    for phase in ("before_cleanup", "after_cleanup"):
        storage.write_report(
            "storage-" + phase + ".json",
            dict(
                version=1,
                identity=identity,
                phase=phase,
                root=fs,
                docker=docker,
                probes={"docker": probes["docker"]},
            ),
        )
    sample = dict(
        phase="server_started",
        elapsed_s=0,
        completed_elapsed_s=1,
        probes=probes,
        events=dict(template_created=0, clone_created=0, create_failed=0, storage_error=0),
        root=fs,
        docker=docker,
        pgdata={**fs, "owned_running_servers": 1, "owned_volumes": 1, "image_sha256": "b" * 64},
        postgres={
            "status": "ok",
            "readonly": True,
            "attribution": "prefix_only",
            "clone_current_bytes_status": "unknown_untracked",
            **{
                key: 1
                for key in (
                    "databases",
                    "template_named_databases",
                    "test_named_databases",
                    "database_bytes",
                    "largest_database_bytes",
                    "template_named_database_bytes",
                    "test_named_database_bytes",
                    "wal_bytes",
                    "temp_bytes_cumulative",
                )
            },
        },
    )
    record = dict(
        version=1,
        identity={**identity, "worker": "gw0", "server": "c" * 64},
        status="observer_joined",
        sample_limit_reached=False,
        samples=[sample, {**sample, "phase": "before_server_stop", "elapsed_s": 1}],
    )
    path = tmp_path / "storage-gw0.json"
    storage.write_report(path.name, record)
    storage.inventory()
    inventory = tmp_path / "storage-inventory.json"
    assert json.loads(inventory.read_bytes())["status"] == "sampled"
    if invalid == "missing_host":
        (tmp_path / "storage-before_cleanup.json").unlink()
    elif invalid == "missing_worker":
        path.unlink()
    elif invalid == "wrong_source":
        record["identity"]["source"] = "d" * 40
    elif invalid == "wrong_worker":
        record["identity"]["worker"] = "gw1"
    elif invalid == "nested_extra":
        record["samples"][0]["postgres"]["PRIVATE-SYNTHETIC-SENTINEL"] = 1
    elif invalid == "unknown_measurement":
        record["samples"][0]["root"] = dict(status="unknown", reason="command_timeout")
    elif invalid == "sample_cap":
        record["sample_limit_reached"] = True
    elif invalid == "invalid_worker_count":
        monkeypatch.setenv("PYTEST_XDIST_AUTO_WORKERS", "33")
    elif invalid == "extra_worker":
        storage.write_report("storage-gw1.json", record)
    elif invalid == "missing_probe_stage":
        record["samples"][0]["probes"]["docker"].pop()
    elif invalid == "wrong_probe_stage":
        record["samples"][0]["probes"]["docker"][0]["stage"] = "PRIVATE-SYNTHETIC-SENTINEL"
    elif invalid == "unknown_probe_outcome":
        record["samples"][0]["probes"]["postgres"][0]["outcome"] = "other"
    elif invalid == "nonfinite_probe_time":
        record["samples"][0]["probes"]["postgres"][0]["elapsed_ms"] = float("nan")
    elif invalid == "overbound_probe_time":
        record["samples"][0]["probes"]["postgres"][0]["elapsed_ms"] = 7_200_001
    elif invalid == "sample_completion_before_start":
        record["samples"][0]["completed_elapsed_s"] = -1
    if invalid not in {"missing_host", "missing_worker"}:
        storage.write_report(path.name, record)
    if invalid == "truncated":
        path.write_bytes(b'{"version":')
    elif invalid == "duplicate_key":
        path.write_bytes(path.read_bytes().replace(b'"version":1', b'"version":1,"version":1'))
    storage.inventory()
    receipt = inventory.read_bytes()
    assert json.loads(receipt)["status"] == "unknown"
    assert b"PRIVATE-SYNTHETIC-SENTINEL" not in receipt
    assert json.loads(receipt)["max_total_bytes"] == 35 * storage.MAX_REPORT
    if invalid == "extra_worker":
        assert json.loads(receipt)["unexpected_workers"] == [1]


def test_observer_join_preserves_original_failure_when_measurement_fails(monkeypatch):
    calls = []

    class Observer:
        def __init__(self, container):
            calls.append("construct")

        def start(self):
            calls.append("start")

        def stop(self):
            calls.append("joined")
            raise RuntimeError("PRIVATE-SYNTHETIC-SENTINEL")

    monkeypatch.setattr(storage, "enabled", lambda: True)
    monkeypatch.setattr(storage, "StorageObserver", Observer)
    with pytest.raises(ValueError, match="original-verdict"):
        with storage.observe(object()):
            raise ValueError("original-verdict")
    assert calls == ["construct", "start", "joined"]
    assert storage._ACTIVE is None


@pytest.mark.integration
def test_actual_pg_storage_query_is_readonly_and_aggregate(monkeypatch):
    # This bounded positive must not census thousands of unrelated worker DBs.
    # Only this proof owns an extra server; ordinary session fixtures are unchanged.
    from sqlalchemy import create_engine, text
    from testcontainers.postgres import PostgresContainer

    container = PostgresContainer("pgvector/pgvector:pg17")
    try:
        container.start()
        engine = create_engine(container.get_connection_url(), isolation_level="AUTOCOMMIT")
        try:
            with engine.connect() as connection:
                connection.execute(text("CREATE DATABASE test_storage_positive"))
        finally:
            engine.dispose()
        result = storage.database_totals(container)
        assert result["status"] == "ok" and result["readonly"] is True
        assert result["databases"] >= 3 and result["test_named_databases"] >= 1
        assert result["database_bytes"] >= result["test_named_database_bytes"] > 0
        assert result["attribution"] == "prefix_only"
        assert result["clone_current_bytes_status"] == "unknown_untracked"
        assert result["wal_bytes"] > 0 and result["temp_bytes_cumulative"] >= 0
        assert all(type(value) in (int, bool, str) for value in result.values())
        # The same proof-owned server also positions real SQL/tool coverage for
        # the new path without taxing the shared session server's full census.
        monkeypatch.setattr(
            storage, "identity", lambda: {"worker": "gw0", "run": "1", "attempt": "1"}
        )
        observer = storage.StorageObserver(container)
        observer.database_event("intent", container, "test_storage_positive", "F")
        observer.database_event("ready", container, "test_storage_positive", "F")
        steps = []
        allocated = storage.owned_database_allocation(observer, steps=steps)
        assert allocated["owned_allocation"]["cohort_complete"] is True
        assert allocated["owned_allocation"]["F_count"] == 1
        assert allocated["owned_allocation"]["F_allocated_namespace_bytes"] > 0
        assert allocated["postgres"]["readonly"] is True
        assert allocated["postgres"]["status"] == "unknown"
        assert allocated["postgres"]["wal_bytes"] > 0
        assert [step["stage"] for step in steps] == [
            "url",
            "connect",
            "readonly",
            "metadata_before",
            "allocation",
            "metadata_after",
            "wal_temp",
            "dispose",
        ]

    finally:
        container.stop(delete_volume=True)


def test_real_observer_thread_records_owned_events_and_joins(monkeypatch):
    container = SimpleNamespace(get_wrapped_container=lambda: SimpleNamespace(id="a" * 64))
    monkeypatch.setattr(storage, "identity", lambda: {"worker": "gw0"})
    monkeypatch.setattr(storage, "filesystem", lambda path: {"status": "unknown"})
    monkeypatch.setattr(storage, "docker_totals", lambda **kwargs: {"status": "unknown"})
    monkeypatch.setattr(storage, "owned_pgdata", lambda cid: {"status": "unknown"})
    monkeypatch.setattr(storage, "database_totals", lambda server, **kwargs: {"status": "unknown"})
    receipts = []
    monkeypatch.setattr(storage, "write_report", lambda name, record: receipts.append(record))
    observer = storage.StorageObserver(container)
    observer.start()
    observer.note("clone_created", container)
    observer.note("clone_created", object())
    observer.stop()
    assert not observer.thread.is_alive()
    assert receipts[-1]["status"] == "observer_joined"
    assert receipts[-1]["samples"][0]["phase"] == "server_started"
    assert receipts[-1]["samples"][-1]["phase"] == "before_server_stop"
    assert receipts[-1]["samples"][-1]["events"]["clone_created"] == 1
    assert receipts[-1]["samples"][-1]["postgres"]["status"] == "unknown"


def test_query_timeout_is_closed_unknown_and_preserves_original_verdict(monkeypatch):
    from psycopg2.errors import QueryCanceled
    from sqlalchemy.exc import OperationalError

    container = SimpleNamespace(get_wrapped_container=lambda: SimpleNamespace(id="a" * 64))
    monkeypatch.setattr(storage, "identity", lambda: {"worker": "gw0"})
    monkeypatch.setattr(storage, "enabled", lambda: True)
    monkeypatch.setattr(storage, "filesystem", lambda path: {"status": "unknown"})
    monkeypatch.setattr(storage, "docker_totals", lambda **kwargs: {"status": "unknown"})
    monkeypatch.setattr(storage, "owned_pgdata", lambda cid: {"status": "unknown"})

    def timed_out(server, **kwargs):
        raise OperationalError("PRIVATE-SYNTHETIC-SENTINEL", {}, QueryCanceled())

    monkeypatch.setattr(storage, "database_totals", timed_out)
    receipts = []
    monkeypatch.setattr(storage, "write_report", lambda name, record: receipts.append(record))
    original = ValueError("original-verdict")
    with pytest.raises(ValueError) as raised:
        with storage.observe(container):
            raise original
    assert raised.value is original
    assert storage._ACTIVE is None
    assert receipts[-1]["status"] == "observer_joined"
    assert receipts[-1]["samples"]
    assert all(
        row["postgres"] == {"status": "unknown", "reason": "measurement_unavailable"}
        for row in receipts[-1]["samples"]
    )
    assert "PRIVATE-SYNTHETIC-SENTINEL" not in json.dumps(receipts)


@pytest.mark.parametrize(
    ("failed_stage", "invalid_clock"),
    [(None, False), ("system_df", False), ("root_info", False), (None, True)],
)
def test_actual_host_probe_identifies_the_failing_docker_stage(
    failed_stage, invalid_clock, monkeypatch
):
    clock = [0.0]
    calls = []
    receipts = []
    fs = dict(status="ok", total_bytes=100, free_bytes=30, total_inodes=20, free_inodes=10)
    monkeypatch.setattr(
        storage.time,
        "monotonic",
        lambda: float("nan") if invalid_clock and clock[0] else clock[0],
    )
    monkeypatch.setattr(storage, "enabled", lambda: True)
    monkeypatch.setattr(storage, "identity", lambda: {"worker": "controller"})
    monkeypatch.setattr(storage, "filesystem", lambda _: dict(fs))
    monkeypatch.setattr(storage, "write_report", lambda name, record: receipts.append(record))

    def capture(argv):
        stage = "system_df" if argv[1:3] == ["system", "df"] else "root_info"
        calls.append(stage)
        clock[0] += 0.25
        if stage == failed_stage:
            raise storage.Unknown("command_timeout")
        if stage == "root_info":
            return b"/synthetic-docker-root\n"
        return b"\n".join(
            json.dumps(dict(Type=kind, TotalCount="1", Active="1", Size="10B")).encode()
            for kind in ("Images", "Containers", "Local Volumes", "Build Cache")
        )

    monkeypatch.setattr(storage, "_capture", capture)
    storage.host("before_cleanup")
    result = receipts[-1]
    assert result["docker"]["status"] == ("unknown" if failed_stage else "ok")
    assert "probes" in result, "actual Docker failure lost its probe stage"
    steps = result["probes"]["docker"]
    expected = ["system_df"] if failed_stage == "system_df" else ["system_df", "root_info"]
    assert calls == expected == [step["stage"] for step in steps]
    assert [step["outcome"] for step in steps] == [
        "command_timeout" if stage == failed_stage else "ok" for stage in expected
    ]
    assert all(step["elapsed_ms"] == (None if invalid_clock else 250) for step in steps)
    assert storage._probe_steps(steps, ("system_df", "root_info")) == (
        failed_stage is None and not invalid_clock
    )
    assert "NaN" not in json.dumps(result)
    if failed_stage is None:
        assert storage._docker(result["docker"])
        assert result["docker"]["volumes"] == dict(count=1, active=1, bytes=10)
    assert "PRIVATE-SYNTHETIC-SENTINEL" not in json.dumps(result)


@pytest.mark.parametrize(
    ("failed_stage", "code", "outcome"),
    [
        (None, None, "ok"),
        ("url", None, "other"),
        ("connect", "08006", "connection"),
        ("readonly", "42501", "permission"),
        ("database_sizes", "57014", "query_canceled"),
        ("wal", "53100", "disk_full"),
        ("temp", "PRIVATE-SYNTHETIC-SENTINEL", "other"),
        ("dispose", None, "other"),
    ],
)
def test_actual_pg_probe_positions_driver_failures_and_disposes(
    failed_stage, code, outcome, monkeypatch
):
    import sqlalchemy
    from sqlalchemy.exc import OperationalError

    clock = [0.0]
    calls = []
    disposed = []
    monkeypatch.setattr(storage.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(storage, "identity", lambda: {"worker": "gw0"})
    monkeypatch.setattr(storage, "filesystem", lambda _: {"status": "unknown"})
    monkeypatch.setattr(storage, "docker_totals", lambda **kwargs: {"status": "unknown"})
    monkeypatch.setattr(storage, "owned_pgdata", lambda _: {"status": "unknown"})
    monkeypatch.setattr(storage, "write_report", lambda *args: None)

    class DriverFailure(Exception):
        pgcode = code

    def entered(stage):
        calls.append(stage)
        clock[0] += 0.125
        if stage == failed_stage:
            raise OperationalError("PRIVATE-SYNTHETIC-SENTINEL", {}, DriverFailure())

    totals = dict(
        databases=5,
        template_named_databases=1,
        test_named_databases=1,
        database_bytes=100,
        largest_database_bytes=30,
        template_named_database_bytes=20,
        test_named_database_bytes=30,
    )

    class Connection:
        def __init__(self):
            self.index = 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, query):
            stage = ["readonly", "database_sizes", "wal", "temp"][self.index]
            self.index += 1
            entered(stage)
            return SimpleNamespace(
                scalar_one=lambda: {"readonly": "on", "wal": 20, "temp": 0}[stage],
                mappings=lambda: SimpleNamespace(one=lambda: dict(totals)),
            )

    class Engine:
        def connect(self):
            entered("connect")
            return Connection()

        def dispose(self):
            disposed.append(True)
            entered("dispose")

    def create_engine(url, **kwargs):
        assert kwargs["connect_args"] == {
            "connect_timeout": 2,
            "options": (
                "-c default_transaction_read_only=on -c statement_timeout=2000 -c lock_timeout=1000"
            ),
        }
        assert kwargs["poolclass"].__name__ == "NullPool"
        return Engine()

    def url():
        entered("url")
        return "postgresql://synthetic.invalid/test"

    monkeypatch.setattr(sqlalchemy, "create_engine", create_engine)
    container = SimpleNamespace(
        get_wrapped_container=lambda: SimpleNamespace(id="a" * 64), get_connection_url=url
    )
    observer = storage.StorageObserver(container)
    observer.sample("server_started")
    row = observer.rows[-1]
    assert row["postgres"]["status"] == ("unknown" if failed_stage else "ok")
    assert "probes" in row, "actual driver failure lost its probe stage"
    steps = row["probes"]["postgres"]
    assert [step["stage"] for step in steps] == calls
    assert all(step["elapsed_ms"] == 125 for step in steps)
    failing = [step for step in steps if step["outcome"] != "ok"]
    assert failing == (
        [dict(stage=failed_stage, outcome=outcome, elapsed_ms=125)] if failed_stage else []
    )
    assert disposed == ([] if failed_stage == "url" else [True])
    assert row["elapsed_s"] <= row["completed_elapsed_s"]
    if failed_stage is None:
        assert row["postgres"] == {
            "status": "ok",
            "readonly": True,
            "attribution": "prefix_only",
            "clone_current_bytes_status": "unknown_untracked",
            **totals,
            "wal_bytes": 20,
            "temp_bytes_cumulative": 0,
        }
    assert "PRIVATE-SYNTHETIC-SENTINEL" not in json.dumps(row)


def test_observer_reports_owned_allocation_as_unknown_until_actual_coverage(monkeypatch):
    container = SimpleNamespace(get_wrapped_container=lambda: SimpleNamespace(id="a" * 64))
    monkeypatch.setattr(storage, "identity", lambda: {"worker": "gw0"})
    monkeypatch.setattr(storage, "filesystem", lambda _: {"status": "unknown"})
    monkeypatch.setattr(storage, "docker_totals", lambda **kwargs: {"status": "unknown"})
    monkeypatch.setattr(storage, "owned_pgdata", lambda _: {"status": "unknown"})
    monkeypatch.setattr(storage, "database_totals", lambda *args, **kwargs: {"status": "unknown"})
    monkeypatch.setattr(storage, "write_report", lambda *args: None)
    observer = storage.StorageObserver(container)
    observer.sample("periodic")
    row = observer.rows[-1]
    assert "owned_allocation" in row, "actual observer omitted exact-owned coverage status"
    assert row["owned_allocation"]["status"] == "unknown"
    assert "PRIVATE-SYNTHETIC-SENTINEL" not in json.dumps(row)


def _allocation_observer(monkeypatch, worker="gw0"):
    container = SimpleNamespace(
        get_wrapped_container=lambda: SimpleNamespace(id="a" * 64),
        get_connection_url=lambda: "postgresql://private:private@local/private",
    )
    monkeypatch.setattr(storage, "identity", lambda: {"worker": worker, "run": "1", "attempt": "1"})
    return storage.StorageObserver(container)


def test_trusted_database_metadata_is_opt_in_identity_bound_and_pending(monkeypatch):
    observer = _allocation_observer(monkeypatch)
    monkeypatch.setattr(storage, "_ACTIVE", observer)
    other = object()
    storage.database_event("intent", other, "private", "C")
    assert observer.database_snapshot()[2] == {}
    for event, kind in [("intent", "T"), ("intent", "F"), ("created", "F"), ("ready", "F")]:
        storage.database_event(event, observer.container, "private", kind)
    assert observer.database_snapshot()[2]["private"][:2] == ("T", "created")
    storage.database_event("ready", observer.container, "private", "T")
    assert observer.database_snapshot()[2]["private"][:2] == ("T", "ready")
    monkeypatch.setattr(storage, "_ACTIVE", None)
    storage.database_event("intent", observer.container, "ignored", "F")
    assert "ignored" not in observer.database_snapshot()[2]
    monkeypatch.setattr(
        storage,
        "_ACTIVE",
        SimpleNamespace(database_event=lambda *a: (_ for _ in ()).throw(RuntimeError("private"))),
    )
    assert storage.database_event("intent", other, "private", "C") is None


def test_registry_limit_does_not_limit_database_creation(monkeypatch):
    observer = _allocation_observer(monkeypatch)
    monkeypatch.setattr(storage, "MAX_DATABASES", 2)
    for name in ("one", "two", "three"):
        observer.database_event("intent", observer.container, name, "F")
    assert observer.database_snapshot()[1] is True
    assert len(observer.database_snapshot()[2]) == 2
    with pytest.raises(storage.Unknown, match="database_registry_limit"):
        storage.owned_database_allocation(observer)


@pytest.mark.parametrize(
    "rows",
    [
        [(True, "private", 1663)],
        [(1, "private", 1664)],
        [(1, "a", 1663), (1, "b", 1663)],
        [(1, "a", 1663), (2, "a", 1663)],
        [(1, "x" * 64, 1663)],
    ],
)
def test_metadata_refuses_ambiguous_or_nondefault_tablespaces(rows):
    with pytest.raises(storage.Unknown):
        storage._metadata(rows)
    assert storage._metadata([(1, "valid", 1663)]) == {"valid": 1}


def _fake_allocation_sql(monkeypatch, metadata, change=None):
    import sqlalchemy

    calls = []
    disposed = []

    class Result:
        def scalar_one(self):
            return "on"

        def all(self):
            return metadata() if callable(metadata) else metadata

        def mappings(self):
            return self

        def one(self):
            return {"wal_bytes": 7, "temp_bytes_cumulative": 11}

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, statement):
            calls.append(str(statement))
            if change and len(calls) == 3:
                change()
            return Result()

    class Engine:
        def connect(self):
            return Connection()

        def dispose(self):
            disposed.append(True)

    def create(url, **kwargs):
        assert kwargs["connect_args"] == {
            "connect_timeout": 2,
            "options": "-c default_transaction_read_only=on -c statement_timeout=2000 -c lock_timeout=1000",
        }
        return Engine()

    monkeypatch.setattr(sqlalchemy, "create_engine", create)
    return calls, disposed


def _real_allocation_command(monkeypatch, tmp_path):
    data = tmp_path / "data"
    (data / "base").mkdir(parents=True)
    (data / "pg_tblspc").mkdir()
    real_capture = storage._capture
    calls = []
    monkeypatch.setattr(
        storage,
        "_owned_server",
        lambda _: {
            "status": "ok",
            "owned_running_servers": 1,
            "owned_volumes": 1,
            "image_sha256": "b" * 64,
        },
    )

    def capture(argv, seconds=3):
        assert argv[:5] == ["docker", "exec", "a" * 64, "sh", "-c"]
        calls.append(argv)
        script = argv[5].replace("data=/var/lib/postgresql/data", "data=" + str(data))
        return real_capture(["sh", "-c", script, "storage", argv[-1]], seconds)

    monkeypatch.setattr(storage, "_capture", capture)
    return data, calls


def test_full_cohort_600_includes_dominant_last_clone_with_fixed_query_count(monkeypatch, tmp_path):
    observer = _allocation_observer(monkeypatch)
    data, commands = _real_allocation_command(monkeypatch, tmp_path)
    rows = []
    expected = {kind: 0 for kind in "TCF"}
    for index in range(600):
        name, oid, kind = f"PRIVATE-{index}", index + 10000, "TCF"[index % 3]
        directory = data / "base" / str(oid)
        directory.mkdir()
        (directory / "relation").write_bytes(b"x" * (524288 if index == 599 else 4096))
        if index == 0:
            (directory / "hardlink").hardlink_to(directory / "relation")
        # Namespace allocation must not use logical length for sparse files.
        with (directory / "sparse").open("wb") as stream:
            stream.truncate(1048576)
        expected[kind] += sum(
            path.stat().st_blocks * 512 for path in [directory, *directory.iterdir()]
        )
        rows.append((oid, name, 1663))
        observer.database_event("intent", observer.container, name, kind)
        observer.database_event("ready", observer.container, name, kind)
    calls, disposed = _fake_allocation_sql(monkeypatch, rows)
    result = storage.owned_database_allocation(observer)
    allocation = result["owned_allocation"]
    assert allocation["status"] == "ok" and allocation["cohort_complete"] is True
    assert allocation["mapped"] == allocation["registered"] == 600
    assert len(commands) == 1 and len(calls) == 4 and disposed == [True]
    assert calls == [
        "SHOW transaction_read_only",
        storage._METADATA_SQL,
        storage._METADATA_SQL,
        storage._WAL_TEMP_SQL,
    ]
    for kind in "TCF":
        assert allocation[f"{kind}_count"] == 200
        assert allocation[f"{kind}_allocated_namespace_bytes"] == expected[kind]
    assert allocation["F_allocated_namespace_bytes"] > allocation["C_allocated_namespace_bytes"]
    assert result["postgres"]["status"] == "unknown"
    assert "database_bytes" not in result["postgres"]
    assert "PRIVATE-" not in json.dumps(result) and "postgresql" not in json.dumps(result)
    # Physical server identity cannot split one server into one per worker.
    observer.binding["worker"] = "gw1"
    assert (
        storage.owned_database_allocation(observer)["owned_allocation"]["physical_server"]
        == allocation["physical_server"]
    )


@pytest.mark.parametrize("refusal", ["pending", "missing", "generation", "metadata"])
def test_full_current_coverage_refuses_partial_or_racing_cohort(monkeypatch, tmp_path, refusal):
    observer = _allocation_observer(monkeypatch)
    data, _ = _real_allocation_command(monkeypatch, tmp_path)
    (data / "base" / "10000").mkdir()
    observer.database_event("intent", observer.container, "private", "C")
    if refusal != "pending":
        observer.database_event("ready", observer.container, "private", "C")
    count = [0]

    def metadata():
        count[0] += 1
        return (
            [(10000 + int(refusal == "metadata" and count[0] > 1), "private", 1663)]
            if refusal != "missing"
            else []
        )

    change = (
        (lambda: observer.database_event("intent", observer.container, "new", "F"))
        if refusal == "generation"
        else None
    )
    _fake_allocation_sql(monkeypatch, metadata, change)
    result = storage.owned_database_allocation(observer)["owned_allocation"]
    assert result["status"] == "unknown" and result["cohort_complete"] is False


@pytest.mark.parametrize("refusal", ["symlink", "tablespace", "missing", "tool"])
def test_compound_scan_refuses_external_or_incomplete_allocation(monkeypatch, tmp_path, refusal):
    data, _ = _real_allocation_command(monkeypatch, tmp_path)
    directory = data / "base" / "10000"
    directory.mkdir()
    assert storage._allocation_filesystem("a" * 64, {10000: "C"})["C_count"] == 1
    if refusal == "symlink":
        (directory / "external").symlink_to(tmp_path)
    elif refusal == "tablespace":
        (data / "pg_tblspc" / "42").symlink_to(tmp_path)
    elif refusal == "missing":
        directory.rmdir()
    else:
        monkeypatch.setattr(
            storage,
            "_ALLOCATION_SCRIPT",
            storage._ALLOCATION_SCRIPT.replace("du -l -x -B1", "unsupported_tool -l -x -B1"),
        )
    with pytest.raises(storage.Unknown):
        storage._allocation_filesystem("a" * 64, {10000: "C"})


@pytest.mark.parametrize(
    "mutate",
    [
        lambda b: b.rsplit(b"\n", 2)[0],
        lambda b: b + b"scan_complete=1\n",
        lambda b: b.replace(b"matched=1", b"matched=0"),
        lambda b: b.replace(b"missing=0", b"missing=1"),
    ],
)
def test_compound_output_never_credits_truncation_or_false_footer(monkeypatch, tmp_path, mutate):
    data, _ = _real_allocation_command(monkeypatch, tmp_path)
    (data / "base" / "10000").mkdir()
    original = storage._capture
    monkeypatch.setattr(
        storage, "_capture", lambda argv, **kwargs: mutate(original(argv, **kwargs))
    )
    with pytest.raises(storage.Unknown):
        storage._allocation_filesystem("a" * 64, {10000: "C"})


def test_allocation_query_failure_preserves_error_identity_disposal_and_stage(monkeypatch):
    import sqlalchemy

    observer = _allocation_observer(monkeypatch)
    observer.database_event("intent", observer.container, "private", "F")
    original = RuntimeError("PRIVATE-SYNTHETIC-SENTINEL")
    original.orig = SimpleNamespace(pgcode="57014")
    disposed = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, sql):
            if str(sql) == "SHOW transaction_read_only":
                return SimpleNamespace(scalar_one=lambda: "on")
            raise original

    engine = SimpleNamespace(connect=lambda: Connection(), dispose=lambda: disposed.append(True))
    monkeypatch.setattr(sqlalchemy, "create_engine", lambda *a, **k: engine)
    steps = []
    with pytest.raises(RuntimeError) as raised:
        storage.owned_database_allocation(observer, steps=steps)
    assert raised.value is original and disposed == [True]
    assert steps[-2]["stage"] == "metadata_before" and steps[-2]["outcome"] == "query_canceled"
    result = storage.safe(lambda: storage.owned_database_allocation(observer))
    assert result == {"status": "unknown", "reason": "measurement_unavailable"}
    assert "PRIVATE" not in json.dumps(result)


def test_allocation_observer_uses_one_compound_path_not_legacy_sweep(monkeypatch, tmp_path):
    observer = _allocation_observer(monkeypatch)
    data, _ = _real_allocation_command(monkeypatch, tmp_path)
    (data / "base" / "10000").mkdir()
    observer.database_event("intent", observer.container, "private", "C")
    observer.database_event("ready", observer.container, "private", "C")
    _fake_allocation_sql(monkeypatch, [(10000, "private", 1663)])
    monkeypatch.setattr(storage, "filesystem", lambda _: {"status": "unknown"})
    monkeypatch.setattr(storage, "docker_totals", lambda **kwargs: {"status": "unknown"})
    monkeypatch.setattr(storage, "write_report", lambda *a: None)

    def no_legacy(*args, **kwargs):
        pytest.fail("allocation slot repeated legacy sweep or df commands")

    monkeypatch.setattr(storage, "owned_pgdata", no_legacy)
    monkeypatch.setattr(storage, "database_totals", no_legacy)
    observer.sample("periodic")
    assert observer.rows[-1]["owned_allocation"]["cohort_complete"] is True
    assert observer.rows[-1]["postgres"]["reason"] == "logical_sizes_not_sampled"
    assert storage._sample(observer.rows[-1]) is False  # No invented legacy complete metric.


def test_fresh_migration_cannot_be_ready_during_bootstrap_or_failed_migration(monkeypatch):
    observer = _allocation_observer(monkeypatch)
    container = observer.container
    for event in ("migration_intent", "intent", "created", "ready"):
        observer.database_event(event, container, "private", "F")
    assert observer.database_snapshot()[2]["private"][:2] == ("F", "created")
    observer.database_event("migrated_ready", container, "private", "F")
    assert observer.database_snapshot()[2]["private"][:2] == ("F", "ready")


def test_compound_scan_refuses_df_nonzero_despite_valid_output(monkeypatch, tmp_path):
    data, _ = _real_allocation_command(monkeypatch, tmp_path)
    (data / "base" / "10000").mkdir()
    assert storage._allocation_filesystem("a" * 64, {10000: "C"})["scan_complete"] == 1
    monkeypatch.setattr(
        storage,
        "_ALLOCATION_SCRIPT",
        'df() { command df "$@"; return 17; };\n' + storage._ALLOCATION_SCRIPT,
    )
    with pytest.raises(storage.Unknown):
        storage._allocation_filesystem("a" * 64, {10000: "C"})


def test_unregistered_oid_allocation_remains_unattributed(monkeypatch, tmp_path):
    data, _ = _real_allocation_command(monkeypatch, tmp_path)
    for oid in (10000, 20000):
        (data / "base" / str(oid)).mkdir()
        (data / "base" / str(oid) / "relation").write_bytes(b"x" * 4096)
    result = storage._allocation_filesystem("a" * 64, {10000: "C"})
    assert result["C_count"] == 1 and result["unmapped_oids"] == 1
    assert result["unmapped_bytes"] > 0
    assert result["T_count"] == result["F_count"] == result["P_count"] == 0


@pytest.mark.parametrize("failed_stage", [None, "metadata_after", "wal_temp", "dispose"])
def test_allocation_sample_retains_reached_scan_after_late_failure(
    monkeypatch, tmp_path, failed_stage
):
    import sqlalchemy

    observer = _allocation_observer(monkeypatch)
    data, commands = _real_allocation_command(monkeypatch, tmp_path)
    directory = data / "base" / "10000"
    directory.mkdir()
    (directory / "relation").write_bytes(b"x" * 4096)
    observer.database_event("intent", observer.container, "private", "C")
    observer.database_event("ready", observer.container, "private", "C")
    original = RuntimeError("PRIVATE-LATE-FAILURE")
    original.orig = SimpleNamespace(pgcode="57014")
    calls, disposed = _fake_allocation_sql(monkeypatch, [(10000, "private", 1663)])
    create = sqlalchemy.create_engine

    def engine_factory(*args, **kwargs):
        engine = create(*args, **kwargs)
        connect, dispose = engine.connect, engine.dispose

        def connection_factory():
            connection = connect()
            execute = connection.execute

            def execute_at_stage(statement):
                index = len(calls) + 1
                if (failed_stage == "metadata_after" and index == 3) or (
                    failed_stage == "wal_temp" and index == 4
                ):
                    raise original
                return execute(statement)

            connection.execute = execute_at_stage
            return connection

        def dispose_at_stage():
            dispose()
            if failed_stage == "dispose":
                raise original

        engine.connect, engine.dispose = connection_factory, dispose_at_stage
        return engine

    monkeypatch.setattr(sqlalchemy, "create_engine", engine_factory)
    monkeypatch.setattr(storage, "filesystem", lambda _: {"status": "ok"})
    monkeypatch.setattr(storage, "docker_totals", lambda **_: {"status": "ok"})
    observer.flush = lambda _: None
    observer.sample("periodic")
    row = observer.rows[-1]
    assert len(commands) == 1 and disposed == [True]
    assert row["pgdata"]["status"] == "ok"
    assert row["pgdata"]["total_bytes"] > 0
    allocation = row["owned_allocation"]
    assert allocation["C_count"] == 1
    assert allocation["C_allocated_namespace_bytes"] >= 4096
    import hashlib

    expected_identity = hashlib.sha256(
        json.dumps(
            {
                "run": observer.binding.get("run", ""),
                "attempt": observer.binding.get("attempt", ""),
                "container": observer.container_id,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    assert allocation.get("physical_server") == expected_identity
    assert allocation["cohort_complete"] is (failed_stage is None)
    assert allocation["status"] == ("ok" if failed_stage is None else "unknown")
    assert "PRIVATE" not in json.dumps(row) and "private" not in json.dumps(row)
    if failed_stage:
        assert row["postgres"]["status"] == "unknown"
        assert any(
            step["stage"] == failed_stage and step["outcome"] == "query_canceled"
            for step in row["probes"]["postgres"]
        )
        calls.clear()
        with pytest.raises(RuntimeError) as raised:
            storage.owned_database_allocation(observer)
        assert raised.value is original
    else:
        assert row["postgres"]["wal_bytes"] == 7
