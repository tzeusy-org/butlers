"""RuntimeAdapter boundary-contract tests."""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from typing import cast

import pytest

from butlers.core.runtimes.base import RuntimeAdapter, validated_session_timeout_overhead_s


@pytest.mark.parametrize(
    "declared",
    [True, False, -1, float("nan"), float("inf"), "0.5", None],
)
def test_timeout_overhead_rejects_malformed_adapter_declarations(declared: object) -> None:
    """All dispatch paths must fail closed on the same invalid declaration."""
    adapter = cast(RuntimeAdapter, SimpleNamespace(session_timeout_overhead_s=declared))

    assert validated_session_timeout_overhead_s(adapter) == 0.0


def test_timeout_overhead_accepts_finite_nonnegative_adapter_declaration() -> None:
    """A bounded numeric allowance remains available outside provider execution."""
    adapter = cast(RuntimeAdapter, SimpleNamespace(session_timeout_overhead_s=0.25))

    assert validated_session_timeout_overhead_s(adapter) == 0.25


def test_runtime_adapter_keeps_its_abstract_contract() -> None:
    """Adding a boundary helper cannot move adapter methods out of the ABC."""
    assert inspect.isabstract(RuntimeAdapter)
    assert {"binary_name", "invoke", "build_config_file", "parse_system_prompt_file"} <= (
        RuntimeAdapter.__abstractmethods__
    )
