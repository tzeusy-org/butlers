"""Tests for the dynamic butler tools loader."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ROSTER_ROOT = REPO_ROOT / "roster"


def test_butler_tools_importable_and_discovery():
    """Core tools importable; imports register modules; discovered butler modules are present."""
    from butlers.tools._loader import _discover_butler_names, register_all_butler_tools
    from butlers.tools.general import collection_create
    from butlers.tools.health import measurement_log
    from butlers.tools.relationship import contact_create
    from butlers.tools.switchboard import register_butler

    assert all(
        callable(f) for f in [register_butler, collection_create, measurement_log, contact_create]
    )

    discovered = _discover_butler_names(ROSTER_ROOT)
    assert len(discovered) > 0
    for name in discovered:
        module_name = f"butlers.tools.{name}"
        assert module_name in sys.modules
        mod = sys.modules[module_name]
        expected_single = ROSTER_ROOT / name / "tools.py"
        expected_package = ROSTER_ROOT / name / "tools" / "__init__.py"
        assert Path(mod.__file__).resolve() in (
            expected_single.resolve(),
            expected_package.resolve(),
        )

    register_all_butler_tools()  # idempotent
    register_all_butler_tools()


def test_missing_tools_file_raises_and_shared_tools():
    """Missing tools.py raises FileNotFoundError; shared tools importable from src/."""
    import tempfile

    import butlers.tools.extraction as ext_mod
    import butlers.tools.extraction_queue as queue_mod
    from butlers.tools._loader import register_butler_tools
    from butlers.tools.extraction import ExtractorSchema, route
    from butlers.tools.extraction_queue import extraction_queue_add

    assert callable(route) and callable(extraction_queue_add) and ExtractorSchema is not None
    assert "src/butlers/tools/extraction.py" in ext_mod.__file__
    assert "src/butlers/tools/extraction_queue.py" in queue_mod.__file__

    with tempfile.TemporaryDirectory() as tmp:
        empty_dir = Path(tmp) / "empty_butler"
        empty_dir.mkdir()
        with pytest.raises(FileNotFoundError):
            register_butler_tools("empty_butler", empty_dir)
