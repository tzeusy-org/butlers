from __future__ import annotations

import pytest

from conftest import _initialize_docker_client_with_retry


def test_initialize_docker_client_retries_transient_timeouts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("conftest.time.sleep", lambda _: None)
    attempts = 0

    def _initialize() -> None:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RuntimeError(
                "Error while fetching server API version: "
                "UnixHTTPConnectionPool(host='localhost', port=None): "
                "Read timed out. (read timeout=60)"
            )

    _initialize_docker_client_with_retry(_initialize, max_attempts=4)

    assert attempts == 3


def test_initialize_docker_client_raises_non_transient_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("conftest.time.sleep", lambda _: None)
    attempts = 0

    def _initialize() -> None:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("permission denied")

    with pytest.raises(RuntimeError, match="permission denied"):
        _initialize_docker_client_with_retry(_initialize, max_attempts=4)

    assert attempts == 1


def test_initialize_docker_client_raises_after_max_transient_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("conftest.time.sleep", lambda _: None)
    attempts = 0

    def _initialize() -> None:
        nonlocal attempts
        attempts += 1
        raise RuntimeError(
            "Error while fetching server API version: "
            "UnixHTTPConnectionPool(host='localhost', port=None): "
            "Read timed out. (read timeout=60)"
        )

    with pytest.raises(RuntimeError, match="Error while fetching server API version"):
        _initialize_docker_client_with_retry(_initialize, max_attempts=3)

    assert attempts == 3
