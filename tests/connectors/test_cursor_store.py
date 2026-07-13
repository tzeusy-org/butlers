"""Focused tests for connector cursor registry persistence."""

from __future__ import annotations

from typing import Any

import pytest

from butlers.connectors.cursor_store import save_cursor

pytestmark = pytest.mark.unit


class _FakeConnection:
    def __init__(self) -> None:
        self.execute_args: tuple[Any, ...] | None = None

    async def execute(self, *args: Any) -> str:
        self.execute_args = args
        return "INSERT 0 1"


class _Acquire:
    def __init__(self, connection: _FakeConnection) -> None:
        self._connection = connection

    async def __aenter__(self) -> _FakeConnection:
        return self._connection

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _FakePool:
    def __init__(self) -> None:
        self.connection = _FakeConnection()

    def acquire(self) -> _Acquire:
        return _Acquire(self.connection)


@pytest.mark.asyncio
async def test_save_cursor_can_archive_internal_registry_row() -> None:
    pool = _FakePool()

    await save_cursor(
        pool,  # type: ignore[arg-type]
        "google_health",
        "google_health:user:account@example.invalid:11111111-2222-3333-4444-555555555555:activity",
        "2026-07-13",
        archive=True,
    )

    assert pool.connection.execute_args is not None
    sql, connector_type, endpoint_identity, cursor_value, _now = pool.connection.execute_args
    assert "archived_at" in sql
    assert connector_type == "google_health"
    assert endpoint_identity.endswith(":activity")
    assert cursor_value == "2026-07-13"
