"""Owner-scoped dispatch tests for core memory session hooks."""

from __future__ import annotations

import pytest

import butlers.core.memory_hooks as memory_hooks


@pytest.mark.asyncio
async def test_session_hooks_route_by_invoking_owner_and_fail_safe_when_absent() -> None:
    """Context and episode storage never borrow another butler's runtime."""
    calls: list[tuple[str, str, object]] = []
    general_pool = object()
    travel_pool = object()

    async def general_context(pool, butler_name, prompt, *, token_budget=3000):
        calls.append(("context:general", butler_name, pool))
        return "general context"

    async def travel_context(pool, butler_name, prompt, *, token_budget=3000):
        calls.append(("context:travel", butler_name, pool))
        return "travel context"

    async def general_store(pool, butler_name, session_output, session_id=None):
        calls.append(("store:general", butler_name, pool))
        return True

    async def travel_store(pool, butler_name, session_output, session_id=None):
        calls.append(("store:travel", butler_name, pool))
        return True

    general_runtime = memory_hooks.register_memory_session_runtime(
        "general",
        context=general_context,
        store_episode=general_store,
    )
    travel_runtime = memory_hooks.register_memory_session_runtime(
        "travel",
        context=travel_context,
        store_episode=travel_store,
    )
    try:
        assert (
            await memory_hooks.fetch_memory_context(general_pool, "general", "general prompt")
            == "general context"
        )
        assert (
            await memory_hooks.fetch_memory_context(travel_pool, "travel", "travel prompt")
            == "travel context"
        )
        assert (
            await memory_hooks.store_session_episode(
                general_pool,
                "general",
                "general session output",
            )
            is True
        )
        assert (
            await memory_hooks.store_session_episode(
                travel_pool,
                "travel",
                "travel session output",
            )
            is True
        )

        assert await memory_hooks.fetch_memory_context(object(), "unknown", "prompt") is None
        assert await memory_hooks.store_session_episode(object(), "unknown", "output") is False
    finally:
        memory_hooks.unregister_memory_session_runtime("general", general_runtime)
        memory_hooks.unregister_memory_session_runtime("travel", travel_runtime)

    assert calls == [
        ("context:general", "general", general_pool),
        ("context:travel", "travel", travel_pool),
        ("store:general", "general", general_pool),
        ("store:travel", "travel", travel_pool),
    ]


@pytest.mark.asyncio
async def test_stale_runtime_unregister_keeps_replacement_for_same_owner() -> None:
    """Older shutdown cannot remove a newer runtime registration."""
    old_pool = object()
    replacement_pool = object()

    async def old_context(pool, butler_name, prompt, *, token_budget=3000):
        return "old context"

    async def old_store(pool, butler_name, session_output, session_id=None):
        return True

    async def replacement_context(pool, butler_name, prompt, *, token_budget=3000):
        return "replacement context"

    async def replacement_store(pool, butler_name, session_output, session_id=None):
        return True

    old_runtime = memory_hooks.register_memory_session_runtime(
        "general",
        context=old_context,
        store_episode=old_store,
    )
    replacement_runtime = memory_hooks.register_memory_session_runtime(
        "general",
        context=replacement_context,
        store_episode=replacement_store,
    )
    try:
        memory_hooks.unregister_memory_session_runtime("general", old_runtime)

        assert (
            await memory_hooks.fetch_memory_context(replacement_pool, "general", "prompt")
            == "replacement context"
        )
    finally:
        memory_hooks.unregister_memory_session_runtime("general", replacement_runtime)

    assert await memory_hooks.fetch_memory_context(old_pool, "general", "prompt") is None
