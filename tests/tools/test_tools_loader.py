"""Tests for the dynamic butler tools loader."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ROSTER_ROOT = REPO_ROOT / "roster"


def test_butler_tools_importable():
    """Core butler tools from each domain are importable after registration."""
    from butlers.tools.switchboard import register_butler
    from butlers.tools.general import collection_create
    from butlers.tools.health import measurement_log
    from butlers.tools.relationship import contact_create

    assert all(callable(f) for f in [register_butler, collection_create, measurement_log, contact_create])


def test_discovered_modules_in_sys_modules():
    """All discovered butler tool modules should be registered in sys.modules."""
    from butlers.tools._loader import _discover_butler_names

    discovered = _discover_butler_names(ROSTER_ROOT)
    assert len(discovered) > 0, "Should discover at least one butler"
    for name in discovered:
        module_name = f"butlers.tools.{name}"
        assert module_name in sys.modules, f"{module_name} not in sys.modules"


def test_tools_loaded_from_roster_dirs():
    """Tool modules should have __file__ pointing to butler config dirs."""
    from butlers.tools._loader import _discover_butler_names

    discovered = _discover_butler_names(ROSTER_ROOT)
    for name in discovered:
        mod = sys.modules[f"butlers.tools.{name}"]
        expected_single = ROSTER_ROOT / name / "tools.py"
        expected_package = ROSTER_ROOT / name / "tools" / "__init__.py"
        mod_path = Path(mod.__file__).resolve()
        assert mod_path in (expected_single.resolve(), expected_package.resolve())


def test_idempotent_registration():
    """Calling register_all_butler_tools multiple times should not error."""
    from butlers.tools._loader import register_all_butler_tools

    register_all_butler_tools()
    register_all_butler_tools()


def test_missing_tools_file_raises():
    """Butlers without tools.py raise FileNotFoundError."""
    from butlers.tools._loader import register_butler_tools

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        empty_dir = Path(tmp) / "empty_butler"
        empty_dir.mkdir()
        with pytest.raises(FileNotFoundError):
            register_butler_tools("empty_butler", empty_dir)


def test_shared_tools_importable():
    """Shared tools (extraction, extraction_queue) are importable from src/."""
    from butlers.tools.extraction import ExtractorSchema, route
    from butlers.tools.extraction_queue import extraction_queue_add
    import butlers.tools.extraction as ext_mod
    import butlers.tools.extraction_queue as queue_mod

    assert ExtractorSchema is not None
    assert callable(route)
    assert callable(extraction_queue_add)
    assert "src/butlers/tools/extraction.py" in ext_mod.__file__
    assert "src/butlers/tools/extraction_queue.py" in queue_mod.__file__
