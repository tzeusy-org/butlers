"""Strict configured pool calls for DB-free tests, never SQL dispatch or authority."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock

_METHODS = ("fetch", "fetchrow", "fetchval", "execute", "executemany")


class AsyncCtx:
    """One acquisition's async context; body errors and cancellation propagate."""

    def __init__(
        self, value: Any, *, on_exit: Callable[[Any], Awaitable[None]] | None = None
    ) -> None:
        self.value = value
        self.enter_count = 0
        self.exit_count = 0
        self._on_exit = on_exit

    async def __aenter__(self) -> Any:
        if self.enter_count:
            raise RuntimeError("AsyncCtx is one acquisition; create a fresh context")
        self.enter_count += 1
        return self.value

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        self.exit_count += 1
        if self._on_exit is not None:
            await self._on_exit(self.value)
        return False


class FakeConnection:
    """Configured async call mocks and a fresh transaction context per call."""

    def __init__(self, **configured: Any) -> None:
        unknown = configured.keys() - _METHODS
        if unknown:
            raise TypeError(f"fake_pool: unknown methods: {sorted(unknown)}")
        for method in _METHODS:
            if method not in configured:
                mock = AsyncMock(side_effect=AssertionError(f"fake_pool: unexpected {method}"))
            else:
                value = configured[method]
                if isinstance(value, AsyncMock):
                    mock = value
                elif callable(value):
                    raise TypeError("fake_pool: callable SQL dispatch is not supported")
                else:
                    mock = AsyncMock(return_value=value)
            setattr(self, method, mock)
        self.transaction = MagicMock(side_effect=lambda: AsyncCtx(None))


class FakePool:
    """Pool and acquired connection share mocks; each acquire releases exactly once."""

    def __init__(self, **configured: Any) -> None:
        self.connection = FakeConnection(**configured)
        for method in _METHODS:
            setattr(self, method, getattr(self.connection, method))
        self.release = AsyncMock()
        self.acquire = MagicMock(
            side_effect=lambda: AsyncCtx(self.connection, on_exit=self.release)
        )


def fake_pool(**configured: Any) -> FakePool:
    """Configure only needed methods, including explicit ``None`` results.

    Pass an ``AsyncMock(side_effect=...)`` for queued results or exceptions.
    Unconfigured calls refuse; SQL text is never inspected or dispatched here.
    This is not evidence of schema, JSONB, transaction, role or RLS correctness.
    """
    return FakePool(**configured)
