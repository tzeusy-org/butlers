"""Smoke tests to verify the project skeleton is wired up correctly."""

import pytest

pytestmark = pytest.mark.unit


def test_version():
    """Package exposes a non-empty version string."""
    import butlers

    assert isinstance(butlers.__version__, str)
    assert butlers.__version__  # non-empty
