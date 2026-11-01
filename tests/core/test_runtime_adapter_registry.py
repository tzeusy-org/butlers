"""The adapter registry is the one place that builds runtime adapters (bu-m0vjeb.1).

Pins the exact constructor arguments each factory passed before the refactor, so routing every
factory through the registry changes nothing: Codex gets ``butler_name`` and its explicit
authority only, every other runtime gets the caller's options and the provider configuration.
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.connectors.discretion_dispatcher import DiscretionDispatcher
from butlers.core.runtime_probe_control import coordinator
from butlers.core.runtimes.registry import (
    AdapterRegistry,
    build_runtime_adapter,
    lacks_required_authority,
    takes_provider_config,
)
from butlers.core.spawner import Spawner

pytestmark = pytest.mark.unit

_PROVIDER = {"ollama": {"base_url": "http://ollama:11434/v1"}}


def test_codex_gets_only_its_name_and_explicit_authority() -> None:
    authority, generic = MagicMock(name="authority"), MagicMock(name="generic")
    with patch("butlers.core.runtimes.base.create_adapter") as create:
        build_runtime_adapter(
            "codex",
            codex_authority=authority,
            provider_config=_PROVIDER,
            butler_name="general",
            log_root="/logs",
            credential_store=generic,
        )

    create.assert_called_once_with("codex", credential_store=authority, butler_name="general")


def test_other_runtimes_get_the_callers_options_and_provider_config() -> None:
    store = MagicMock(name="store")
    with patch("butlers.core.runtimes.base.create_adapter") as create:
        build_runtime_adapter(
            "opencode",
            codex_authority=MagicMock(name="authority"),
            provider_config=_PROVIDER,
            butler_name="general",
            credential_store=store,
        )

    create.assert_called_once_with(
        "opencode", provider_config=_PROVIDER, butler_name="general", credential_store=store
    )


def test_registry_caches_per_runtime_until_the_provider_config_changes() -> None:
    registry = AdapterRegistry(butler_name="general")
    with patch(
        "butlers.core.runtimes.base.create_adapter", side_effect=lambda *a, **k: MagicMock()
    ) as create:
        first = registry.for_runtime("opencode", _PROVIDER)
        assert registry.for_runtime("opencode", _PROVIDER) is first
        assert registry.for_runtime("opencode", {"ollama": {"base_url": "other"}}) is not first
        assert registry.for_runtime("claude") is not first

    assert create.call_count == 3


def test_codex_authority_and_provider_config_predicates() -> None:
    global_authority = MagicMock(has_system_global_authority=True)
    local_store = MagicMock(has_system_global_authority=False)

    assert not takes_provider_config("codex")
    assert takes_provider_config("opencode")
    assert lacks_required_authority("codex", local_store)
    assert lacks_required_authority("codex", None)
    assert not lacks_required_authority("codex", global_authority)
    assert not lacks_required_authority("opencode", None)


def test_discretion_codex_adapter_keeps_its_explicit_authority() -> None:
    authority, generic = MagicMock(name="authority"), MagicMock(name="generic")
    dispatcher = DiscretionDispatcher(
        MagicMock(), butler_name="d", credential_store=generic, codex_auth_authority=authority
    )
    with patch("butlers.core.runtimes.base.create_adapter") as create:
        dispatcher._get_or_create_adapter("codex", _PROVIDER)
        dispatcher._get_or_create_adapter("opencode", _PROVIDER)

    assert create.call_args_list[0].args == ("codex",)
    assert create.call_args_list[0].kwargs == {"credential_store": authority, "butler_name": "d"}
    assert create.call_args_list[1].kwargs == {
        "provider_config": _PROVIDER,
        "butler_name": "d",
        "credential_store": generic,
    }


def test_discretion_without_a_generic_store_passes_none_of_it() -> None:
    dispatcher = DiscretionDispatcher(MagicMock(), butler_name="d")
    with patch("butlers.core.runtimes.base.create_adapter") as create:
        dispatcher._get_or_create_adapter("opencode")

    create.assert_called_once_with("opencode", provider_config=None, butler_name="d")


async def test_probe_codex_skips_provider_lookup_and_passes_only_the_authority() -> None:
    authority = MagicMock(name="authority")
    resolve = AsyncMock(return_value=_PROVIDER)
    with (
        patch("butlers.core.spawner.resolve_provider_config", resolve),
        patch("butlers.core.runtimes.base.create_adapter") as create,
    ):
        await coordinator.build_probe_adapter(
            MagicMock(), "codex", "gpt-5", codex_auth_authority=authority
        )
        await coordinator.build_probe_adapter(MagicMock(), "opencode", "ollama/qwen")

    resolve.assert_awaited_once()
    assert resolve.await_args.args[1] == "ollama/qwen"
    assert create.call_args_list[0].kwargs == {"credential_store": authority}
    assert create.call_args_list[1].kwargs == {"provider_config": _PROVIDER}


@pytest.mark.parametrize(
    "factory",
    [
        Spawner._get_or_create_adapter,
        DiscretionDispatcher._get_or_create_adapter,
        coordinator.build_probe_adapter,
    ],
)
def test_factory_sites_do_not_special_case_codex(factory) -> None:
    """AC1: the registry is the only place that names the Codex runtime."""
    source = inspect.getsource(factory)

    assert '"codex"' not in source
    assert "'codex'" not in source
