"""A real dashboard DatabaseManager over given pools, without connecting anywhere."""

from __future__ import annotations

import pytest

from butlers.api.db import DatabaseManager
from butlers.testing.db_manager import db_manager
from butlers.testing.fake_pool import fake_pool

pytestmark = pytest.mark.unit


def test_serves_registered_pools_and_refuses_others_like_production():
    general, health = object(), object()
    manager = db_manager({"general": general, "health": health})

    assert isinstance(manager, DatabaseManager)
    assert manager.pool("general") is general
    assert manager.butler_names == ["general", "health"]
    with pytest.raises(KeyError, match="No pool for butler: atlas"):
        manager.pool("atlas")


def test_reports_domain_and_memory_schemas():
    manager = db_manager(
        {"chronicler": object(), "general": object(), "legacy": object()},
        schemas={"chronicler": "chronicler", "general": "general"},
        memory_schemas={"chronicler": "chronicler_mem"},
    )

    assert manager.schema_for_butler("general") == "general"
    assert manager.schema_for_butler("legacy") is None
    assert manager.memory_schema_for_butler("chronicler") == "chronicler_mem"
    assert manager.memory_schema_for_butler("general") == "general"


def test_module_census_is_unknown_until_modules_are_given():
    assert db_manager({"home": object()}).butlers_with_module("approvals") is None

    manager = db_manager(
        {"home": object(), "health": object()},
        modules={"home": ["approvals"], "health": ["memory"]},
    )

    assert manager.butlers_with_module("approvals") == ["home"]
    assert manager.configured_butlers_with_module("memory") == ["health"]


def test_credential_pool_is_optional():
    with pytest.raises(KeyError, match="not configured"):
        db_manager({}).credential_shared_pool()

    shared = object()
    assert db_manager({}, credential_pool=shared).credential_shared_pool() is shared


async def test_fans_out_over_the_given_pools():
    ok = fake_pool(fetch=[{"n": 1}])
    broken = fake_pool()
    manager = db_manager({"general": ok, "health": broken})

    results, failed = await manager.fan_out_with_status("SELECT 1 AS n")

    assert results == {"general": [{"n": 1}], "health": []}
    assert failed == ["health"]
