"""Runtime credential isolation survives the daemon's phased startup wiring."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from butlers import lifecycle
from butlers.config import ButlerConfig
from butlers.core.spawner import Spawner
from butlers.exceptions import RuntimeBinaryNotFoundError

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
    # An unused builder must still fail the runtime-identity/call assertions if startup
    # regresses to constructing an adapter outside the registry.
    monkeypatch.setattr(lifecycle, "build_runtime_adapter", adapter_factory, raising=False)
    which = Mock(return_value="/synthetic/runtime")
    monkeypatch.setattr(lifecycle.shutil, "which", which)
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
        log_root=tmp_path / "logs",
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

    adapter_kwargs = {
        "codex_authority": credential_store,
        "butler_name": "messenger",
        "log_root": state.log_root,
    }
    adapter_factory.assert_called_once_with(lifecycle.DEFAULT_RUNTIME_TYPE, **adapter_kwargs)
    which.assert_called_once_with(runtime.binary_name)

    # Neither a failed registry construction nor a missing binary may advance startup
    # into audit-pool creation, module wiring, Spawner construction or client setup.
    del daemon.spawner
    construction_error = RuntimeError("synthetic adapter construction failure")
    for failure, expected_error in (
        (construction_error, RuntimeError),
        (None, RuntimeBinaryNotFoundError),
    ):
        adapter_factory.reset_mock()
        adapter_factory.side_effect = failure
        which.reset_mock()
        which.return_value = None
        daemon._create_audit_pool.reset_mock()
        daemon._wire_pipelines.reset_mock()
        daemon._connect_switchboard.reset_mock()
        module.wire_audit_pool.reset_mock()

        with pytest.raises(expected_error) as exc:
            await lifecycle._startup_spawner(daemon, state)

        if failure is not None:
            assert exc.value is failure
            which.assert_not_called()
        else:
            which.assert_called_once_with(runtime.binary_name)
        adapter_factory.assert_called_once_with(lifecycle.DEFAULT_RUNTIME_TYPE, **adapter_kwargs)
        daemon._create_audit_pool.assert_not_awaited()
        module.wire_audit_pool.assert_not_called()
        daemon._wire_pipelines.assert_not_called()
        daemon._connect_switchboard.assert_not_awaited()
        assert not hasattr(daemon, "spawner")
