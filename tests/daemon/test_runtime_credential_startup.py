"""Runtime credential isolation survives the daemon's phased startup wiring."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from butlers import lifecycle
from butlers.config import ButlerConfig
from butlers.core.spawner import Spawner

pytestmark = pytest.mark.unit


async def test_startup_constructs_real_spawner_without_module_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep daemon credential validation separate from the child runtime boundary."""
    pool = object()
    audit_pool = object()
    credential_store = object()
    runtime = SimpleNamespace(binary_name="synthetic-runtime")
    adapter_factory = Mock(return_value=runtime)
    monkeypatch.setattr(lifecycle, "get_adapter", lambda _: adapter_factory)
    monkeypatch.setattr(lifecycle.shutil, "which", lambda _: "/synthetic/runtime")
    module = SimpleNamespace(wire_audit_pool=Mock())
    daemon = SimpleNamespace(
        config=ButlerConfig(name="messenger", port=41100),
        config_dir=tmp_path,
        _runtime_config_accessor=None,
        _create_audit_pool=AsyncMock(return_value=audit_pool),
        _wire_pipelines=Mock(),
        _connect_switchboard=AsyncMock(),
    )
    state = lifecycle.StartupState(
        pool=pool,
        credential_store=credential_store,
        module_creds={"telegram": ["BUTLER_TELEGRAM_TOKEN"]},
        started_modules=[module],
    )

    await lifecycle._startup_spawner(daemon, state)

    assert isinstance(daemon.spawner, Spawner)
    assert daemon.spawner._pool is pool
    assert daemon.spawner._runtime is runtime
    assert daemon.spawner._credential_store is credential_store
    assert daemon._audit_pool is audit_pool
    module.wire_audit_pool.assert_called_once_with(audit_pool)
    daemon._create_audit_pool.assert_awaited_once_with(pool)
    daemon._wire_pipelines.assert_called_once_with(pool)
    daemon._connect_switchboard.assert_awaited_once_with()
    assert state.module_creds == {"telegram": ["BUTLER_TELEGRAM_TOKEN"]}
