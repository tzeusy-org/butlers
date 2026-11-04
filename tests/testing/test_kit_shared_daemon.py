"""The shared ``booted_daemon``: one boot per module for read-only tests (bu-rvgzmf.2).

Tests here run in file order on purpose: each pair checks what one test leaves for
the next.
"""

from __future__ import annotations

import pytest

from butlers.testing.daemon import SharedDaemonMutated

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

_seen: dict[str, object] = {}


async def test_read_only_tests_share_one_boot(shared_booted_daemon) -> None:
    booted = await shared_booted_daemon()
    status = await booted.call_tool("status")

    assert status["health"] == "ok"
    _seen["first"] = booted.daemon


async def test_the_next_read_only_test_gets_the_same_daemon(shared_booted_daemon) -> None:
    booted = await shared_booted_daemon()

    assert booted.daemon is _seen["first"]


async def test_differently_configured_requests_get_their_own_boot(shared_booted_daemon) -> None:
    plain = await shared_booted_daemon()
    registered = await shared_booted_daemon(registered=True)

    assert plain.daemon is _seen["first"]
    assert registered.daemon is not plain.daemon
    assert registered.daemon._identity_facts()["accepting_routes"] is True


async def test_runtime_state_left_by_one_test(shared_booted_daemon) -> None:
    booted = await shared_booted_daemon()
    # Things a test can leave on the shared fake runtime without touching the daemon.
    booted.runtime.script("left over")
    booted.runtime.supports_resume = True
    booted.runtime.invocations.append({"prompt": "left over"})
    _seen["runtime"] = booted.runtime


async def test_is_reset_before_the_next(shared_booted_daemon) -> None:
    booted = await shared_booted_daemon()

    assert booted.runtime is _seen["runtime"]
    assert booted.runtime.invocations == []
    assert booted.runtime.supports_resume is False
    output, _, _ = await booted.runtime.invoke("probe", "system", {}, {})
    assert output == booted.runtime.reply
    booted.runtime.invocations.clear()


async def test_durable_daemon_state_change_is_refused(shared_booted_daemon) -> None:
    from butlers.core.route_inbox import route_inbox_insert

    booted = await shared_booted_daemon(registered=True)
    await route_inbox_insert(
        booted.daemon.db.pool,
        route_envelope={"schema_version": "route.v1", "input": {"prompt": "x"}},
    )

    with pytest.raises(SharedDaemonMutated, match="route_inbox"):
        await shared_booted_daemon.verify()
    _seen["retired"] = booted.daemon


async def test_a_refused_boot_is_replaced_not_reused(shared_booted_daemon) -> None:
    booted = await shared_booted_daemon(registered=True)

    assert booted.daemon is not _seen["retired"]
    count = await booted.daemon.db.pool.fetchval("SELECT count(*) FROM route_inbox")
    assert count == 0
