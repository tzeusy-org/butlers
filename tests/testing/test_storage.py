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
    for phase in ("before_cleanup", "after_cleanup"):
        storage.write_report(
            "storage-" + phase + ".json",
            dict(version=1, identity=identity, phase=phase, root=fs, docker=docker),
        )
    sample = dict(
        phase="server_started",
        elapsed_s=0,
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
def test_actual_pg_storage_query_is_readonly_and_aggregate():
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

    finally:
        container.stop(delete_volume=True)


def test_real_observer_thread_records_owned_events_and_joins(monkeypatch):
    container = SimpleNamespace(get_wrapped_container=lambda: SimpleNamespace(id="a" * 64))
    monkeypatch.setattr(storage, "identity", lambda: {"worker": "gw0"})
    monkeypatch.setattr(storage, "filesystem", lambda path: {"status": "unknown"})
    monkeypatch.setattr(storage, "docker_totals", lambda: {"status": "unknown"})
    monkeypatch.setattr(storage, "owned_pgdata", lambda cid: {"status": "unknown"})
    monkeypatch.setattr(storage, "database_totals", lambda server: {"status": "unknown"})
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
    monkeypatch.setattr(storage, "docker_totals", lambda: {"status": "unknown"})
    monkeypatch.setattr(storage, "owned_pgdata", lambda cid: {"status": "unknown"})

    def timed_out(server):
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
