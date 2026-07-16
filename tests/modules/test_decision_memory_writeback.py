"""Failure-boundary tests for deterministic approval decision-memory writeback."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from butlers.modules.approvals.decision_memory import DecisionMemoryWriter
from butlers.modules.approvals.module import ApprovalsModule


@pytest.mark.asyncio
async def test_write_failure_is_logged_without_raising(caplog) -> None:
    """Decision memory is observational and cannot reverse a terminal outcome."""
    writer = DecisionMemoryWriter(
        butler_name="home",
        memory_pool_provider=lambda: (_ for _ in ()).throw(RuntimeError("memory unavailable")),
        resolution_pool_provider=object,
        embedding_engine_provider=object,
        tool_meta_provider=lambda _tool_name: None,
    )
    action = SimpleNamespace(
        id=uuid.uuid4(),
        tool_name="notify",
        tool_args={"recipient": "123456"},
    )

    await writer.record_terminal_decision(action, "rejected")

    assert "terminal decision remains committed" in caplog.text


def test_module_without_memory_module_has_no_writeback_hook() -> None:
    """Memory-module absence is a silent no-op, not a dependency failure."""
    module = ApprovalsModule()
    module._db = object()  # The memory module is intentionally absent.
    module._butler_name = "messenger"

    module.on_all_modules_ready({})

    assert module.get_decision_memory_writer() is None


def test_module_binds_the_memory_modules_own_pool() -> None:
    """A private memory-schema pool is selected instead of the approvals pool."""
    approvals_pool = object()
    memory_pool = object()
    memory_module = SimpleNamespace(
        _get_pool=lambda: memory_pool,
        _get_embedding_engine=object,
    )
    module = ApprovalsModule()
    module._db = SimpleNamespace(pool=approvals_pool)
    module._butler_name = "relationship"

    module.on_all_modules_ready({"memory": memory_module})

    writer = module.get_decision_memory_writer()
    assert writer is not None
    assert writer._memory_pool_provider() is memory_pool
    assert writer._resolution_pool_provider() is approvals_pool
