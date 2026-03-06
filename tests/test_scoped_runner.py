"""Tests for the scoped test runner."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from butlers.testing.changed_files import ChangedFiles
from butlers.testing.scoped_runner import (
    DEFAULT_IGNORES,
    FULL_SUITE_FALLBACK_ALLOWLIST,
    ScopedTestPlan,
    build_pytest_command,
    find_fallback_trigger,
    plan_scoped_tests,
)
from butlers.testing.source_test_map import FULL_SUITE

# ---------------------------------------------------------------------------
# FULL_SUITE_FALLBACK_ALLOWLIST
# ---------------------------------------------------------------------------


class TestFallbackAllowlist:
    def test_allowlist_is_tuple(self):
        assert isinstance(FULL_SUITE_FALLBACK_ALLOWLIST, tuple)

    def test_allowlist_contains_required_patterns(self):
        required = {
            "conftest.py",
            "tests/conftest.py",
            "src/butlers/core/",
            "src/butlers/modules/base.py",
            "src/butlers/modules/registry.py",
            "pyproject.toml",
            "migrations/",
        }
        assert required.issubset(set(FULL_SUITE_FALLBACK_ALLOWLIST))


# ---------------------------------------------------------------------------
# find_fallback_trigger
# ---------------------------------------------------------------------------


class TestFindFallbackTrigger:
    def test_no_match_returns_none(self):
        result = find_fallback_trigger(["src/butlers/modules/memory/tools/search.py"])
        assert result is None

    def test_exact_match_conftest(self):
        result = find_fallback_trigger(["conftest.py"])
        assert result is not None
        file, pattern = result
        assert file == "conftest.py"
        assert pattern == "conftest.py"

    def test_exact_match_tests_conftest(self):
        result = find_fallback_trigger(["tests/conftest.py"])
        assert result is not None
        file, pattern = result
        assert file == "tests/conftest.py"
        assert pattern == "tests/conftest.py"

    def test_prefix_match_core(self):
        result = find_fallback_trigger(["src/butlers/core/scheduler.py"])
        assert result is not None
        file, pattern = result
        assert file == "src/butlers/core/scheduler.py"
        assert pattern == "src/butlers/core/"

    def test_prefix_match_migrations(self):
        result = find_fallback_trigger(["migrations/v001_initial.py"])
        assert result is not None
        file, pattern = result
        assert file == "migrations/v001_initial.py"
        assert pattern == "migrations/"

    def test_exact_match_pyproject(self):
        result = find_fallback_trigger(["pyproject.toml"])
        assert result is not None
        file, pattern = result
        assert file == "pyproject.toml"
        assert pattern == "pyproject.toml"

    def test_exact_match_modules_base(self):
        result = find_fallback_trigger(["src/butlers/modules/base.py"])
        assert result is not None
        _, pattern = result
        assert pattern == "src/butlers/modules/base.py"

    def test_exact_match_modules_registry(self):
        result = find_fallback_trigger(["src/butlers/modules/registry.py"])
        assert result is not None
        _, pattern = result
        assert pattern == "src/butlers/modules/registry.py"

    def test_first_matching_file_returned(self):
        """Returns the first file that matches, not all of them."""
        result = find_fallback_trigger(["src/butlers/api/app.py", "src/butlers/core/scheduler.py"])
        assert result is not None
        file, _ = result
        assert file == "src/butlers/core/scheduler.py"

    def test_strips_leading_dot_slash(self):
        result = find_fallback_trigger(["./conftest.py"])
        assert result is not None

    def test_custom_allowlist(self):
        result = find_fallback_trigger(
            ["src/butlers/api/app.py"],
            allowlist=("src/butlers/api/",),
        )
        assert result is not None
        file, pattern = result
        assert file == "src/butlers/api/app.py"
        assert pattern == "src/butlers/api/"

    def test_empty_files(self):
        assert find_fallback_trigger([]) is None

    def test_empty_allowlist(self):
        assert find_fallback_trigger(["conftest.py"], allowlist=()) is None

    def test_prefix_dir_itself_matches(self):
        """A file path equal to the prefix dir (without trailing slash) matches."""
        result = find_fallback_trigger(["src/butlers/core"])
        assert result is not None


# ---------------------------------------------------------------------------
# plan_scoped_tests
# ---------------------------------------------------------------------------


class TestPlanScopedTests:
    def test_no_changed_files(self):
        with patch(
            "butlers.testing.scoped_runner.get_changed_files",
            return_value=ChangedFiles(files=[], base_ref="origin/main", head_ref="feat"),
        ):
            plan = plan_scoped_tests("feat")
        assert plan.scope == "none"
        assert plan.reason == "No files changed"
        assert plan.test_paths == []
        assert plan.changed_files == []

    def test_non_testable_files_only(self):
        with patch(
            "butlers.testing.scoped_runner.get_changed_files",
            return_value=ChangedFiles(
                files=["frontend/src/App.tsx", "docs/README.md"],
                base_ref="origin/main",
                head_ref="feat",
            ),
        ):
            plan = plan_scoped_tests("feat")
        assert plan.scope == "none"
        assert plan.changed_files == ["frontend/src/App.tsx", "docs/README.md"]

    def test_scoped_to_module(self):
        with patch(
            "butlers.testing.scoped_runner.get_changed_files",
            return_value=ChangedFiles(
                files=["src/butlers/modules/memory/tools/search.py"],
                base_ref="origin/main",
                head_ref="feat",
            ),
        ):
            plan = plan_scoped_tests("feat")
        assert plan.scope == "scoped"
        assert plan.test_paths == ["tests/modules/memory/"]
        assert plan.changed_files == ["src/butlers/modules/memory/tools/search.py"]

    def test_full_suite_via_source_map_trigger(self):
        """source_test_map FULL_SUITE_TRIGGERS (not in allowlist) still triggers full suite."""
        with patch(
            "butlers.testing.scoped_runner.get_changed_files",
            return_value=ChangedFiles(
                files=["src/butlers/testing/__init__.py"],
                base_ref="origin/main",
                head_ref="feat",
            ),
        ):
            plan = plan_scoped_tests("feat")
        assert plan.scope == "full"

    # --- Fallback allowlist triggers ---

    def test_fallback_conftest_triggers_full_suite(self):
        with patch(
            "butlers.testing.scoped_runner.get_changed_files",
            return_value=ChangedFiles(
                files=["conftest.py"],
                base_ref="origin/main",
                head_ref="feat",
            ),
        ):
            plan = plan_scoped_tests("feat")
        assert plan.scope == "full"
        assert plan.test_paths == FULL_SUITE
        assert "conftest.py" in plan.reason
        assert "conftest.py" in plan.reason  # triggering file in reason

    def test_fallback_tests_conftest_triggers_full_suite(self):
        with patch(
            "butlers.testing.scoped_runner.get_changed_files",
            return_value=ChangedFiles(
                files=["tests/conftest.py"],
                base_ref="origin/main",
                head_ref="feat",
            ),
        ):
            plan = plan_scoped_tests("feat")
        assert plan.scope == "full"
        assert "tests/conftest.py" in plan.reason

    def test_fallback_core_file_triggers_full_suite(self):
        with patch(
            "butlers.testing.scoped_runner.get_changed_files",
            return_value=ChangedFiles(
                files=["src/butlers/core/scheduler.py"],
                base_ref="origin/main",
                head_ref="feat",
            ),
        ):
            plan = plan_scoped_tests("feat")
        assert plan.scope == "full"
        assert plan.test_paths == FULL_SUITE
        assert "src/butlers/core/scheduler.py" in plan.reason
        assert "src/butlers/core/" in plan.reason

    def test_fallback_migrations_triggers_full_suite(self):
        with patch(
            "butlers.testing.scoped_runner.get_changed_files",
            return_value=ChangedFiles(
                files=["migrations/v001_initial.py"],
                base_ref="origin/main",
                head_ref="feat",
            ),
        ):
            plan = plan_scoped_tests("feat")
        assert plan.scope == "full"
        assert "migrations/v001_initial.py" in plan.reason
        assert "migrations/" in plan.reason

    def test_fallback_reason_names_file_and_pattern(self):
        """Reason must include both the triggering file and the matched pattern."""
        with patch(
            "butlers.testing.scoped_runner.get_changed_files",
            return_value=ChangedFiles(
                files=["src/butlers/core/daemon.py"],
                base_ref="origin/main",
                head_ref="feat",
            ),
        ):
            plan = plan_scoped_tests("feat")
        assert "src/butlers/core/daemon.py" in plan.reason
        assert "src/butlers/core/" in plan.reason

    def test_fallback_mixed_with_normal_files(self):
        """One allowlist match in a mixed set triggers full suite."""
        with patch(
            "butlers.testing.scoped_runner.get_changed_files",
            return_value=ChangedFiles(
                files=[
                    "src/butlers/modules/memory/tools/search.py",
                    "src/butlers/core/scheduler.py",
                ],
                base_ref="origin/main",
                head_ref="feat",
            ),
        ):
            plan = plan_scoped_tests("feat")
        assert plan.scope == "full"

    def test_custom_fallback_allowlist(self):
        """plan_scoped_tests accepts a custom fallback_allowlist."""
        with patch(
            "butlers.testing.scoped_runner.get_changed_files",
            return_value=ChangedFiles(
                files=["src/butlers/core/scheduler.py"],
                base_ref="origin/main",
                head_ref="feat",
            ),
        ):
            # Exclude core/ from allowlist — should fall through to scoped mapping
            plan = plan_scoped_tests("feat", fallback_allowlist=())
        assert plan.scope == "scoped"

    def test_multiple_modules_scoped(self):
        with patch(
            "butlers.testing.scoped_runner.get_changed_files",
            return_value=ChangedFiles(
                files=[
                    "src/butlers/modules/memory/tools/search.py",
                    "src/butlers/api/routers/memory.py",
                ],
                base_ref="origin/main",
                head_ref="feat",
            ),
        ):
            plan = plan_scoped_tests("feat")
        assert plan.scope == "scoped"
        assert "tests/modules/memory/" in plan.test_paths
        assert "tests/api/" in plan.test_paths

    def test_passes_base_and_repo_dir(self):
        with patch(
            "butlers.testing.scoped_runner.get_changed_files",
            return_value=ChangedFiles(files=[], base_ref="origin/dev", head_ref="feat"),
        ) as mock_gcf:
            plan_scoped_tests("feat", base="origin/dev", repo_dir="/tmp/repo")
        mock_gcf.assert_called_once_with("feat", "origin/dev", repo_dir="/tmp/repo")

    def test_frozen_dataclass(self):
        plan = ScopedTestPlan(scope="none", reason="test")
        with pytest.raises(AttributeError):
            plan.scope = "full"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# build_pytest_command
# ---------------------------------------------------------------------------


class TestBuildPytestCommand:
    def test_scoped_command(self):
        plan = ScopedTestPlan(
            scope="scoped",
            test_paths=["tests/api/", "tests/modules/memory/"],
            changed_files=["src/butlers/api/app.py"],
            reason="Scoped",
        )
        cmd = build_pytest_command(plan)
        assert cmd[:3] == ["uv", "run", "pytest"]
        assert "tests/api/" in cmd
        assert "tests/modules/memory/" in cmd
        for ignore in DEFAULT_IGNORES:
            assert ignore in cmd

    def test_full_suite_command(self):
        plan = ScopedTestPlan(
            scope="full",
            test_paths=["tests/"],
            changed_files=["conftest.py"],
            reason="Full suite",
        )
        cmd = build_pytest_command(plan)
        assert cmd[:3] == ["uv", "run", "pytest"]
        assert "tests/" in cmd

    def test_none_scope_raises(self):
        plan = ScopedTestPlan(scope="none", reason="No tests needed")
        with pytest.raises(ValueError, match="No tests to run"):
            build_pytest_command(plan)

    def test_custom_ignores(self):
        plan = ScopedTestPlan(
            scope="scoped",
            test_paths=["tests/api/"],
            changed_files=[],
            reason="test",
        )
        cmd = build_pytest_command(plan, ignores=["tests/slow/"])
        assert "--ignore" in cmd
        idx = cmd.index("--ignore")
        assert cmd[idx + 1] == "tests/slow/"
        assert "tests/test_db.py" not in cmd

    def test_extra_args(self):
        plan = ScopedTestPlan(
            scope="scoped",
            test_paths=["tests/api/"],
            changed_files=[],
            reason="test",
        )
        cmd = build_pytest_command(plan, extra_args=["-q", "--tb=short", "--maxfail=1"])
        assert "-q" in cmd
        assert "--tb=short" in cmd
        assert "--maxfail=1" in cmd

    def test_default_ignores_included(self):
        plan = ScopedTestPlan(
            scope="scoped",
            test_paths=["tests/api/"],
            changed_files=[],
            reason="test",
        )
        cmd = build_pytest_command(plan)
        for ignore in DEFAULT_IGNORES:
            assert ignore in cmd

    def test_command_order(self):
        """Test paths come before ignores, ignores before extra args."""
        plan = ScopedTestPlan(
            scope="scoped",
            test_paths=["tests/api/"],
            changed_files=[],
            reason="test",
        )
        cmd = build_pytest_command(plan, extra_args=["-v"])
        path_idx = cmd.index("tests/api/")
        ignore_idx = cmd.index("--ignore")
        v_idx = cmd.index("-v")
        assert path_idx < ignore_idx < v_idx


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------


class TestReportFormatting:
    def test_scoped_report_content(self):
        plan = ScopedTestPlan(
            scope="scoped",
            test_paths=["tests/api/", "tests/modules/memory/"],
            changed_files=["src/butlers/api/app.py", "src/butlers/modules/memory/tools/search.py"],
            reason="Scoped to 2 test path(s) from 2 changed file(s)",
        )
        report = plan.report()
        assert "SCOPED" in report
        assert "tests/api/" in report
        assert "tests/modules/memory/" in report
        assert "src/butlers/api/app.py" in report

    def test_full_suite_report(self):
        plan = ScopedTestPlan(
            scope="full",
            test_paths=["tests/"],
            changed_files=["conftest.py"],
            reason="Cross-cutting change detected",
        )
        report = plan.report()
        assert "FULL SUITE" in report

    def test_none_report(self):
        plan = ScopedTestPlan(scope="none", reason="No files changed")
        report = plan.report()
        assert "NO TESTS" in report

    def test_fallback_report_includes_reason(self):
        plan = ScopedTestPlan(
            scope="full",
            test_paths=["tests/"],
            changed_files=["src/butlers/core/scheduler.py"],
            reason=(
                "Full-suite fallback triggered by 'src/butlers/core/scheduler.py' "
                "(matches shared-infrastructure pattern 'src/butlers/core/')"
            ),
        )
        report = plan.report()
        assert "FULL SUITE" in report
        assert "src/butlers/core/scheduler.py" in report
