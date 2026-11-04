"""Boot a real butler daemon for tests, with no LLM (bu-rvgzmf.2 S7a).

:class:`DaemonBooter` runs the production startup sequence end to end:
``ButlerDaemon.start()`` with the real ``ModuleRegistry``, real FastMCP served over
real HTTP, and the real ``Spawner``. Only the runtime adapter is fake: a
:class:`FakeRuntime` is registered as the default runtime for the boot, so the
spawner can never start an LLM CLI.

It patches no lifecycle internals. Every input goes through a public seam:

- the database is a fresh template clone from the shared factory
  (:func:`butlers.testing.db.migrated_url`), injected as an already-connected
  ``Database`` (``ButlerDaemon(db=...)``), so provisioning is skipped;
- the config is loaded with ``load_config`` and pre-set on the daemon (as the
  e2e ecosystem does), with a free port, the clone's database name and no
  Switchboard;
- the runtime adapter goes through ``register_adapter``;
- the environment selects test-safe production behaviour: file logs under a
  temp root, and no MCP warmup (an untracked fire-and-forget task that would
  outlive shutdown).

The root ``conftest.py`` exposes it as the ``booted_daemon`` fixture::

    booted = await booted_daemon(config_dir)
    status = await booted.call_tool("status")
    await booted.shutdown()   # optional; teardown shuts down whatever is left

The butler must use the one-db topology on a schema that has a runtime role
(``[butler.db] name = "butlers"``, ``schema = "general"``); every chain migrates
into that schema and the pool runs under ``butler_<schema>_rw``, as in production.
Boot registration needs the Switchboard chain's ``public.register_butler_boot``, so
without it the daemon serves tools but ``_accepting_connections`` stays ``False``.
One boot costs a clone plus a full startup and shutdown;
``tests/testing/test_kit_booted_daemon.py`` reports it as ``DAEMON-BOOT-SECONDS``.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import socket
import sys
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest

# The package import registers every production adapter first, so none of them can
# re-register over the fake later in the boot.
from butlers.core.runtimes import (
    RuntimeAdapter,
    get_adapter,
    list_registered_runtime_types,
    register_adapter,
)

Reply = str | BaseException | Callable[[dict[str, Any]], str]


class FakeRuntime(RuntimeAdapter):
    """A runtime adapter that records each invocation and returns a scripted reply.

    ``binary_name`` is the running interpreter, so the spawner's PATH check passes
    without installing anything, and ``invoke`` never starts a process.

    Each invocation takes the next entry from :meth:`script`, then :attr:`reply`
    once the script runs out. An entry is the output text, an exception to raise
    (a runtime failure), or a callable that gets the recorded invocation and
    returns the text.

    Set ``supports_resume = True`` to act as a resume-capable adapter (Claude's):
    each turn then reports a fresh ``provider_session_id`` (``fake-session-<n>``)
    in :attr:`last_process_info`, and invocations record the ``resume_session_id``
    the spawner passed.
    """

    reply: str = "ok"

    def __init__(self, **_: Any) -> None:
        self.invocations: list[dict[str, Any]] = []
        self._last_process_info: dict[str, Any] | None = None
        self._script: deque[Reply] = deque()
        self._invoked = asyncio.Condition()

    def script(self, *replies: Reply) -> None:
        """Queue *replies* for the next invocations, in order."""
        self._script.extend(replies)

    async def wait_for_invocations(
        self,
        count: int,
        timeout: float = 10.0,  # noqa: ASYNC109 - a bounded wait is the point
    ) -> list[dict[str, Any]]:
        """Wait until *count* invocations have happened, e.g. from a background route."""
        try:
            async with asyncio.timeout(timeout):
                async with self._invoked:
                    await self._invoked.wait_for(lambda: len(self.invocations) >= count)
        except TimeoutError:
            raise TimeoutError(
                f"expected {count} invocation(s) within {timeout}s, got {len(self.invocations)}"
            ) from None
        return list(self.invocations)

    @property
    def binary_name(self) -> str:
        return sys.executable

    @property
    def last_process_info(self) -> dict[str, Any] | None:
        return self._last_process_info

    async def invoke(
        self,
        prompt: str,
        system_prompt: str,
        mcp_servers: dict[str, Any],
        env: dict[str, str],
        max_turns: int = 20,
        model: str | None = None,
        runtime_args: list[str] | None = None,
        cwd: Path | None = None,
        timeout: int | None = None,  # noqa: ASYNC109 - RuntimeAdapter signature
        resume_session_id: str | None = None,
    ) -> tuple[str | None, list[dict[str, Any]], dict[str, Any] | None]:
        invocation = {
            "prompt": prompt,
            "system_prompt": system_prompt,
            "model": model,
            "mcp_servers": mcp_servers,
            "resume_session_id": resume_session_id,
        }
        async with self._invoked:
            self.invocations.append(invocation)
            self._invoked.notify_all()
        self._last_process_info = (
            {"provider_session_id": f"fake-session-{len(self.invocations)}"}
            if self.supports_resume
            else None
        )
        reply = self._script.popleft() if self._script else self.reply
        if isinstance(reply, BaseException):
            raise reply
        output = reply(invocation) if callable(reply) else reply
        return output, [], {"input_tokens": 0, "output_tokens": 0}

    def build_config_file(self, mcp_servers: dict[str, Any], tmp_dir: Path) -> Path:
        path = tmp_dir / "mcp.json"
        path.write_text(json.dumps({"mcpServers": mcp_servers}))
        return path

    def parse_system_prompt_file(self, config_dir: Path) -> str:
        path = config_dir / "CLAUDE.md"
        return path.read_text() if path.exists() else ""


def write_butler_config(
    root: Path,
    name: str = "general",
    *,
    schema: str = "general",
    description: str = "booted_daemon test butler",
    extra_toml: str = "",
) -> Path:
    """Write ``root/name/butler.toml`` for a one-db butler the kit can boot.

    *schema* must have a runtime role (``butler_<schema>_rw``); for
    ``registered=True`` boots, *name* must equal it. *extra_toml* is appended as is,
    for example ``[modules.x]`` or ``[butler.security]`` sections.
    """
    config_dir = root / name
    config_dir.mkdir(parents=True)
    (config_dir / "butler.toml").write_text(
        f'[butler]\nname = "{name}"\nport = 0\ndescription = "{description}"\n\n'
        f'[butler.db]\nname = "butlers"\nschema = "{schema}"\n\n{extra_toml}'
    )
    return config_dir


def _link_roster(roster: Path, config_dir: Path) -> None:
    roster.mkdir()
    (roster / config_dir.name).symlink_to(config_dir.resolve(), target_is_directory=True)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class _LoggingSnapshot:
    """Startup's ``configure_logging`` replaces the root logger's handlers process-wide.

    Restoring them after shutdown keeps pytest's capture handlers and levels intact
    for later tests in the same worker.
    """

    def __init__(self) -> None:
        from butlers.core.logging import _NOISE_LOGGERS

        root = logging.getLogger()
        self._root = (list(root.handlers), list(root.filters), root.level)
        self._noise = {
            name: (list(logging.getLogger(name).handlers), logging.getLogger(name).level)
            for name in _NOISE_LOGGERS
        }

    def reattach(self) -> None:
        """Put back the pre-boot root handlers that startup's ``configure_logging`` dropped.

        It closes and removes every non-file console handler, pytest's capture
        handlers included; without them ``caplog`` records nothing while the daemon
        runs, and a "not in caplog.text" check passes vacuously. The daemon's own
        handlers stay.
        """
        root = logging.getLogger()
        for handler in self._root[0]:
            if handler not in root.handlers:
                root.addHandler(handler)

    def restore(self) -> None:
        root = logging.getLogger()
        handlers, filters, level = self._root
        for handler in root.handlers:
            if handler not in handlers:
                handler.close()
        root.handlers, root.filters = handlers, filters
        root.setLevel(level)
        for name, (noise_handlers, noise_level) in self._noise.items():
            noise = logging.getLogger(name)
            for handler in noise.handlers:
                if handler not in noise_handlers:
                    handler.close()
            noise.handlers = noise_handlers
            noise.setLevel(noise_level)


async def _rearm_sse() -> None:
    """Re-arm sse_starlette for the next server in this process.

    Its per-loop shutdown watcher binds the uvicorn server running at the first SSE
    response and, once that server stops, sets the process-wide
    ``AppStatus.should_exit``; every later SSE response then ends at once, so a later
    boot's MCP calls never get a reply. A production process stops its server only
    at exit, so only a test that boots twice needs this. Cancelling the watcher lets
    the next boot's first SSE response start a fresh one bound to its own server.
    """
    from sse_starlette.sse import AppStatus

    watchers = [
        task
        for task in asyncio.all_tasks()
        if not task.done() and getattr(task.get_coro(), "__qualname__", None) == "_shutdown_watcher"
    ]
    for task in watchers:
        task.cancel()
    await asyncio.gather(*watchers, return_exceptions=True)
    AppStatus.should_exit = False


class BootedDaemon:
    """One started daemon: its ``ButlerDaemon``, MCP URL and fake runtime."""

    def __init__(self, daemon: Any, url: str, start_seconds: float, logs: _LoggingSnapshot):
        self.daemon = daemon
        self.url = url
        self.start_seconds = start_seconds
        self._logs = logs
        self._stopped = False

    @property
    def runtime(self) -> FakeRuntime:
        return self.daemon.spawner._runtime

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        """Call *name* over the daemon's real streamable-HTTP MCP endpoint."""
        from fastmcp import Client

        async with Client(self.url) as client:
            result = await client.call_tool(name, arguments or {})
        return result.data if result.data is not None else result.structured_content

    async def shutdown(self) -> None:
        """Run the production shutdown once, then restore process-wide logging."""
        if self._stopped:
            return
        self._stopped = True
        try:
            await self.daemon.shutdown()
        finally:
            self._logs.restore()
            await _rearm_sse()


def _stand_in(fake: FakeRuntime, production: type[RuntimeAdapter]) -> type[RuntimeAdapter]:
    """An adapter class that answers capability lookups as *production* and is *fake*.

    The spawner builds its default adapter at boot and a per-model adapter for
    whichever runtime type the catalog resolves at trigger time, each through the
    registry; every instantiation returns the one *fake*, so every invocation lands
    where the test looks. Model resolution reads the class's declared capabilities
    (and caches them per runtime type), so the stand-in declares exactly what the
    production adapter does and resolution picks the models production would.
    ``fake`` is not an instance of the stand-in, so Python does not re-run
    ``__init__`` on it.
    """

    def __new__(cls: type, *_args: Any, **_kwargs: Any) -> Any:
        return fake

    return type(
        f"FakeRuntimeAs{production.__name__}",
        (FakeRuntime,),
        {
            "__new__": __new__,
            "declared_capabilities": getattr(production, "declared_capabilities", {}),
            "supports_resume": getattr(production, "supports_resume", False),
        },
    )


class DaemonBooter:
    """Boots daemons on fresh clones for one test and shuts down what is left.

    From construction until :meth:`close`, every registered runtime type resolves
    to one :class:`FakeRuntime` (:attr:`runtime`), shared by the daemons it boots.
    """

    def __init__(
        self,
        postgres_container: Any,
        monkeypatch: pytest.MonkeyPatch,
        tmp_root: Path,
    ) -> None:
        self._container = postgres_container
        self._monkeypatch = monkeypatch
        self._tmp_root = tmp_root
        self._booted: list[BootedDaemon] = []
        self.runtime = FakeRuntime()
        self._production_adapters = {
            name: get_adapter(name) for name in list_registered_runtime_types()
        }
        monkeypatch.setenv("BUTLERS_LOG_ROOT", str(tmp_root / "logs"))
        monkeypatch.setenv("BUTLERS_MCP_WARMUP_DISABLED", "1")
        # Shared credentials then come from the clone's ``public`` under the butler's role.
        monkeypatch.delenv("BUTLER_SHARED_DB_NAME", raising=False)
        for name, production in self._production_adapters.items():
            register_adapter(name, _stand_in(self.runtime, production))

    async def boot(
        self,
        config_dir: Path,
        *chains: str,
        registry: Any = None,
        registered: bool = False,
    ) -> BootedDaemon:
        """Start the butler in *config_dir* on a fresh clone of *chains* (default ``core``).

        ``registered=True`` also migrates the Switchboard chain into its own schema and
        seeds this butler's roster row, as the Switchboard does in production, so boot
        registration commits an epoch and the daemon accepts routes. The butler's name
        must then match its schema's runtime role (``butler_<name>_rw``).
        """
        from butlers.config import load_config
        from butlers.daemon import ButlerDaemon
        from butlers.db import Database
        from butlers.testing.db import migrated_url

        config = load_config(config_dir)
        schema = config.db_schema
        if schema is None:
            raise ValueError(
                f'{config_dir}/butler.toml needs [butler.db] name = "butlers" and a schema '
                'with a runtime role (for example schema = "general")'
            )
        chains = chains or ("core",)
        schemas = dict.fromkeys(chains, schema)
        if registered:
            schemas["switchboard"] = "switchboard"
        url = await asyncio.to_thread(migrated_url, self._container, *schemas, schemas=schemas)
        db_name = urlparse(url).path.lstrip("/")
        if registered:
            await self._seed_roster_row(url, config_dir)
        config.port = _free_port()
        config.db_name = db_name
        config.switchboard_url = None

        db = Database(
            db_name=db_name,
            schema=schema,
            role=f"butler_{schema}_rw",
            host=self._container.get_container_host_ip(),
            port=int(self._container.get_exposed_port(5432)),
            user=self._container.username,
            password=self._container.password,
            min_pool_size=1,
            max_pool_size=5,
        )
        await db.connect()
        daemon = ButlerDaemon(config_dir, registry, db=db)
        daemon.config = config

        logs = _LoggingSnapshot()
        began = time.monotonic()
        try:
            await daemon.start()
        except BaseException:
            try:
                await daemon.shutdown()
            finally:
                logs.restore()
            raise
        logs.reattach()
        booted = BootedDaemon(
            daemon,
            f"http://127.0.0.1:{config.port}/mcp",
            time.monotonic() - began,
            logs,
        )
        self._booted.append(booted)
        return booted

    async def _seed_roster_row(self, url: str, config_dir: Path) -> None:
        """Insert this butler's roster row with the Switchboard's own seeding.

        The seed scans a roster directory, so it gets one holding only a link to
        *config_dir*; other butlers beside it in the test's tmp tree stay out.
        """
        from butlers.testing.db import open_pool

        # butlers.tools.* is the roster alias installed at import time, which static
        # analysis cannot follow.
        registry = importlib.import_module("butlers.tools.switchboard.registry.registry")
        roster = self._tmp_root / f"roster-{config_dir.name}-{_free_port()}"
        await asyncio.to_thread(_link_roster, roster, config_dir)
        pool = await open_pool(url)
        try:
            await registry.seed_missing_roster_butlers(pool, roster)
        finally:
            await pool.close()

    async def close(self) -> None:
        """Shut down every daemon still running, newest first, and restore the adapter."""
        try:
            for booted in reversed(self._booted):
                await booted.shutdown()
        finally:
            for name, adapter in self._production_adapters.items():
                register_adapter(name, adapter)
