from __future__ import annotations

import pytest

from conftest import _remove_container_with_retry


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
