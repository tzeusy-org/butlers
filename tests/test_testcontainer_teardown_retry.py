from __future__ import annotations

from dataclasses import dataclass

import pytest
from docker.errors import APIError
from requests.exceptions import ReadTimeout

import conftest

pytestmark = pytest.mark.unit


@dataclass
class _Response:
    status_code: int


def _docker_api_error(*, status_code: int, explanation: str) -> APIError:
    return APIError(
        message="docker api error",
        response=_Response(status_code=status_code),
        explanation=explanation,
    )


def test_transient_teardown_error_is_detected() -> None:
    err = _docker_api_error(
        status_code=500,
        explanation=(
            "cannot remove container: could not kill: failed to exit within 10s "
            "- did not receive an exit event"
        ),
    )

    assert conftest._is_transient_testcontainer_teardown_error(err)


def test_non_transient_teardown_error_is_not_detected() -> None:
    err = _docker_api_error(
        status_code=500,
        explanation="cannot remove container: conflict",
    )

    assert not conftest._is_transient_testcontainer_teardown_error(err)


def test_read_timeout_teardown_error_is_detected() -> None:
    assert conftest._is_transient_testcontainer_teardown_error(
        ReadTimeout("UnixHTTPConnectionPool(host='localhost', port=None): Read timed out.")
    )


def test_retry_testcontainer_stop_retries_transient_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(conftest.time, "sleep", lambda _: None)

    calls = 0

    def stop() -> None:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise _docker_api_error(
                status_code=500,
                explanation="could not kill: did not receive an exit event",
            )

    conftest._retry_testcontainer_stop(stop, max_attempts=4, base_delay_seconds=0.001)
    assert calls == 3


def test_retry_testcontainer_stop_retries_read_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(conftest.time, "sleep", lambda _: None)

    calls = 0

    def stop() -> None:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise ReadTimeout(
                "UnixHTTPConnectionPool(host='localhost', port=None): Read timed out."
            )

    conftest._retry_testcontainer_stop(stop, max_attempts=4, base_delay_seconds=0.001)
    assert calls == 3


def test_retry_testcontainer_stop_does_not_retry_non_transient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(conftest.time, "sleep", lambda _: None)

    calls = 0

    def stop() -> None:
        nonlocal calls
        calls += 1
        raise _docker_api_error(status_code=500, explanation="cannot remove container: conflict")

    with pytest.raises(APIError):
        conftest._retry_testcontainer_stop(stop, max_attempts=4, base_delay_seconds=0.001)

    assert calls == 1


def test_retry_testcontainer_stop_raises_after_max_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(conftest.time, "sleep", lambda _: None)

    calls = 0

    def stop() -> None:
        nonlocal calls
        calls += 1
        raise _docker_api_error(
            status_code=500,
            explanation="could not kill: did not receive an exit event",
        )

    with pytest.raises(APIError):
        conftest._retry_testcontainer_stop(stop, max_attempts=3, base_delay_seconds=0.001)

    assert calls == 3


def test_retry_testcontainer_stop_rejects_invalid_attempts() -> None:
    with pytest.raises(ValueError, match="max_attempts must be >= 1"):
        conftest._retry_testcontainer_stop(lambda: None, max_attempts=0)
