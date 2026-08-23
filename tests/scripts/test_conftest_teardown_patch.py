"""Pins the shape of the root conftest's testcontainers teardown patch.

Regression guard for bu-1y1qs. This file had grown *two* independent patches
over ``DockerContainer.stop``, one wrapping the other, with disagreeing
definitions of "transient" -- so the outer layer could re-raise exactly what the
inner one had deliberately swallowed, and the retry budget was the product of
the two. The three things that went wrong are the three things pinned here:

1. ``DockerContainer.stop`` is patched exactly once.
2. There is one transient-error rule, and it is the union of what the two
   layers used to know.
3. A final *transient* teardown failure swallows with a warning; a
   non-transient one raises.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest
import requests
from docker.errors import APIError, NotFound
from requests.exceptions import ReadTimeout

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CONFTEST_PATH = REPO_ROOT / "conftest.py"


def _load_root_conftest() -> Any:
    """Load the root conftest by path (its patches are idempotent, so this is safe)."""
    spec = importlib.util.spec_from_file_location(
        "_butlers_root_conftest_teardown_patch_under_test", CONFTEST_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


root_conftest = _load_root_conftest()


def _api_error(status_code: int, explanation: str, *, cls: type[APIError] = APIError) -> APIError:
    response = requests.Response()
    response.status_code = status_code
    response.url = "http+docker://localhost/v1.43/containers/d14bb21f2cc4"
    response.reason = "docker daemon said no"
    return cls("api error", response=response, explanation=explanation)


class _FakeContainer:
    """Raises ``error`` from every ``remove()`` call, counting the attempts."""

    def __init__(self, error: Exception | None = None) -> None:
        self.name = "angry_dubinsky"
        self.id = "d14bb21f2cc4" + "0" * 52
        self._error = error
        self.remove_calls = 0

    def remove(self, *, force: bool, v: bool) -> None:
        self.remove_calls += 1
        if self._error is not None:
            raise self._error


# --- 1. Patched exactly once ------------------------------------------------


def test_conftest_assigns_dockercontainer_stop_exactly_once() -> None:
    """Structural, rename-proof pin: one ``DockerContainer.stop = ...`` in the file.

    The second layer was a separate module-level function, so a test that only
    named the deleted function would not stop the next one from appearing under
    a different name. Counting the assignments does.
    """
    tree = ast.parse(CONFTEST_PATH.read_text())

    assignments = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Attribute)
        and target.attr == "stop"
        and isinstance(target.value, ast.Name)
        and target.value.id == "DockerContainer"
    ]

    assert len(assignments) == 1, (
        "conftest.py must patch DockerContainer.stop exactly once; found "
        f"{len(assignments)} assignments at lines {[n.lineno for n in assignments]}"
    )


def test_installed_stop_is_a_single_layer_over_upstream() -> None:
    """The live patch is ours, and what sits directly under it is upstream."""
    from testcontainers.core.container import DockerContainer

    stop = DockerContainer.stop
    assert getattr(stop, "__butlers_resilient__", False) is True

    underneath = stop.__wrapped__
    assert underneath.__module__.startswith("testcontainers."), (
        "DockerContainer.stop is wrapped over something other than upstream stop, "
        f"which means a second patch layer exists: {underneath!r}"
    )


def test_installing_the_stop_patch_again_does_not_add_a_layer() -> None:
    from testcontainers.core.container import DockerContainer

    before = DockerContainer.stop
    root_conftest._install_resilient_testcontainers_stop()
    assert DockerContainer.stop is before


# --- 2. One transient-error rule --------------------------------------------


@pytest.mark.parametrize(
    ("label", "exc"),
    [
        # docker-py raises NotFound (404), not a 500. The deleted outer layer
        # required status_code == 500 and so would have called this fatal,
        # contradicting its own "no such container" snippet.
        ("404 no such container", _api_error(404, "No such container: d14bb21f2cc4", cls=NotFound)),
        # A concurrent remove is a 409, also not a 500.
        (
            "409 removal already in progress",
            _api_error(409, "removal of container d14bb21f2cc4 is already in progress"),
        ),
        (
            "500 no exit event",
            _api_error(
                500,
                "Could not kill container: tried to kill container, but did not receive "
                "an exit event",
            ),
        ),
        (
            "500 dead or marked for removal",
            _api_error(500, "container is dead or marked for removal"),
        ),
        # Contributed by the deleted outer layer, and the only thing it knew
        # that the surviving rule did not: a contended daemon times out the
        # remove call it is still processing.
        ("read timeout", ReadTimeout("UnixHTTPConnectionPool(host='localhost'): Read timed out.")),
    ],
)
def test_known_docker_teardown_races_are_transient(label: str, exc: Exception) -> None:
    assert root_conftest._is_transient_docker_teardown_error(exc) is True, label


@pytest.mark.parametrize(
    ("label", "exc"),
    [
        ("permission denied", RuntimeError("permission denied while removing container")),
        ("disk full", OSError("no space left on device")),
        ("400 bad request", _api_error(400, "invalid remove request")),
    ],
)
def test_real_teardown_failures_are_not_transient(label: str, exc: Exception) -> None:
    assert root_conftest._is_transient_docker_teardown_error(exc) is False, label


def test_transient_marker_is_found_through_the_exception_chain() -> None:
    """docker-py chains its APIError off the underlying HTTPError."""
    try:
        try:
            raise _api_error(404, "No such container: d14bb21f2cc4", cls=NotFound)
        except NotFound as inner:
            raise RuntimeError("failed to remove container") from inner
    except RuntimeError as exc:
        assert root_conftest._is_transient_docker_teardown_error(exc) is True


# --- 3. Swallow vs raise on the final failure -------------------------------


def test_final_transient_failure_swallows_rather_than_failing_the_session() -> None:
    """The decision, made explicit (bu-1y1qs).

    Teardown of a session-scoped fixture runs after every test has already
    passed. Raising a Docker race here would redden a ~40 minute gate over an
    infrastructure hiccup, and the damage it reports -- one leaked container --
    is recoverable: Ryuk reaps the ordinary case and
    ``scripts/reap_orphaned_testcontainers.py`` sweeps the residue. So we warn
    loudly, name the container, and let the run finish.
    """
    container = _FakeContainer(_api_error(500, "did not receive an exit event"))

    with pytest.warns(RuntimeWarning, match="Leaked test container"):
        root_conftest._remove_container_with_retry(
            container, force=True, delete_volume=True, base_delay_seconds=0
        )

    assert container.remove_calls == root_conftest._TESTCONTAINER_STOP_RETRY_ATTEMPTS


def test_read_timeout_is_retried_then_swallowed() -> None:
    """The behaviour change: the deleted outer layer re-raised this after retrying."""
    container = _FakeContainer(ReadTimeout("Read timed out. (read timeout=60)"))

    with pytest.warns(RuntimeWarning, match="Leaked test container"):
        root_conftest._remove_container_with_retry(
            container, force=True, delete_volume=True, base_delay_seconds=0
        )

    assert container.remove_calls == root_conftest._TESTCONTAINER_STOP_RETRY_ATTEMPTS


def test_non_transient_failure_raises_on_the_first_attempt() -> None:
    container = _FakeContainer(RuntimeError("permission denied"))

    with pytest.raises(RuntimeError, match="permission denied"):
        root_conftest._remove_container_with_retry(container, force=True, delete_volume=True)

    assert container.remove_calls == 1


def test_retries_back_off_exponentially(monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []
    monkeypatch.setattr(root_conftest.time, "sleep", slept.append)

    container = _FakeContainer(_api_error(500, "did not receive an exit event"))
    with pytest.warns(RuntimeWarning):
        root_conftest._remove_container_with_retry(
            container, force=True, delete_volume=True, max_attempts=4, base_delay_seconds=0.1
        )

    # One sleep per retry, never after the final attempt.
    assert slept == [0.1, 0.2, 0.4]
