"""Shared pytest fixtures and helper types used across test trees."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest


@dataclass
class SpawnerResult:
    """Represents the result of a LLM CLI spawner invocation."""

    output: str | None = None
    success: bool = False
    tool_calls: list[dict] = field(default_factory=list)
    error: str | None = None
    duration_ms: int = 0


class MockSpawner:
    """A mock LLM CLI spawner that returns configurable results and records invocations."""

    def __init__(self, default_result: SpawnerResult | None = None) -> None:
        self.default_result = default_result or SpawnerResult()
        self.invocations: list[dict] = []
        self._results: list[SpawnerResult] = []

    def enqueue_result(self, result: SpawnerResult) -> None:
        """Enqueue a result to be returned on the next invocation."""
        self._results.append(result)

    async def spawn(self, **kwargs) -> SpawnerResult:
        """Simulate spawning an LLM CLI instance."""
        self.invocations.append(kwargs)
        if self._results:
            return self._results.pop(0)
        return self.default_result


@pytest.fixture
def mock_spawner() -> MockSpawner:
    """Provide a MockSpawner instance for tests."""
    return MockSpawner()


__all__ = ["MockSpawner", "SpawnerResult", "mock_spawner"]
