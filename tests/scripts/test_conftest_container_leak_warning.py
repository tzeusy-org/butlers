"""Tests for the leaked-container warning in the root conftest.

Regression guard for bu-3zu5l: when Docker teardown keeps failing, the root
conftest gives up and lets the run continue, which silently leaves a live
Postgres container behind. The warning it emits at that moment is the only
record tying that container back to the run that leaked it, so it has to name
the container.
"""

from __future__ import annotations

import importlib.util
import sys
import warnings
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _load_root_conftest() -> Any:
    """Load the root conftest by path.

    Its module-level testcontainers patches are idempotent (they tag the
    functions they install and return early on a second call), so importing it
    under a private name here does not re-wrap anything pytest already wired.
    """
    spec = importlib.util.spec_from_file_location(
        "_butlers_root_conftest_under_test", REPO_ROOT / "conftest.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


root_conftest = _load_root_conftest()


class _FakeContainer:
    def __init__(self, *, name: str, container_id: str, error: Exception | None) -> None:
        self.name = name
        self.id = container_id
        self._error = error
        self.remove_calls = 0

    def remove(self, *, force: bool, v: bool) -> None:
        self.remove_calls += 1
        if self._error is not None:
            raise self._error


def test_giving_up_on_teardown_names_the_leaked_container() -> None:
    container = _FakeContainer(
        name="angry_dubinsky",
        container_id="d14bb21f2cc4" + "0" * 52,
        error=RuntimeError("Could not kill container: did not receive an exit event"),
    )

    with pytest.warns(RuntimeWarning) as recorded:
        root_conftest._remove_container_with_retry(
            container, force=True, delete_volume=True, max_attempts=2
        )

    message = str(recorded[0].message)
    assert "angry_dubinsky" in message
    assert "d14bb21f2cc4" in message
    assert "reap_orphaned_testcontainers.py" in message
    assert container.remove_calls == 2


def test_successful_teardown_warns_about_nothing() -> None:
    container = _FakeContainer(name="brave_gates", container_id="060c3619298b", error=None)

    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        root_conftest._remove_container_with_retry(container, force=True, delete_volume=True)

    assert recorded == []
    assert container.remove_calls == 1


def test_non_transient_teardown_error_still_raises() -> None:
    container = _FakeContainer(
        name="brave_gates", container_id="060c3619298b", error=RuntimeError("permission denied")
    )

    with pytest.raises(RuntimeError, match="permission denied"):
        root_conftest._remove_container_with_retry(container, force=True, delete_volume=True)

    assert container.remove_calls == 1


def test_container_identity_tolerates_a_container_without_name_or_id() -> None:
    class _Bare:
        pass

    assert root_conftest._container_identity(_Bare()) == "<unnamed> (<unknown-id>)"
