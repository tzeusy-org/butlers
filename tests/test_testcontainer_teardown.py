from __future__ import annotations

import pytest
from docker.errors import APIError

from conftest import _is_transient_docker_teardown_error, _remove_container_with_retry


class _FakeHTTPResponse:
    status_code = 500
    url = "http+docker://localhost/v1.47/containers/deadbeef?v=True&force=True"
    reason = "Internal Server Error"


def _wrapped_api_error() -> RuntimeError:
    cause = APIError(
        "container removal failed",
        response=_FakeHTTPResponse(),
        explanation="could not kill: tried to kill container, but did not receive an exit event",
    )
    wrapped = RuntimeError("container teardown failed")
    wrapped.__cause__ = cause
    return wrapped


class _FakeContainer:
    def __init__(self, failures: list[Exception]) -> None:
        self.failures = failures
        self.remove_calls = 0

    def remove(self, *, force: bool, v: bool) -> None:
        self.remove_calls += 1
        if self.failures:
            raise self.failures.pop(0)


def test_remove_container_retries_transient_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("conftest.time.sleep", lambda _: None)
    container = _FakeContainer(
        [
            RuntimeError("could not kill container, did not receive an exit event"),
            RuntimeError("could not kill container, did not receive an exit event"),
        ]
    )

    _remove_container_with_retry(container, force=True, delete_volume=True)

    assert container.remove_calls == 3


def test_remove_container_retries_when_transient_marker_is_in_cause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("conftest.time.sleep", lambda _: None)
    container = _FakeContainer([_wrapped_api_error()])

    _remove_container_with_retry(container, force=True, delete_volume=True)

    assert container.remove_calls == 2


def test_remove_container_raises_non_transient_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("conftest.time.sleep", lambda _: None)
    container = _FakeContainer([RuntimeError("permission denied")])

    with pytest.raises(RuntimeError, match="permission denied"):
        _remove_container_with_retry(container, force=True, delete_volume=True)

    assert container.remove_calls == 1


def test_remove_container_warns_after_max_transient_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("conftest.time.sleep", lambda _: None)
    container = _FakeContainer([RuntimeError("did not receive an exit event") for _ in range(4)])

    with pytest.warns(RuntimeWarning, match="Ignoring transient Docker teardown error"):
        _remove_container_with_retry(container, force=True, delete_volume=True)

    assert container.remove_calls == 4


def test_transient_error_detection_scans_exception_causes() -> None:
    assert _is_transient_docker_teardown_error(_wrapped_api_error())


@pytest.mark.parametrize(
    "message",
    [
        (
            'cannot remove container "abc": could not kill: '
            "tried to kill container, but did not receive an exit event"
        ),
        'cannot remove container "abc": removal of container abc is already in progress',
        'cannot remove container "abc": container is dead or marked for removal',
    ],
)
def test_transient_error_detection_matches_additional_docker_apierror_variants(
    message: str,
) -> None:
    assert _is_transient_docker_teardown_error(APIError(message))
