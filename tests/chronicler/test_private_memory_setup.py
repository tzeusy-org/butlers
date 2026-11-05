"""Schema-faithful fixture forwarding for the fixed Chronicler 030 boundary."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, call

import pytest

from tests.chronicler import _private_memory_setup as setup

pytestmark = pytest.mark.unit


async def test_default_prepared_pool_uses_same_domain_for_clone_upgrade_and_reads(monkeypatch):
    migrations = AsyncMock()
    monkeypatch.setattr(setup, "run_migrations", migrations)
    cloned = []

    def fresh_db(*chains, **kwargs):
        cloned.append((chains, kwargs))
        return "test-url"

    pool = object()
    db = SimpleNamespace(open=AsyncMock(return_value=pool))
    result = await setup.open_prepared_chronicler_pool(db, fresh_db, "chronicler", max_size=5)
    assert result is pool
    assert cloned == [
        (
            ("chronicler",),
            {
                "schemas": {"chronicler": "chronicler"},
                "revisions": {"chronicler": "chronicler_029"},
            },
        )
    ]
    assert migrations.await_args_list == [
        call("test-url", chain="core", schema="chronicler_mem"),
        call("test-url", chain="memory", schema="chronicler_mem"),
        call("test-url", chain="chronicler", schema="chronicler"),
    ]
    db.open.assert_awaited_once_with("test-url", schema="chronicler", max_size=5)


async def test_prepared_pool_preserves_explicit_chain_mapping_and_pool_options(monkeypatch):
    migrations = AsyncMock()
    monkeypatch.setattr(setup, "run_migrations", migrations)
    schemas = {"core": "chronicler", "chronicler": "chronicler"}
    original = dict(schemas)
    cloned = []

    def fresh_db(*chains, **kwargs):
        cloned.append((chains, kwargs))
        return "test-url"

    db = SimpleNamespace(open=AsyncMock())
    await setup.open_prepared_chronicler_pool(
        db,
        fresh_db,
        "core",
        "chronicler",
        domain_schema="chronicler",
        pool_schema="chronicler",
        schemas=schemas,
        min_size=2,
        max_size=5,
    )
    assert schemas == original
    assert cloned == [
        (
            ("core", "chronicler"),
            {"schemas": original, "revisions": {"chronicler": "chronicler_029"}},
        )
    ]
    assert migrations.await_args_list[-1] == call(
        "test-url", chain="chronicler", schema="chronicler"
    )
    db.open.assert_awaited_once_with("test-url", schema="chronicler", min_size=2, max_size=5)
