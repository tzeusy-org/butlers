"""Tests for DiscretionDispatcher — semaphore-gated adapter dispatcher.

Covers:
- Model resolution from shared.model_catalog (mock pool)
- RuntimeError on missing catalog entry
- Semaphore concurrency limit
- Timeout enforcement via asyncio.wait_for
- Adapter is invoked with mcp_servers={}, env={}, max_turns=1
- Own adapter cache (lazy instantiation via get_adapter registry)
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.connectors.discretion_dispatcher import DiscretionDispatcher
from butlers.core.runtimes.base import RuntimeAdapter, register_adapter

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _mock_pool(
    *, runtime_type: str = "claude", model_id: str = "claude-haiku", extra_args: list | None = None
):
    """Return an asyncpg pool mock whose fetchrow returns a matching catalog row."""
    # asyncpg returns JSONB columns as strings; simulate that behaviour here.
    extra_args_json = json.dumps(extra_args) if extra_args is not None else None
    row = MagicMock()
    row.__getitem__ = lambda self, key: {
        "runtime_type": runtime_type,
        "model_id": model_id,
        "extra_args": extra_args_json,
    }[key]
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=row)
    return pool


def _mock_pool_empty():
    """Return a pool mock that returns no matching catalog row (None)."""
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)
    return pool


class _StubAdapter(RuntimeAdapter):
    """Minimal concrete adapter for testing; returns a configurable string."""

    def __init__(self, response: str = "ok", *, butler_name: str | None = None) -> None:
        self._response = response
        self._calls: list[dict[str, Any]] = []

    @property
    def binary_name(self) -> str:
        return "stub-binary"

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
        timeout: int | None = None,
    ) -> tuple[str | None, list[dict[str, Any]], dict[str, Any] | None]:
        self._calls.append(
            {
                "prompt": prompt,
                "system_prompt": system_prompt,
                "mcp_servers": mcp_servers,
                "env": env,
                "max_turns": max_turns,
                "model": model,
                "runtime_args": runtime_args,
            }
        )
        return (self._response, [], None)

    def build_config_file(self, mcp_servers: dict[str, Any], tmp_dir: Path) -> Path:
        return tmp_dir / "config.json"

    def parse_system_prompt_file(self, config_dir: Path) -> str:
        return ""


class _SlowAdapter(RuntimeAdapter):
    """Adapter that sleeps longer than any test timeout to trigger TimeoutError."""

    @property
    def binary_name(self) -> str:
        return "slow-binary"

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
        timeout: int | None = None,
    ) -> tuple[str | None, list[dict[str, Any]], dict[str, Any] | None]:
        await asyncio.sleep(999)
        return (None, [], None)  # pragma: no cover

    def build_config_file(self, mcp_servers: dict[str, Any], tmp_dir: Path) -> Path:
        return tmp_dir / "config.json"

    def parse_system_prompt_file(self, config_dir: Path) -> str:
        return ""


# ---------------------------------------------------------------------------
# Model resolution from catalog
# ---------------------------------------------------------------------------


async def test_call_resolves_model_from_catalog() -> None:
    """call() queries the catalog and uses the resolved model_id."""
    stub = _StubAdapter(response="discretion-answer")
    pool = _mock_pool(runtime_type="stub-discretion", model_id="tiny-model")

    register_adapter("stub-discretion", type(stub))
    # Inject instance into cache to avoid constructor lookup
    dispatcher = DiscretionDispatcher(pool=pool)
    dispatcher._adapter_cache["stub-discretion"] = stub

    result = await dispatcher.call("Is this spam?")
    assert result == "discretion-answer"
    assert stub._calls[0]["model"] == "tiny-model"


async def test_call_passes_prompt_and_system_prompt() -> None:
    """call() forwards prompt and system_prompt to adapter.invoke()."""
    stub = _StubAdapter(response="yes")
    pool = _mock_pool(runtime_type="stub-pass", model_id="m1")
    register_adapter("stub-pass", type(stub))

    dispatcher = DiscretionDispatcher(pool=pool)
    dispatcher._adapter_cache["stub-pass"] = stub

    await dispatcher.call("Hello", system_prompt="You are a judge.")
    assert stub._calls[0]["prompt"] == "Hello"
    assert stub._calls[0]["system_prompt"] == "You are a judge."


# ---------------------------------------------------------------------------
# RuntimeError on missing catalog entry
# ---------------------------------------------------------------------------


async def test_call_raises_runtime_error_when_no_catalog_entry() -> None:
    """call() raises RuntimeError when catalog has no discretion-tier entry."""
    pool = _mock_pool_empty()
    dispatcher = DiscretionDispatcher(pool=pool)

    with pytest.raises(RuntimeError, match="No discretion model configured"):
        await dispatcher.call("test prompt")


# ---------------------------------------------------------------------------
# Single-turn, no-tools invocation contract
# ---------------------------------------------------------------------------


async def test_call_invokes_with_empty_mcp_servers_and_env() -> None:
    """Adapter is always invoked with mcp_servers={} and env={}."""
    stub = _StubAdapter()
    pool = _mock_pool(runtime_type="stub-empty", model_id="m")
    register_adapter("stub-empty", type(stub))

    dispatcher = DiscretionDispatcher(pool=pool)
    dispatcher._adapter_cache["stub-empty"] = stub

    await dispatcher.call("test")
    assert stub._calls[0]["mcp_servers"] == {}
    assert stub._calls[0]["env"] == {}


async def test_call_invokes_with_max_turns_one() -> None:
    """Adapter is always invoked with max_turns=1."""
    stub = _StubAdapter()
    pool = _mock_pool(runtime_type="stub-turns", model_id="m")
    register_adapter("stub-turns", type(stub))

    dispatcher = DiscretionDispatcher(pool=pool)
    dispatcher._adapter_cache["stub-turns"] = stub

    await dispatcher.call("test")
    assert stub._calls[0]["max_turns"] == 1


# ---------------------------------------------------------------------------
# Adapter None result
# ---------------------------------------------------------------------------


async def test_call_returns_empty_string_for_none_result() -> None:
    """call() returns '' when the adapter returns None as result_text."""

    class NoneResultAdapter(_StubAdapter):
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
            timeout: int | None = None,
        ) -> tuple[str | None, list[dict[str, Any]], dict[str, Any] | None]:
            return (None, [], None)

    stub = NoneResultAdapter()
    pool = _mock_pool(runtime_type="stub-none", model_id="m")
    register_adapter("stub-none", type(stub))

    dispatcher = DiscretionDispatcher(pool=pool)
    dispatcher._adapter_cache["stub-none"] = stub

    result = await dispatcher.call("anything")
    assert result == ""


# ---------------------------------------------------------------------------
# Semaphore concurrency limit
# ---------------------------------------------------------------------------


async def test_semaphore_limits_concurrent_calls() -> None:
    """Only max_concurrent calls run at the same time."""
    max_concurrent = 2
    # Track how many calls are in-flight simultaneously
    in_flight: list[int] = []
    peak: list[int] = [0]
    gate = asyncio.Event()

    class CountingAdapter(_StubAdapter):
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
            timeout: int | None = None,
        ) -> tuple[str | None, list[dict[str, Any]], dict[str, Any] | None]:
            in_flight.append(1)
            peak[0] = max(peak[0], len(in_flight))
            await gate.wait()
            in_flight.pop()
            return ("ok", [], None)

    stub = CountingAdapter()
    pool = _mock_pool(runtime_type="stub-counting", model_id="m")
    register_adapter("stub-counting", type(stub))

    dispatcher = DiscretionDispatcher(pool=pool, max_concurrent=max_concurrent, timeout_s=10.0)
    dispatcher._adapter_cache["stub-counting"] = stub

    # Launch more tasks than max_concurrent
    tasks = [asyncio.create_task(dispatcher.call("p")) for _ in range(max_concurrent + 2)]

    # Let the first batch start
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    # Verify that no more than max_concurrent are in-flight
    assert len(in_flight) <= max_concurrent

    gate.set()
    await asyncio.gather(*tasks)

    # At most max_concurrent were ever running simultaneously
    assert peak[0] <= max_concurrent


# ---------------------------------------------------------------------------
# Timeout enforcement
# ---------------------------------------------------------------------------


async def test_call_raises_timeout_error_on_slow_adapter() -> None:
    """call() raises asyncio.TimeoutError when the adapter exceeds timeout_s."""
    pool = _mock_pool(runtime_type="stub-slow", model_id="m")
    register_adapter("stub-slow", _SlowAdapter)

    dispatcher = DiscretionDispatcher(pool=pool, timeout_s=0.01)
    dispatcher._adapter_cache["stub-slow"] = _SlowAdapter()

    with pytest.raises(asyncio.TimeoutError):
        await dispatcher.call("slow call")


# ---------------------------------------------------------------------------
# Lazy adapter instantiation
# ---------------------------------------------------------------------------


async def test_adapter_is_lazily_instantiated_and_cached() -> None:
    """_get_or_create_adapter lazily creates and caches adapter instances."""
    pool = _mock_pool(runtime_type="stub-cache", model_id="m")
    register_adapter("stub-cache", _StubAdapter)

    dispatcher = DiscretionDispatcher(pool=pool)

    # Cache should be empty before first call
    assert "stub-cache" not in dispatcher._adapter_cache

    await dispatcher.call("test")

    # Cache should contain the adapter after the call
    assert "stub-cache" in dispatcher._adapter_cache
    first_instance = dispatcher._adapter_cache["stub-cache"]

    # Second call reuses the cached instance
    await dispatcher.call("test2")
    assert dispatcher._adapter_cache["stub-cache"] is first_instance


async def test_get_or_create_adapter_raises_for_unknown_type() -> None:
    """_get_or_create_adapter raises ValueError for unregistered runtime types."""
    pool = _mock_pool(runtime_type="nonexistent-xyz", model_id="m")

    dispatcher = DiscretionDispatcher(pool=pool)

    with pytest.raises(ValueError, match="Unknown runtime type"):
        await dispatcher.call("test")


async def test_call_passes_extra_args_as_runtime_args() -> None:
    """call() forwards catalog extra_args to adapter.invoke as runtime_args."""
    stub = _StubAdapter(response="ok")
    pool = _mock_pool(runtime_type="stub-rta", model_id="m", extra_args=["--flag", "val"])
    register_adapter("stub-rta", type(stub))

    dispatcher = DiscretionDispatcher(pool=pool)
    dispatcher._adapter_cache["stub-rta"] = stub

    await dispatcher.call("test prompt")
    assert stub._calls[0]["runtime_args"] == ["--flag", "val"]


async def test_call_passes_none_runtime_args_when_extra_args_empty() -> None:
    """call() passes runtime_args=None when catalog extra_args is empty."""
    stub = _StubAdapter(response="ok")
    pool = _mock_pool(runtime_type="stub-rta-empty", model_id="m", extra_args=None)
    register_adapter("stub-rta-empty", type(stub))

    dispatcher = DiscretionDispatcher(pool=pool)
    dispatcher._adapter_cache["stub-rta-empty"] = stub

    await dispatcher.call("test prompt")
    assert stub._calls[0]["runtime_args"] is None
