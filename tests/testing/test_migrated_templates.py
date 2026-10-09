"""Software lifecycle controls; catalog/runtime authority is proved in PG tests."""

from __future__ import annotations

import ast
import asyncio
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import pytest

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
        self.roles.remove(role)


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

    def helper_child(args, **kwargs):
        if args[1:] != ["-m", "butlers.testing.migrated_templates", "--build"]:
            return original_popen(args, **kwargs)
        source = (
            "import os,sys,time;from pathlib import Path;sys.stdin.buffer.read();"
            "os.write(1,b'x'*262144);os.write(2,b'y'*262144);"
            f"Path({str(marker)!r}).write_text('ready');"
            + ("pass" if healthy else "time.sleep(30)")
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
        healthy = True
        assert (
            urlparse(cache.borrow("released-positive", stages, threading.Event())).path
            == "/released-positive"
        )
        assert children[-1].returncode == 0
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)
        cache.close()

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

    # Repeated cancellation while actual cleanup is blocked must keep the
    # thread owner joined, not turn a cancelled await into orphaned IO.
    from butlers.testing import migration

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
