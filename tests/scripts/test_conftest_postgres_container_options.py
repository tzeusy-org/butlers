"""Tests for how the root conftest configures the shared Postgres testcontainer.

Regression guard for bu-29wkwj.6. The nightly faketime legs failed on every
scheduled run from 2026-07-25 for two reasons this configuration owns:

1. libfaketime shifted only the pytest process clock, so every DB test that
   compares a Postgres ``now()`` against a Python ``datetime.now()`` failed by
   the leg offset unless someone remembered the ``pg_clock`` marker. The
   container now runs under the same shifted clock as the process, as the
   ``postgres`` user: started as root, libfaketime creates its semaphore
   ``/faketime_sem_1`` as root, and the entrypoint's drop to ``postgres`` then
   exits 1 on "sem_open failed ... Permission denied" (run 39770283852).
2. Docker's default 64 MB ``/dev/shm`` ran out once one container had served a
   third of the suite ("could not resize shared memory segment ... No space
   left on device"), and Postgres then crashed into recovery mode.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_LIBRARY = "/usr/lib/x86_64-linux-gnu/faketime/libfaketime.so.1"


def _load_root_conftest() -> Any:
    """Load the root conftest by path (its patches are idempotent, so this is safe)."""
    spec = importlib.util.spec_from_file_location(
        "_butlers_root_conftest_postgres_options_under_test", REPO_ROOT / "conftest.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


root_conftest = _load_root_conftest()


class _RecordingContainer:
    """Records the testcontainers builder calls the conftest makes."""

    def __init__(self) -> None:
        self.volumes: list[tuple[str, str, str]] = []
        self.env: dict[str, str] = {}
        self.kwargs: dict[str, object] = {}

    def with_volume_mapping(self, host: str, container: str, mode: str = "ro") -> Any:
        self.volumes.append((host, container, mode))
        return self

    def with_env(self, key: str, value: str) -> Any:
        self.env[key] = value
        return self

    def with_kwargs(self, **kwargs: object) -> Any:
        # testcontainers replaces, not merges, the kwargs on each call.
        self.kwargs = kwargs
        return self


def _configure(environ: dict[str, str]) -> _RecordingContainer:
    container = _RecordingContainer()
    assert root_conftest._configure_postgres_container(container, environ) is container
    return container


def test_container_gets_shm_beyond_the_docker_default() -> None:
    container = _configure({})

    assert container.kwargs == {"shm_size": root_conftest._POSTGRES_SHM_SIZE}
    assert root_conftest._POSTGRES_SHM_SIZE == "1g"


def test_unshifted_process_leaves_the_container_clock_alone() -> None:
    container = _configure({"LD_PRELOAD": "/usr/lib/libother.so"})

    assert container.volumes == []
    assert container.env == {}


@pytest.mark.parametrize(
    "preload",
    [_LIBRARY, f"/usr/lib/libother.so {_LIBRARY}", f"/usr/lib/libother.so:{_LIBRARY}"],
)
def test_shifted_process_shares_its_offset_with_the_container(preload: str) -> None:
    container = _configure({"LD_PRELOAD": preload, "FAKETIME": "+45d"})

    mounted = root_conftest._CONTAINER_FAKETIME_LIBRARY
    assert container.volumes == [(_LIBRARY, mounted, "ro")]
    assert container.env == {"LD_PRELOAD": mounted, "FAKETIME": "+45d"}
    assert container.kwargs == {"shm_size": root_conftest._POSTGRES_SHM_SIZE, "user": "postgres"}


def test_offset_without_the_library_preloaded_is_not_forwarded() -> None:
    container = _configure({"FAKETIME": "+45d"})

    assert container.volumes == []
    assert container.env == {}
