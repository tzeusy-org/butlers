"""Software lifecycle controls; catalog/runtime authority is proved in PG tests."""

from __future__ import annotations

import ast
import asyncio
import json
import os
import signal
import subprocess
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, contextmanager
from urllib.parse import urlparse

import pytest
from sqlalchemy.exc import ProgrammingError

from butlers.testing import migrated_templates
from butlers.testing.migrated_templates import MigrationStage, TemplateCache, TemplateError

pytestmark = pytest.mark.unit


class _MemoryBackend:
    """Explicit software stand-in for IO, never a SQL or performance receipt."""

    def __init__(self):
        self.builds = 0
        self.clones = []
        self.databases = set()
        self.roles = set()
        self.allowed = {}
        self.changed_authority = False
        self.fail_build = False
        self.started = threading.Event()
        self.release = threading.Event()
        self.release.set()
        self.real_child = False
        self.admin_url = "postgresql://software@localhost/software"

    def identity(self):
        return ("software-server",)

    def url(self, name, entry):
        # No password, role value or connection parameter is retained in proof.
        return f"postgresql://software@localhost/{name}"

    def create_role(self, entry):
        self.roles.add(entry.role)

    def construct(self, entry, name, cancel):
        self.builds += 1
        self.databases.add(name)
        self.started.set()
        assert self.release.wait(5)
        if cancel.is_set() or self.fail_build:
            raise TemplateError("planted-construction-failure")
        if self.real_child:
            migrated_templates._Backend.construct(self, entry, name, cancel)

    def validate_stages(self, entry, name):
        assert name in self.databases

    def validate_source_flags(self, entry):
        assert self.allowed[entry.source_name] is False

    def database_state(self, name):
        return ("software-owner", False, ("full-acl",), ("full-settings",))

    def authority(self, entry, *, complete=False):
        return ("complete" if complete else "relevant", self.changed_authority)

    def schema_dump(self, name):
        return b"whole-software-schema"

    def allow_connections(self, name, allowed):
        self.allowed[name] = allowed

    def clone(self, entry, name):
        self.clones.append(name)
        self.databases.add(name)

    def drop_db(self, name):
        self.databases.discard(name)

    def drop_role(self, role):
        self.roles.discard(role)


def test_owned_cache_publishes_once_and_refuses_failed_or_changed_authority(monkeypatch, tmp_path):
    """REQ-testing-052 / REQ-testing-053: atomic ownership, not synthetic SQL parity."""
    backend = _MemoryBackend()
    cache = TemplateCache(backend)
    stages = (MigrationStage("core"),)
    cancel = threading.Event()
    try:
        backend.release.clear()
        with ThreadPoolExecutor(max_workers=2) as workers:
            first = workers.submit(cache.borrow, "first", stages, cancel)
            assert backend.started.wait(5)
            second = workers.submit(cache.borrow, "second", stages, cancel)
            assert not cache.entries  # A half-build is never published.
            backend.release.set()
            one, two = first.result(5), second.result(5)
        assert one != two
        assert backend.builds == 1
        assert backend.clones == ["first", "second"]
        assert len(backend.roles) == 1
        reference = cache.fresh_reference(one)
        assert reference != one and backend.builds == 2
        backend.changed_authority = True
        with pytest.raises(TemplateError, match="principal-authority-changed"):
            cache.borrow("must-refuse", stages, cancel)
        assert "must-refuse" not in backend.databases and not cache.entries
        backend.changed_authority = False
        backend.fail_build = True
        with pytest.raises(TemplateError, match="planted-construction-failure"):
            cache.borrow("failed", stages, cancel)
        assert "failed" not in backend.databases and not cache.entries
        backend.fail_build = False
        assert urlparse(cache.borrow("restored", stages, cancel)).path == "/restored"
        cancel.set()
        with pytest.raises(TemplateError, match="borrow-cancelled"):
            cache.borrow("cancelled", stages, cancel)
        assert "cancelled" not in backend.databases
    finally:
        backend.release.set()
        cache.close()
    assert not backend.databases and not backend.roles

    # The actual owning subprocess runs a real noisy helper. IO is replaced;
    # this establishes lifetime ownership, never actual SQL/template parity.
    original_popen = migrated_templates.subprocess.Popen
    children = []
    marker = tmp_path / "builder-started"
    healthy = False
    descendant = True
    fail_parent = False

    def helper_child(args, **kwargs):
        if args[1:] != ["-m", "butlers.testing.migrated_templates", "--build"]:
            return original_popen(args, **kwargs)
        source = (
            "import os,sys,time;from pathlib import Path;sys.stdin.buffer.read();"
            "os.write(1,b'x'*262144);os.write(2,b'y'*262144);"
            + (
                "import subprocess;subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']);"
                if descendant
                else ""
            )
            + f"Path({str(marker)!r}).write_text('ready');"
            + ("sys.exit(3)" if fail_parent else "pass" if healthy else "time.sleep(30)")
        )
        child = original_popen([args[0], "-c", source], **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(migrated_templates.subprocess, "Popen", helper_child)
    monkeypatch.setattr(migrated_templates, "_BUILD_SECONDS", 0.2)
    backend = _MemoryBackend()
    backend.real_child = True
    cache = TemplateCache(backend)
    try:
        with pytest.raises(TemplateError, match="construction-timeout"):
            cache.borrow("timeout", stages, threading.Event())
        assert marker.exists() and children[-1].poll() is not None
        assert not migrated_templates._active_group_members(children[-1].pid)
        assert "timeout" not in backend.databases and not cache.entries
        marker.unlink()
        cancelled = threading.Event()
        with ThreadPoolExecutor(max_workers=1) as worker:
            pending = worker.submit(cache.borrow, "cancel", stages, cancelled)
            deadline = time.monotonic() + 10
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert marker.exists()
            cancelled.set()
            with pytest.raises(TemplateError, match="construction-cancelled"):
                pending.result(5)
        assert children[-1].poll() is not None and not cache.entries
        assert not migrated_templates._active_group_members(children[-1].pid)
        healthy = True
        descendant = True
        with pytest.raises(TemplateError, match="construction-left-active-descendant"):
            cache.borrow("descendant-refusal", stages, threading.Event())
        assert children[-1].returncode == 0
        assert not migrated_templates._active_group_members(children[-1].pid)
        assert "descendant-refusal" not in backend.databases and not cache.entries
        fail_parent = True
        with pytest.raises(TemplateError, match="^construction-failed$"):
            cache.borrow("nonzero-refusal", stages, threading.Event())
        assert children[-1].returncode == 3
        assert not migrated_templates._active_group_members(children[-1].pid)
        assert "nonzero-refusal" not in backend.databases and not cache.entries
        fail_parent = False
        descendant = False
        assert (
            urlparse(cache.borrow("released-positive", stages, threading.Event())).path
            == "/released-positive"
        )
        assert children[-1].returncode == 0
    finally:
        for child in children:
            # The neutralized old builder may publish after its parent exits.
            # The test controller still owns and kills its planted group.
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)
        cache.close()

    monkeypatch.setattr(migrated_templates.subprocess, "Popen", original_popen)

    # SQLAlchemy renders bound parameters when an exception escapes. A real
    # typed synthetic failure must become a closed category at the public API;
    # no credentials, SQL operands or formatted exception enter a receipt.
    backend = _MemoryBackend()
    cache = TemplateCache(backend)
    private_parameters = []

    class Connection:
        def execute(self, statement, params):
            private_parameters.append(params["password"])
            raise ProgrammingError(str(statement), params, ValueError())

    @contextmanager
    def private_connection(url):
        yield Connection()

    def private_sql_failure(entry):
        migrated_templates._Backend.create_role(backend, entry)

    monkeypatch.setattr(migrated_templates, "_connection", private_connection)
    monkeypatch.setattr(backend, "create_role", private_sql_failure)
    try:
        with pytest.raises(TemplateError, match="^owned-template-operation-failed$") as caught:
            cache.borrow("private-error", stages, threading.Event())
        assert caught.value.__suppress_context__ is True
        rendered = "".join(traceback.format_exception(caught.value))
        assert private_parameters and all(value not in rendered for value in private_parameters)
        assert not cache.entries and not backend.databases
        original_drop = backend.drop_db

        def private_cleanup_failure(name):
            raise ProgrammingError(
                "private cleanup", {"password": private_parameters[0]}, ValueError()
            )

        monkeypatch.setattr(backend, "drop_db", private_cleanup_failure)
        with pytest.raises(TemplateError, match="^owned-template-operation-failed$") as caught:
            cache.borrow("private-cleanup", stages, threading.Event())
        assert all(
            value not in "".join(traceback.format_exception(caught.value))
            for value in private_parameters
        )
        with pytest.raises(TemplateError, match="^owned-cleanup-incomplete$") as caught:
            cache.close()
        assert all(
            value not in "".join(traceback.format_exception(caught.value))
            for value in private_parameters
        )
        assert cache.closed and cache.databases
        with pytest.raises(TemplateError, match="^cache-owner-closed$"):
            cache.borrow("closed-owner", stages, threading.Event())
        monkeypatch.setattr(backend, "drop_db", original_drop)
        cache.close()
        assert not cache.databases and not cache.roles and not backend.databases
        cache = TemplateCache(backend)
        monkeypatch.setattr(backend, "create_role", _MemoryBackend.create_role.__get__(backend))
        assert urlparse(cache.borrow("privacy-restored", stages, threading.Event())).path == (
            "/privacy-restored"
        )
    finally:
        cache.close()

    # The builder's actual phase boundary projects a typed failure into a tiny
    # closed receipt. Neither the SQL operand nor its synthetic password is
    # formatted; malformed/extra fields cannot become a public error suffix.
    from butlers.testing import migration

    class DatabaseFailure(Exception):
        pgcode = "23505"

    state = {"phase": "input", "stage": -1}
    private = ProgrammingError(
        "private statement", {"password": "private-value"}, DatabaseFailure()
    )

    class BuildConnection:
        def execute(self, statement):
            return None

    @contextmanager
    def build_connection(url):
        yield BuildConnection()

    def failed_bootstrap(*args):
        raise private

    with monkeypatch.context() as patch:
        patch.setattr(migrated_templates, "_connection", build_connection)
        patch.setattr(migration, "bootstrap_extensions", lambda *_: None)
        patch.setattr(migration, "_bootstrap_migration_prerequisites", failed_bootstrap)
        with pytest.raises(ProgrammingError) as failure:
            migrated_templates._build(
                {
                    "admin_url": "postgresql://software",
                    "name": "software",
                    "role": "software",
                    "url": "postgresql://software",
                    "stages": [],
                },
                state,
            )
    receipt = migrated_templates._builder_failure(failure.value, state)
    assert receipt == {
        "phase": "bootstrap",
        "stage": -1,
        "category": "database",
        "sqlstate": "23505",
        "revision": "UNKNOWN",
    }
    status_file = tmp_path / "closed-builder-status.json"
    status_file.write_text(json.dumps(receipt))
    assert (
        migrated_templates._read_builder_failure(status_file)
        == ":bootstrap:-1:database:23505:UNKNOWN"
    )
    assert "private" not in status_file.read_text()
    for corrupted in (
        {**receipt, "stage": True},
        {**receipt, "category": "private-value"},
        {**receipt, "sqlstate": "private-value"},
        {**receipt, "revision": "private-value"},
        {**receipt, "operand": "private-value"},
    ):
        status_file.write_text(json.dumps(corrupted))
        assert migrated_templates._read_builder_failure(status_file) == ""
    status_file.write_text(json.dumps(receipt))
    assert (
        migrated_templates._read_builder_failure(status_file)
        == ":bootstrap:-1:database:23505:UNKNOWN"
    )

    # A real published migration frame supplies only its declared revision,
    # while the driver's public duplicate-column code survives the finite
    # decoder. No raw statement/context is needed to position the boundary.
    import runpy

    from alembic import op

    class DuplicateColumn(Exception):
        pgcode = "42701"

    module = runpy.run_path(
        str(
            migrated_templates._ROOT / "roster/relationship/migrations/002_align_contacts_schema.py"
        )
    )
    private_failure = ProgrammingError(
        "private statement", {"password": "private-value"}, DuplicateColumn()
    )
    with monkeypatch.context() as patch:

        def failed_statement(*args, **kwargs):
            raise private_failure

        patch.setattr(op, "execute", failed_statement)
        with pytest.raises(ProgrammingError) as positioned:
            module["upgrade"]()
    receipt = migrated_templates._builder_failure(
        positioned.value, {"phase": "migration", "stage": 3}
    )
    assert receipt["revision"] == "rel_002" and receipt["sqlstate"] == "42701"
    status_file.write_text(json.dumps(receipt))
    assert migrated_templates._read_builder_failure(status_file) == (
        ":migration:3:database:42701:rel_002"
    )
    assert "private" not in status_file.read_text()

    # Read the actual owning fixture's requested stages, then execute the real
    # builder and rel_037 upgrade through an explicit software catalog. Separate
    # version tables cannot make its fixed Relationship table migrate twice.
    fixture = runpy.run_path(
        str(migrated_templates._ROOT / "roster/relationship/tests/test_resolve_outbound_channel.py")
    )["pool"].__wrapped__
    captured = []

    class SeedPool:
        async def execute(self, *args):
            return None

    @asynccontextmanager
    async def fixture_pool(container, *, stages, **kwargs):
        captured.append(stages)
        yield SeedPool()

    async def read_fixture():
        instance = fixture(object())
        try:
            await anext(instance)
        finally:
            await instance.aclose()

    with monkeypatch.context() as patch:
        patch.setitem(fixture.__globals__, "migrated_pool", fixture_pool)
        asyncio.run(read_fixture())
    assert len(captured) == 1
    authority_migration = runpy.run_path(
        str(
            migrated_templates._ROOT
            / "roster/relationship/migrations/037_fact_content_authority.py"
        )
    )
    columns = set()
    versions = set()

    def apply_statement(statement):
        # Only the real first ADD-COLUMN statement is modelled. Every actual
        # statement still runs through op.execute; this is not PostgreSQL proof.
        if statement == authority_migration["upgrade_statements"]()[0]:
            if "content_authority" in columns:
                raise ProgrammingError(
                    "private statement", {"password": "private-value"}, DuplicateColumn()
                )
            columns.add("content_authority")

    def apply_stage(url, chain, *, schema=None, revision=None):
        if chain == "relationship" and schema not in versions:
            authority_migration["upgrade"]()
            versions.add(schema)

    def build_stages(requested):
        columns.clear()
        versions.clear()
        state = {}
        payload = {
            "admin_url": "postgresql://software",
            "name": "software",
            "role": "software",
            "url": "postgresql://software",
            "stages": [
                {"chain": stage.chain, "schema": stage.schema, "revision": stage.revision}
                for stage in requested
            ],
        }
        with monkeypatch.context() as patch:
            patch.setattr(migrated_templates, "_connection", build_connection)
            patch.setattr(migration, "bootstrap_extensions", lambda *_: None)
            patch.setattr(migration, "_bootstrap_migration_prerequisites", lambda *_: None)
            patch.setattr(migration, "_upgrade_chain_to_revision", apply_stage)
            patch.setattr(op, "execute", apply_statement)
            migrated_templates._build(payload, state)
        return state

    build_stages(captured[0])
    assert versions == {"relationship"} and columns == {"content_authority"}
    duplicate_stages = (*captured[0][:2], MigrationStage("relationship"), *captured[0][2:])
    with pytest.raises(ProgrammingError) as positioned:
        build_stages(duplicate_stages)
    receipt = migrated_templates._builder_failure(
        positioned.value, {"phase": "migration", "stage": 3}
    )
    assert receipt["revision"] == "rel_037" and receipt["sqlstate"] == "42701"
    build_stages(captured[0])
    assert versions == {"relationship"} and columns == {"content_authority"}

    # Actual filesystem/Git inputs and finite ambient aliases bind the key;
    # these are software provenance controls, not a real server equivalence.
    checkout = tmp_path / "key-source"
    checkout.mkdir()
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    for path in (
        "scripts/init-db.sql",
        "pyproject.toml",
        "uv.lock",
        "alembic/versions/core/key.py",
    ):
        target = checkout / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# source\n" if path.endswith(".py") else "source\n")
    initial = migrated_templates.source_profile(checkout)
    fixture = checkout / "tests/fixture.py"
    fixture.parent.mkdir()
    fixture.write_text("# complete fixture input\n")
    assert migrated_templates.source_profile(checkout) != initial
    changed = migrated_templates.source_profile(checkout)
    fixture.chmod(0o755)
    assert migrated_templates.source_profile(checkout) != changed
    keys = migrated_templates._environment_inputs(
        ast.parse(
            "from os import getenv as setting; import os as system; env=system.environ; x=setting('TEMPLATE_KEY'); y=env['OTHER_KEY']"
        )
    )
    assert keys == {"TEMPLATE_KEY", "OTHER_KEY"}
    with pytest.raises(TemplateError, match="unbound-migration-environment"):
        migrated_templates._environment_inputs(ast.parse("import os; x=os.getenv(unknown_key)"))
    for source in (
        "import os; keys=list(os.environ)",
        "from os import environ as env; x=dict(env)",
        "import os; env=os.environ; x=getattr(env,'get')('KEY')",
        "import os; x=vars(os)['environ']",
    ):
        with pytest.raises(TemplateError, match="unbound-migration-environment"):
            migrated_templates._environment_inputs(ast.parse(source))
    for stage in (MigrationStage(True), MigrationStage("core", revision=False)):
        with pytest.raises(TemplateError, match="unresolved-stage"):
            stage.resolved()

    # Repeated cancellation while actual cleanup is blocked must keep the
    # thread owner joined, not turn a cancelled await into orphaned IO.
    composite_stages = (
        MigrationStage("core"),
        MigrationStage("memory", schema="relationship"),
        MigrationStage("chronicler", schema="chronicler"),
    )
    assert (
        migration._composite_test_search_path(
            ("relationship", "chronicler"), stages=composite_stages
        )
        == "relationship,chronicler,public"
    )
    for invalid in ((), "relationship", (True,), ("unmigrated",), ("relationship", "relationship")):
        with pytest.raises(ValueError):
            migration._composite_test_search_path(invalid, stages=composite_stages)

    cleanup_entered = threading.Event()
    cleanup_release = threading.Event()
    cleaned = threading.Event()

    def cleanup():
        cleanup_entered.set()
        assert cleanup_release.wait(5)
        cleaned.set()

    async def cancel_cleanup():
        owner = asyncio.create_task(migration._join_owned_cleanup(asyncio.to_thread(cleanup)))
        assert await asyncio.to_thread(cleanup_entered.wait, 5)
        owner.cancel()
        await asyncio.sleep(0)
        owner.cancel()
        await asyncio.sleep(0)
        assert not owner.done() and not cleaned.is_set()
        cleanup_release.set()
        await owner
        assert cleaned.is_set()

    try:
        asyncio.run(cancel_cleanup())
    finally:
        cleanup_release.set()

    # These are real operation clocks over software controls. The same bounded
    # owner follows asyncio.to_thread without disclosing source/key/SQL values;
    # nested observations are spans, not an invented additive wall-time total.
    from butlers.testing import migration_metrics

    token = migration_metrics.begin("fresh")
    try:
        migrated_templates.source_profile(checkout)

        def threaded_span():
            with migration_metrics.measure("pool-connect", mode="fresh"):
                time.sleep(0.002)

        asyncio.run(asyncio.to_thread(threaded_span))
        with pytest.raises(ValueError, match="planted operation failure"):
            with migration_metrics.measure("cleanup-database"):
                raise ValueError("planted operation failure")
        observed = migration_metrics.snapshot()
        assert observed["policy"] == "fresh" and observed["complete"] is True
        assert [span["phase"] for span in observed["spans"]] == [
            "key-hash",
            "pool-connect",
            "cleanup-database",
        ]
        assert [span["success"] for span in observed["spans"]] == [True, True, False]
        assert all(
            type(span["elapsed_s"]) is float
            and span["elapsed_s"] >= 0
            and set(span) == {"phase", "mode", "elapsed_s", "success"}
            for span in observed["spans"]
        )
        assert str(checkout) not in json.dumps(observed)
        with pytest.raises(ValueError, match="unknown migration provisioning span"):
            with migration_metrics.measure("private operand"):
                pass
        with monkeypatch.context() as patch:
            patch.setattr(migration_metrics, "_MAX_EVENTS", len(observed["spans"]))
            threaded_span()
        assert migration_metrics.snapshot()["complete"] is False
    finally:
        migration_metrics.finish(token)
    # Restoring the prior per-test owner is distinct from fabricating fresh
    # measurements after a failed/overflowed capture.
    token = migration_metrics.begin("cloned-eligible")
    try:
        threaded_span()
        assert migration_metrics.snapshot()["complete"] is True
        assert len(migration_metrics.snapshot()["spans"]) == 1
        reference_backend = _MemoryBackend()
        reference_cache = TemplateCache(reference_backend)
        try:
            clone = reference_cache.borrow("clock-clone", stages, threading.Event())
            reference_cache.fresh_reference(clone)
            reference_span = migration_metrics.snapshot()["spans"][-1]
            assert reference_span["phase"] == "fixture-total"
            assert reference_span["mode"] == "fresh-reference"
            assert reference_span["success"] is True
        finally:
            reference_cache.close()
    finally:
        migration_metrics.finish(token)
