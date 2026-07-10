"""Shared test configuration for all butler tool test suites under roster/.

This conftest is discovered by pytest for all tests under roster/ and provides:

1. Auto-applied marks: roster tool tests default to ``integration`` and are
   skipped when Docker is unavailable, without repeating this boilerplate in
   every individual test_tools.py. A test that explicitly declares itself
   ``pytest.mark.unit`` (no Docker, no external services) is left alone —
   see ``pytest_collection_modifyitems`` below.

Fixtures that are specific to individual butler schemas remain in each butler's
own test_tools.py — only cross-cutting infrastructure lives here.
"""

from __future__ import annotations

import shutil

import pytest

_docker_available = shutil.which("docker") is not None

_DOCKER_SKIPIF = pytest.mark.skipif(
    not _docker_available,
    reason="Docker not available",
)
_INTEGRATION_MARK = pytest.mark.integration


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    """Auto-apply integration + docker-skip marks to unmarked roster/ tests.

    This runs after pytest collects all tests. Any test whose node-id path
    contains ``/roster/`` receives:
    - ``pytest.mark.integration``
    - ``pytest.mark.skipif(not docker_available, reason="Docker not available")``

    unless the test (or its class/module) already declares an explicit
    ``pytest.mark.unit`` — that declaration is the author's taxonomy call and
    must be respected, not silently overridden to ``integration``. Individual
    test files otherwise no longer need to declare these marks themselves.
    """
    for item in items:
        if "/roster/" in str(item.fspath):
            if item.get_closest_marker("unit") is not None:
                continue
            item.add_marker(_INTEGRATION_MARK, append=False)
            item.add_marker(_DOCKER_SKIPIF, append=False)
