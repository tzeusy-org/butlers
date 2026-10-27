"""Real cross-process lifecycle proof for the shared core_272 function.

The pause observes completed, uncommitted DDL on the actual migration connection;
it does not replace PostgreSQL or the migration. Child output is closed metadata,
and the disposable database locator travels only over private stdin.
"""

from __future__ import annotations

import importlib.util
import json
import os
import select
import shutil
import signal
import subprocess
import sys
import time
from contextlib import ExitStack, contextmanager, nullcontext, suppress
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import DBAPIError

from butlers.testing.migration import create_migrated_test_db, migration_db_name

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION = _ROOT / "alembic/versions/core/core_272_health_refill_premise_probe.py"
_SIGNATURE = "public.resolve_health_medication_refill(text,numeric,numeric)"


def _migration():
    spec = importlib.util.spec_from_file_location("_core272_concurrency", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _emit(**fields):
    print(json.dumps(fields), flush=True)


def _worker() -> int:
    config = json.loads(sys.stdin.readline())
    migration = _migration()
    if config.get("remove_serializer"):
        # Source-specific negative control, only inside this disposable child.
        migration._serialized_function_ddl = nullcontext
    engine = create_engine(config["url"], connect_args={"connect_timeout": 5})
    outcome = {"phase": "error", "kind": "worker"}
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("SET LOCAL search_path TO " + config["schema"] + ",public")
            connection.exec_driver_sql(
                "SELECT pg_catalog.set_config('lock_timeout', %s, true)",
                (str(config.get("timeout_ms", 0)) + "ms",),
            )
            before = connection.exec_driver_sql("SHOW lock_timeout").scalar_one()
            pid = connection.exec_driver_sql("SELECT pg_backend_pid()").scalar_one()
            _emit(phase="connected", pid=pid)
            paused = False
            marker = (
                "CREATE OR REPLACE FUNCTION"
                if config["operation"] == "upgrade"
                else "DROP FUNCTION"
            )

            def after_statement(_conn, _cursor, statement, _args, _context, _many):
                nonlocal paused
                if config.get("pause") and not paused and marker in statement:
                    paused = True
                    _emit(phase="ddl-written", pid=pid)
                    if sys.stdin.readline().strip() != "release":
                        raise RuntimeError("release_required")

            event.listen(connection, "after_cursor_execute", after_statement)
            with Operations.context(MigrationContext.configure(connection)):
                getattr(migration, config["operation"])()
            after = connection.exec_driver_sql("SHOW lock_timeout").scalar_one()
            assert before == after
            if config.get("rollback"):
                connection.rollback()
            outcome = {
                "phase": "rolled-back" if config.get("rollback") else "committed",
                "pid": pid,
            }
    except DBAPIError as error:
        outcome = {"phase": "error", "kind": "database", "sqlstate": error.orig.pgcode}
    except Exception:
        outcome = {"phase": "error", "kind": "worker"}
    finally:
        engine.dispose()
    _emit(**outcome)
    return int(outcome["phase"] == "error")


class _Child:
    def __init__(self, url, operation, *, schema="general", **options):
        self.deadline = time.monotonic() + 20
        self.process = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve())],
            cwd=_ROOT,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            bufsize=0,
        )
        self.buffer = b""
        self.pid = None
        assert self.process.stdin is not None
        self.process.stdin.write(
            (
                json.dumps({"url": url, "operation": operation, "schema": schema, **options}) + "\n"
            ).encode()
        )
        self.process.stdin.flush()

    def next(self):
        assert self.process.stdout is not None
        while b"\n" not in self.buffer:
            remaining = self.deadline - time.monotonic()
            assert remaining > 0, "migration child deadline"
            readable, _, _ = select.select([self.process.stdout], [], [], remaining)
            assert readable, "migration child stage timeout"
            chunk = os.read(self.process.stdout.fileno(), 4096)
            assert chunk, "migration child closed without terminal stage"
            self.buffer += chunk
            assert len(self.buffer) <= 16384, "unexpected child output"
        line, self.buffer = self.buffer.split(b"\n", 1)
        result = json.loads(line)
        if result["phase"] == "connected":
            self.pid = result["pid"]
        return result

    def expect(self, phase):
        result = self.next()
        assert result["phase"] == phase, result
        return result

    def release(self):
        assert self.process.stdin is not None
        self.process.stdin.write(b"release\n")
        self.process.stdin.flush()

    def finish(self):
        result = self.next()
        assert result["phase"] in {"committed", "rolled-back", "error"}, result
        code = self.process.wait(timeout=max(0.01, self.deadline - time.monotonic()))
        assert code == int(result["phase"] == "error"), result
        return result

    def close(self):
        if self.process.poll() is None:
            with suppress(ProcessLookupError):
                os.killpg(self.process.pid, signal.SIGTERM)
            try:
                self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                with suppress(ProcessLookupError):
                    os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(timeout=2)
        for stream in (self.process.stdin, self.process.stdout):
            if stream is not None:
                stream.close()


@contextmanager
def _children():
    children = []
    with ExitStack() as cleanup:
        try:
            yield children
        finally:
            for child in children:
                cleanup.callback(child.close)


@pytest.fixture(scope="module")
def database(postgres_container):
    url = create_migrated_test_db(
        postgres_container, migration_db_name(), chains=["core"], revisions={"core": "core_271"}
    )
    engine = create_engine(url, connect_args={"connect_timeout": 5})
    try:
        yield url, engine
    finally:
        engine.dispose()


def _apply(engine, operation):
    with engine.begin() as connection, Operations.context(MigrationContext.configure(connection)):
        getattr(_migration(), operation)()


def _wait_for_lock(engine, pid, *, advisory):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        with engine.connect() as connection:
            waiting = connection.execute(
                text(
                    "SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_locks "
                    "WHERE pid=:pid AND NOT granted AND (locktype='advisory')=:advisory)"
                ),
                {"pid": pid, "advisory": advisory},
            ).scalar_one()
        if waiting:
            return
        time.sleep(0.02)
    pytest.fail("actual migration did not reach the expected PostgreSQL lock wait")


def _catalog(engine):
    with engine.connect() as connection:
        return (
            connection.execute(
                text(
                    "SELECT pg_catalog.pg_get_functiondef(p.oid) AS definition, p.prosecdef, "
                    "p.provolatile, p.proconfig, p.prorettype::regtype::text AS return_type, "
                    "EXISTS(SELECT 1 FROM pg_catalog.aclexplode(COALESCE(p.proacl, "
                    "pg_catalog.acldefault('f',p.proowner))) a "
                    "WHERE a.grantee=0 AND a.privilege_type='EXECUTE') AS public_executes, "
                    "has_function_privilege('butler_switchboard_rw',p.oid,'EXECUTE') AS sb_executes "
                    "FROM pg_catalog.pg_proc p WHERE p.oid=to_regprocedure(:signature)"
                ),
                {"signature": _SIGNATURE},
            )
            .mappings()
            .one_or_none()
        )


def _assert_installed(engine):
    row = _catalog(engine)
    assert row is not None
    assert row["prosecdef"] and row["provolatile"] == "s"
    assert row["return_type"] == "boolean"
    assert row["proconfig"] == ["search_path=pg_catalog, pg_temp"]
    assert not row["public_executes"] and row["sb_executes"]
    return dict(row)


def _assert_disconnected(engine, children):
    for child in children:
        assert child.process.poll() is not None
        deadline = min(child.deadline, time.monotonic() + 2)
        while True:
            # A fresh transaction avoids retaining an activity snapshot while
            # PostgreSQL processes the already-exited client's termination.
            with engine.connect() as connection:
                remaining = connection.execute(
                    text("SELECT count(*) FROM pg_stat_activity WHERE pid=:pid"), {"pid": child.pid}
                ).scalar_one()
            if remaining == 0:
                break
            assert time.monotonic() < deadline, "owned migration backend did not disconnect"
            time.sleep(min(0.02, max(0, deadline - time.monotonic())))
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT count(*) FROM pg_stat_activity WHERE pid=:pid"), {"pid": child.pid}
                ).scalar_one()
                == 0
            )


def _contend(url, engine, *, first_op="upgrade", second_op="upgrade", remove=False, rollback=False):
    with _children() as children:
        first = _Child(url, first_op, pause=True, remove_serializer=remove, rollback=rollback)
        children.append(first)
        first.expect("connected")
        first.expect("ddl-written")
        second = _Child(url, second_op, schema="switchboard", remove_serializer=remove)
        children.append(second)
        second.expect("connected")
        try:
            _wait_for_lock(engine, second.pid, advisory=not remove)
        finally:
            first.release()
        a, b = first.finish(), second.finish()
        assert a["phase"] == ("rolled-back" if rollback else "committed"), a
        if remove:
            assert b["phase"] == "error" and b["sqlstate"] == "23505", b
        else:
            assert b["phase"] == "committed", b
        _assert_disconnected(engine, children)


def test_repeated_real_first_create_and_serializer_removal_counterfactual(database):
    """The actual historical DDL race, not a source-shape or sleep-only control."""
    url, engine = database
    _apply(engine, "downgrade")
    _contend(url, engine, remove=True)
    expected = _assert_installed(engine)
    for _ in range(8):
        _apply(engine, "downgrade")
        assert _catalog(engine) is None
        _contend(url, engine)
        assert _assert_installed(engine) == expected


def test_real_lifecycle_both_orders_and_rollback_release(database):
    url, engine = database
    _apply(engine, "downgrade")
    _contend(url, engine, second_op="downgrade")
    assert _catalog(engine) is None
    _apply(engine, "upgrade")
    _contend(url, engine, first_op="downgrade")
    expected = _assert_installed(engine)
    _apply(engine, "downgrade")
    _contend(url, engine, rollback=True)
    assert _assert_installed(engine) == expected


def test_real_shorter_lock_timeout_refuses_without_partial_DDL_then_recovers(database):
    url, engine = database
    _apply(engine, "downgrade")
    with _children() as children:
        with engine.begin() as holder:
            holder.exec_driver_sql(_migration()._FUNCTION_DDL_LOCK_SQL)
            child = _Child(url, "upgrade", timeout_ms=250)
            children.append(child)
            child.expect("connected")
            started = time.monotonic()
            result = child.finish()
            assert result["phase"] == "error" and result["sqlstate"] == "55P03", result
            assert time.monotonic() - started < 4
            assert _catalog(engine) is None
        child = _Child(url, "upgrade", timeout_ms=250)
        children.append(child)
        child.expect("connected")
        assert child.finish()["phase"] == "committed"
        _assert_disconnected(engine, children)
    _assert_installed(engine)


if __name__ == "__main__":
    raise SystemExit(_worker())
