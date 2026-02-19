"""Tests for the dynamic butler tools loader."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
# Repo root (3 levels up from tests/tools/test_tools_loader.py)
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ROSTER_ROOT = REPO_ROOT / "roster"


class TestRegisterButlerTools:
    """Tests for register_butler_tools."""

    def test_loads_switchboard_tools(self):
        """Switchboard tools should be importable after registration."""
        from butlers.tools.switchboard import register_butler

        assert callable(register_butler)

    def test_loads_general_tools(self):
        """General tools should be importable after registration."""
        from butlers.tools.general import collection_create

        assert callable(collection_create)

    def test_loads_health_tools(self):
        """Health tools should be importable after registration."""
        from butlers.tools.health import measurement_log

        assert callable(measurement_log)

    def test_loads_relationship_tools(self):
        """Relationship tools should be importable after registration."""
        from butlers.tools.relationship import contact_create

        assert callable(contact_create)

    def test_modules_registered_in_sys_modules(self):
        """All discovered butler tool modules should be in sys.modules."""
        from butlers.tools._loader import _discover_butler_names

        discovered = _discover_butler_names(ROSTER_ROOT)
        assert len(discovered) > 0, "Should discover at least one butler"
        for name in discovered:
            module_name = f"butlers.tools.{name}"
            assert module_name in sys.modules, f"{module_name} not in sys.modules"

    def test_tools_loaded_from_config_dirs(self):
        """Tool modules should have __file__ pointing to butler config dirs."""
        from butlers.tools._loader import _discover_butler_names

        discovered = _discover_butler_names(ROSTER_ROOT)
        assert len(discovered) > 0, "Should discover at least one butler"
        for name in discovered:
            module_name = f"butlers.tools.{name}"
            mod = sys.modules[module_name]
            # Support both tools.py (single file) and tools/__init__.py (package)
            expected_single = ROSTER_ROOT / name / "tools.py"
            expected_package = ROSTER_ROOT / name / "tools" / "__init__.py"
            mod_path = Path(mod.__file__).resolve()
            assert mod_path in (expected_single.resolve(), expected_package.resolve()), (
                f"{module_name}.__file__ = {mod.__file__}, "
                f"expected {expected_single} or {expected_package}"
            )


class TestRegisterAllButlerTools:
    """Tests for register_all_butler_tools."""

    def test_idempotent_registration(self):
        """Calling register_all_butler_tools multiple times should not error."""
        from butlers.tools._loader import register_all_butler_tools

        # Should not raise
        register_all_butler_tools()
        register_all_butler_tools()

    def test_auto_detect_repo_root(self):
        """register_all_butler_tools should auto-detect repo root."""
        from butlers.tools._loader import register_all_butler_tools

        # This is called by __init__.py already; just verify it works
        register_all_butler_tools(roster_root=None)

    def test_explicit_roster_root(self):
        """register_all_butler_tools should work with explicit path."""
        from butlers.tools._loader import register_all_butler_tools

        register_all_butler_tools(roster_root=ROSTER_ROOT)

    def test_missing_tools_file_skipped(self, tmp_path):
        """Butlers without tools.py should be silently skipped."""
        from butlers.tools._loader import register_butler_tools

        empty_dir = tmp_path / "empty_butler"
        empty_dir.mkdir()

        with pytest.raises(FileNotFoundError):
            register_butler_tools("empty_butler", empty_dir)


class TestSharedToolsUnaffected:
    """Verify shared tools still work from their original location."""

    def test_extraction_importable(self):
        """extraction module should still be importable normally."""
        from butlers.tools.extraction import ExtractorSchema

        assert ExtractorSchema is not None

    def test_extraction_queue_importable(self):
        """extraction_queue module should still be importable normally."""
        from butlers.tools.extraction_queue import extraction_queue_add

        assert callable(extraction_queue_add)

    def test_extraction_file_in_src(self):
        """extraction.py should still live in src/butlers/tools/."""
        import butlers.tools.extraction as mod

        assert "src/butlers/tools/extraction.py" in mod.__file__

    def test_extraction_queue_file_in_src(self):
        """extraction_queue.py should still live in src/butlers/tools/."""
        import butlers.tools.extraction_queue as mod

        assert "src/butlers/tools/extraction_queue.py" in mod.__file__


class TestCrossModuleImports:
    """Verify cross-module imports work (extraction -> switchboard)."""

    def test_extraction_can_import_switchboard_route(self):
        """extraction.py imports route from switchboard, which should still work."""
        from butlers.tools.extraction import route

        assert callable(route)
