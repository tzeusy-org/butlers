"""The shared ``booted_daemon`` kit: a real daemon boot with no LLM (bu-rvgzmf.2 S7a)."""

from __future__ import annotations

import asyncio
import logging
import statistics
import time
import warnings

import pytest

from butlers.core.model_capabilities import capability_baseline_of_adapter
from butlers.core.runtimes import create_adapter, get_adapter, list_registered_runtime_types
from butlers.testing.daemon import DaemonBooter, FakeRuntime, write_butler_config

pytestmark = pytest.mark.integration

# Async fixtures run on the session loop (asyncio_default_fixture_loop_scope), so tests
# that use them must too.
session_loop = pytest.mark.asyncio(loop_scope="session")

_BOOTS = 10


def test_write_butler_config_writes_a_bootable_one_db_butler(tmp_path) -> None:
    from butlers.config import load_config

    config_dir = write_butler_config(
        tmp_path,
        "general",
        extra_toml='[butler.security]\ntrusted_route_callers = ["switchboard", "health"]\n',
    )

    assert config_dir == tmp_path / "general"
    config = load_config(config_dir)
    assert (config.name, config.db_name, config.db_schema) == ("general", "butlers", "general")
    assert set(config.trusted_route_callers) == {"switchboard", "health"}


def _live_tasks() -> set[asyncio.Task]:
    return {task for task in asyncio.all_tasks() if not task.done()}


@session_loop
async def test_booted_daemon_serves_a_tool_and_shuts_down_clean(booted_daemon, tmp_path) -> None:
    before = _live_tasks()
    booted = await booted_daemon(write_butler_config(tmp_path, "kit_smoke"))

    # The real spawner holds the fake adapter, so no LLM CLI can start.
    assert isinstance(booted.daemon.spawner._runtime, FakeRuntime)
    status = await booted.call_tool("status")
    assert status["name"] == "kit_smoke"
    assert status["health"] == "ok"

    await booted.shutdown()
    assert booted.daemon.db.pool is None
    # Let cancelled tasks finish unwinding before counting what is left.
    await asyncio.sleep(0)
    leaked = _live_tasks() - before - {asyncio.current_task()}
    assert not leaked, sorted(task.get_name() for task in leaked)


@session_loop
async def test_caplog_still_captures_daemon_logs_after_boot(
    booted_daemon, tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    # Startup's configure_logging replaces the root logger's console handlers, which
    # would silently drop pytest's capture handler: every "not in caplog.text" check
    # on a booted daemon would then pass vacuously.
    booted = await booted_daemon(write_butler_config(tmp_path, "general"))

    with caplog.at_level("INFO"):
        logging.getLogger("butlers.core_tools._routing").info("kit capture probe")
        await booted.call_tool("status")

    assert "kit capture probe" in caplog.text


@session_loop
async def test_admin_url_reaches_the_daemons_clone_as_its_owner(booted_daemon, tmp_path) -> None:
    # Fixtures the butler role may not write (dashboard turns, rows that must exist
    # before a recovery sweep) are seeded through the clone's owner connection.
    from butlers.testing.db import open_pool

    booted = await booted_daemon(write_butler_config(tmp_path, "general"))

    pool = await open_pool(booted.admin_url)
    try:
        database, owns_it = await pool.fetchrow(
            "SELECT datname, pg_get_userbyid(datdba) = current_user FROM pg_database "
            "WHERE datname = current_database()"
        )
    finally:
        await pool.close()
    assert database == booted.daemon.db.db_name
    assert owns_it is True


@session_loop
async def test_seeded_credentials_let_credentialed_modules_start(booted_daemon, tmp_path) -> None:
    # Module credentials resolve from the butler's secret store only (no env
    # fallback), so they are seeded on the clone before the daemon starts.
    # Channel egress modules belong to the Messenger only.
    config_dir = write_butler_config(
        tmp_path, "messenger", schema="messenger", extra_toml="[modules.telegram]\n"
    )

    booted = await booted_daemon(
        config_dir, credentials={"BUTLER_TELEGRAM_TOKEN": "synthetic-test-token"}
    )

    assert booted.daemon._module_statuses["telegram"].status == "active"


@session_loop
async def test_a_later_boot_does_not_log_into_an_earlier_daemon(
    booted_daemon, tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    # Boot leaves the first daemon's butler context on the test task. A later boot's
    # template build runs migrations on its own loop in a worker thread; if that
    # thread inherits the context, the first daemon's DB log handler tries to write
    # every migration line through a pool on another loop (bu-rvgzmf.12).
    await booted_daemon(write_butler_config(tmp_path, "general"))

    with caplog.at_level("WARNING", logger="butlers.core.butler_logging"):
        await booted_daemon(write_butler_config(tmp_path / "second", "general"), registered=True)

    assert "failed to write log line" not in caplog.text


@session_loop
async def test_unregistered_boot_serves_tools_but_does_not_accept_routes(
    booted_daemon, tmp_path
) -> None:
    booted = await booted_daemon(write_butler_config(tmp_path, "general"))

    assert booted.daemon._identity_facts()["accepting_routes"] is False


@session_loop
async def test_registered_boot_accepts_routes(booted_daemon, tmp_path) -> None:
    # Boot registration is the role-bound public.register_butler_boot, so the butler
    # name must match the schema's runtime role (butler_general_rw).
    booted = await booted_daemon(write_butler_config(tmp_path, "general"), registered=True)

    facts = booted.daemon._identity_facts()
    assert facts["accepting_routes"] is True
    assert facts["boot_epoch"] >= 1
    assert booted.daemon._boot_registration_task is None


@session_loop
async def test_scripted_reply_comes_back_through_the_real_spawner(booted_daemon, tmp_path) -> None:
    booted = await booted_daemon(write_butler_config(tmp_path, "general"))
    booted.runtime.script("scripted answer")

    result = await booted.daemon.spawner.trigger(
        prompt="say something", trigger_source="trigger", context="CONTEXT LINE"
    )

    assert result.success, result.error
    assert result.output == "scripted answer"
    (invocation,) = booted.runtime.invocations
    assert invocation["prompt"].startswith("CONTEXT LINE\n\n")
    assert invocation["prompt"].endswith("say something")


@session_loop
async def test_every_runtime_type_is_the_fake_until_close(
    postgres_container, monkeypatch, tmp_path
) -> None:
    # The spawner builds a per-model adapter for whatever runtime type the catalog
    # resolves, so faking only the default type would let a real CLI start.
    production = {name: get_adapter(name) for name in list_registered_runtime_types()}
    booter = DaemonBooter(postgres_container, monkeypatch, tmp_path)
    try:
        for name, adapter in production.items():
            assert create_adapter(name) is booter.runtime, name
            # Model resolution reads capabilities off the class, so the fake must
            # declare what production does or resolution picks different models.
            assert capability_baseline_of_adapter(
                get_adapter(name)
            ) == capability_baseline_of_adapter(adapter), name
    finally:
        await booter.close()

    assert {name: get_adapter(name) for name in list_registered_runtime_types()} == production


async def _invoke(runtime: FakeRuntime, prompt: str) -> str | None:
    output, _, _ = await runtime.invoke(prompt, "system", {}, {})
    return output


async def test_fake_runtime_plays_its_script_in_order_then_the_default() -> None:
    runtime = FakeRuntime()
    runtime.script("first", RuntimeError("runtime down"), "third")

    assert await _invoke(runtime, "a") == "first"
    with pytest.raises(RuntimeError, match="runtime down"):
        await _invoke(runtime, "b")
    assert await _invoke(runtime, "c") == "third"
    assert await _invoke(runtime, "d") == FakeRuntime.reply

    assert [call["prompt"] for call in runtime.invocations] == ["a", "b", "c", "d"]
    assert runtime.invocations[0]["system_prompt"] == "system"


async def test_fake_runtime_reply_can_depend_on_the_invocation() -> None:
    runtime = FakeRuntime()
    runtime.script(lambda call: call["prompt"].upper())

    assert await _invoke(runtime, "echo") == "ECHO"


async def test_fake_runtime_without_resume_reports_no_provider_session() -> None:
    runtime = FakeRuntime()

    await _invoke(runtime, "turn")

    assert runtime.supports_resume is False
    assert runtime.last_process_info is None
    assert runtime.invocations[0]["resume_session_id"] is None


async def test_resumable_fake_runtime_mints_a_session_per_turn_and_records_resumes() -> None:
    # Like the Claude adapter: each successful turn reports a fresh provider session
    # id, which the spawner stores as the handle the next turn resumes from.
    runtime = FakeRuntime()
    runtime.supports_resume = True

    await _invoke(runtime, "first")
    first = runtime.last_process_info
    await runtime.invoke("second", "system", {}, {}, resume_session_id="fake-session-1")

    assert first == {"provider_session_id": "fake-session-1"}
    assert runtime.last_process_info == {"provider_session_id": "fake-session-2"}
    assert [call["resume_session_id"] for call in runtime.invocations] == [
        None,
        "fake-session-1",
    ]


async def test_wait_for_invocations_returns_once_enough_have_happened() -> None:
    runtime = FakeRuntime()

    waiter = asyncio.create_task(runtime.wait_for_invocations(2))
    await _invoke(runtime, "one")
    await asyncio.sleep(0)
    assert not waiter.done()
    await _invoke(runtime, "two")

    calls = await asyncio.wait_for(waiter, timeout=1)
    assert [call["prompt"] for call in calls] == ["one", "two"]


async def test_wait_for_invocations_times_out_loudly() -> None:
    runtime = FakeRuntime()

    with pytest.raises(TimeoutError, match="1 invocation"):
        await runtime.wait_for_invocations(1, timeout=0.01)


@session_loop
async def test_boot_cost_is_reported(booted_daemon, tmp_path) -> None:
    await (await booted_daemon(write_butler_config(tmp_path, "warm"))).shutdown()
    cycles: list[float] = []
    starts: list[float] = []
    for index in range(_BOOTS):
        began = time.monotonic()
        booted = await booted_daemon(write_butler_config(tmp_path, f"boot_{index}"))
        starts.append(booted.start_seconds)
        await booted.call_tool("status")
        await booted.shutdown()
        cycles.append(time.monotonic() - began)
    # junit properties are lost under xdist and passing output is captured; a warning
    # reaches the CI log's warnings summary. A cycle is clone + start + call + shutdown.
    warnings.warn(
        f"DAEMON-BOOT-SECONDS p50={statistics.median(cycles):.3f} "
        f"start_p50={statistics.median(starts):.3f} max={max(cycles):.3f} n={_BOOTS}",
        UserWarning,
        stacklevel=1,
    )
    assert statistics.median(cycles) < 30
