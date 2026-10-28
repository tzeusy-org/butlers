"""Actual pytest/session and disposable serving identity proof, without providers.

Native execution is separate from source review. Child output remains private;
only a digest is used in failure text. No fixture-manager or principal is mocked.
"""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_SUPPORT = r"""
import asyncio
from contextlib import asynccontextmanager
import pytest
from testcontainers.postgres import PostgresContainer
from butlers.credential_store import CredentialPurposeConfig, create_system_global_credential_store
from butlers.db_authority.roles import purpose_profile
from butlers.testing.migration import async_serving_test_deployment
from tests.e2e import conftest as public
from tests.e2e import _serving_ecosystem as serving

EVENTS, OBJECTS, POOLS = [], [], []
ENTERED, RELEASE, CLOSING, CLOSE_RELEASE = (asyncio.Event() for _ in range(4))
SCENARIO = __SCENARIO__

@pytest.fixture(scope="session")
def postgres_container():
    with PostgresContainer("pgvector/pgvector:pg17") as container:
        yield container
    EVENTS.append("container_closed")

@asynccontextmanager
async def bootstrap(container):
    EVENTS.append("setup")
    try:
        async with async_serving_test_deployment(container) as deployment:
            runtime = deployment.runtime_database("general")
            runtime_pool = await runtime.connect()
            writer = await create_system_global_credential_store(
                CredentialPurposeConfig(deployment.purpose_url("credential")))
            writer_pool = writer.require_system_global_pool()
            deployment.retain(writer_pool)
            POOLS.extend((runtime_pool, writer_pool))
            if SCENARIO == "success":
                from types import SimpleNamespace
                from butlers.daemon import ButlerDaemon

                async def build_explicit(config):
                    daemon = ButlerDaemon(
                        butler_name="general", db=runtime, credential_purpose=config)
                    daemon.config = SimpleNamespace(db_schema="general")
                    try:
                        return await daemon._build_credential_store(runtime_pool)
                    finally:
                        # This provider-free proof does not start modules. Own
                        # every real admitted reader/writer even if build fails.
                        for resource in (daemon._shared_credentials_db,
                                         daemon._credential_purpose_pool):
                            if resource is not None:
                                deployment.retain(resource)
                selected = CredentialPurposeConfig(deployment.purpose_url("credential"))
                explicit = await build_explicit(selected)
                document = '{"native_session_owner_proof":true}'
                await explicit.store_codex_cli_auth(document)
                assert await explicit.load_codex_cli_auth() == document
                omitted = await build_explicit(None)
                assert omitted.has_system_global_authority is False
                with pytest.raises(RuntimeError):
                    await omitted.load_codex_cli_auth()
                restored = await build_explicit(selected)
                assert await restored.load_codex_cli_auth() == document
                EVENTS.append("actual_input_removal_restoration")
            async with runtime_pool.acquire() as connection:
                assert await connection.fetchval("SELECT current_user") == "butler_general_rw"
            async with writer_pool.acquire() as connection:
                assert await connection.fetchval("SELECT current_user") == purpose_profile(
                    "butlers", "credential").role
            EVENTS.append("physical_roles_admitted")
            ecosystem = public.ButlerEcosystem(
                pools={"general": runtime_pool}, postgres_container=container)
            ENTERED.set()
            try:
                if SCENARIO == "failure":
                    raise RuntimeError("source-owned-startup-failure")
                if SCENARIO in {"startup_cancel", "borrow_cancel"}:
                    await RELEASE.wait()
                yield ecosystem
            finally:
                if SCENARIO == "close_cancel":
                    CLOSING.set()
                    await CLOSE_RELEASE.wait()
    finally:
        assert all(pool.is_closing() for pool in POOLS)
        EVENTS.append("bootstrap_closed")

@pytest.fixture(scope="session")
async def opted_ecosystem(request, postgres_container):
    async with serving._session_ecosystem(
        request.session, postgres_container, bootstrap=bootstrap) as ecosystem:
        yield ecosystem
"""

_MANUAL = r"""
import asyncio
import pytest
from support import (CLOSE_RELEASE, CLOSING, ENTERED, EVENTS, POOLS, RELEASE, SCENARIO, bootstrap)
from tests.e2e import _serving_ecosystem as serving
pytestmark = pytest.mark.asyncio(loop_scope="session")

async def test_real_owner_protocol(request, postgres_container):
    yielded, finish = asyncio.Event(), asyncio.Event()
    async def first():
        async with serving._session_ecosystem(
            request.session, postgres_container, bootstrap=bootstrap):
            yielded.set()
            await finish.wait()
    async def borrower():
        async with serving._session_ecosystem(
            request.session, postgres_container, bootstrap=bootstrap):
            raise AssertionError("blocked setup unexpectedly completed")
    first_task = asyncio.create_task(first())
    borrowed = None
    try:
        if SCENARIO == "failure":
            with pytest.raises(RuntimeError, match="source-owned-startup-failure"):
                await first_task
            with pytest.raises(RuntimeError, match="retry is forbidden"):
                async with serving._session_ecosystem(
                    request.session, postgres_container, bootstrap=bootstrap):
                    pytest.fail("failed owner retried")
            assert EVENTS.count("setup") == 1
            return
        await asyncio.wait_for(ENTERED.wait(), 180)
        if SCENARIO == "startup_cancel":
            first_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first_task
            assert all(pool.is_closing() for pool in POOLS)
            return
        if SCENARIO == "borrow_cancel":
            borrowed = asyncio.create_task(borrower())
            await asyncio.sleep(0)
            borrowed.cancel()
            with pytest.raises(asyncio.CancelledError):
                await borrowed
            owner = request.session.stash[serving._SESSION_OWNER]
            assert not owner.setup_task.done()
            assert not any(pool.is_closing() for pool in POOLS)
            RELEASE.set()
            await asyncio.wait_for(yielded.wait(), 2)
            finish.set()
            await first_task
            return
        await asyncio.wait_for(yielded.wait(), 2)
        if SCENARIO == "mismatch":
            loop = asyncio.get_running_loop()
            owner = request.session.stash[serving._SESSION_OWNER]
            other_loop = asyncio.new_event_loop()
            try:
                for container, selected_loop, factory in (
                    (object(), loop, bootstrap),
                    (postgres_container, other_loop, bootstrap),
                    (postgres_container, loop, lambda container: bootstrap(container)),
                ):
                    with pytest.raises(RuntimeError, match="input identity changed"):
                        owner.require_identity(container, selected_loop, factory)
            finally:
                other_loop.close()
            assert not any(pool.is_closing() for pool in POOLS)
            finish.set()
            await first_task
            return
        assert SCENARIO == "close_cancel"
        finish.set()
        await asyncio.wait_for(CLOSING.wait(), 2)
        first_task.cancel()
        await asyncio.sleep(0)
        first_task.cancel()
        await asyncio.sleep(0)
        assert not first_task.done()
        assert not any(pool.is_closing() for pool in POOLS)
        CLOSE_RELEASE.set()
        with pytest.raises(asyncio.CancelledError):
            await first_task
        assert request.session.stash[serving._SESSION_OWNER].close_task.done()
        assert all(pool.is_closing() for pool in POOLS)
    finally:
        RELEASE.set()
        CLOSE_RELEASE.set()
        finish.set()
        if not first_task.done():
            first_task.cancel()
        if borrowed is not None and not borrowed.done():
            borrowed.cancel()
        await asyncio.gather(first_task, *([] if borrowed is None else [borrowed]),
                             return_exceptions=True)
"""


def _child_group_exists(group: int) -> bool:
    try:
        os.killpg(group, 0)
    except ProcessLookupError:
        return False
    return True


def _stop_child(child: subprocess.Popen) -> bytes:
    """Terminate only the owned group; require child and descendant settlement."""
    output = b""
    for selected_signal in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(child.pid, selected_signal)
        except ProcessLookupError:
            pass
        try:
            output = child.communicate(timeout=5)[0]
        except subprocess.TimeoutExpired:
            continue
        deadline = time.monotonic() + 1
        while _child_group_exists(child.pid) and time.monotonic() < deadline:
            time.sleep(0.02)
        if not _child_group_exists(child.pid):
            return output
    pytest.fail("E2E lifetime child cleanup UNKNOWN; owned group did not join")


def _run_framework_child(directory: Path, checkout: Path) -> None:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(checkout) + os.pathsep + str(directory)
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    environment["PYTEST_ADDOPTS"] = ""
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-c",
        str(directory / "pytest.ini"),
        "--confcutdir",
        str(directory),
        "-p",
        "pytest_asyncio.plugin",
        "-p",
        "pytest_timeout",
        "--timeout=240",
        "-q",
        str(directory),
    ]
    child = subprocess.Popen(
        command,
        cwd=directory,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    timed_out = False
    try:
        try:
            output, _ = child.communicate(timeout=270)
        except subprocess.TimeoutExpired:
            timed_out = True
            output = _stop_child(child)
    finally:
        if child.poll() is None or _child_group_exists(child.pid):
            _stop_child(child)
    digest = hashlib.sha256(output).hexdigest()
    assert not timed_out, f"E2E lifetime child timed out; output SHA256={digest}"
    assert child.returncode == 0, f"E2E lifetime child failed; output SHA256={digest}"


@pytest.mark.parametrize(
    "scenario",
    ["success", "failure", "startup_cancel", "borrow_cancel", "close_cancel", "mismatch"],
)
def test_real_pytest_session_owner_and_disposable_purpose_lifetime(tmp_path, scenario):
    """Provider-free native lifetime is separate from full original live E2E outcomes."""
    checkout = Path(__file__).resolve().parents[2]
    (tmp_path / "pytest.ini").write_text(
        "[pytest]\nasyncio_mode=auto\nasyncio_default_fixture_loop_scope=session\n"
    )
    (tmp_path / "support.py").write_text(_SUPPORT.replace("__SCENARIO__", repr(scenario)))
    (tmp_path / "conftest.py").write_text(
        "import json\nfrom pathlib import Path\n"
        "from support import EVENTS, OBJECTS, opted_ecosystem, postgres_container\n"
        "def pytest_sessionfinish(session, exitstatus):\n"
        "    Path(__file__).with_name('lifetime.json').write_text(json.dumps({\n"
        "        'events':EVENTS,'objects':OBJECTS,'exitstatus':int(exitstatus)}))\n"
    )
    if scenario == "success":
        for index, label in ((1, "first"), (2, "later")):
            (tmp_path / f"test_0{index}_module.py").write_text(
                "import pytest\nfrom support import EVENTS, OBJECTS, opted_ecosystem\n"
                "pytestmark=pytest.mark.asyncio(loop_scope='session')\n"
                f"async def test_{label}(opted_ecosystem):\n"
                "    OBJECTS.append(id(opted_ecosystem))\n"
                f"    EVENTS.append('{label}_module')\n"
                "    async with opted_ecosystem.pools['general'].acquire() as connection:\n"
                "        assert await connection.fetchval('SELECT 1') == 1\n"
            )
    else:
        (tmp_path / "test_protocol.py").write_text(_MANUAL)
    _run_framework_child(tmp_path, checkout)
    report = json.loads((tmp_path / "lifetime.json").read_text())
    assert report["exitstatus"] == 0
    events = report["events"]
    assert events.count("setup") == 1
    assert events.count("physical_roles_admitted") == 1
    assert events.count("bootstrap_closed") == 1
    assert events.count("container_closed") == 1
    assert events.index("bootstrap_closed") < events.index("container_closed")
    if scenario == "success":
        assert events.count("actual_input_removal_restoration") == 1
        assert len(report["objects"]) == 2
        assert report["objects"][0] == report["objects"][1]
        assert events.index("first_module") < events.index("later_module")
        assert events.index("later_module") < events.index("bootstrap_closed")
