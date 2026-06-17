"""Contract test: dependency direction — core and core_tools must not import modules.

Doctrine: about/craft-and-care/interfaces-and-dependencies.md
          about/heart-and-soul/architecture.md

Rule enforced:
    butlers.core.*       → butlers.modules.*   FORBIDDEN
    butlers.core_tools.* → butlers.modules.*   FORBIDDEN
    butlers.modules.*    → butlers.core.spawner FORBIDDEN

The module system is a plugin layer that sits ABOVE core. Modules "only add
tools" (architecture.md). Core importing from modules inverts that direction,
creating a hard coupling from core infrastructure to specific domain plugins.

Current-compliance: the analysis below found 10 known violations where core or
core_tools already imports from modules. Each is documented and allowlisted;
follow-up issues have been filed (see PR body). The test FAILS on any NEW
violation, guarding against further drift without making current CI red.

Clean boundary (no current violations):
    modules.* must NOT import butlers.core.spawner (the LLM CLI spawning engine).
    Spawner access belongs exclusively to core; no module should spawn sessions.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.contract

# ---------------------------------------------------------------------------
# Static-analysis helpers
# ---------------------------------------------------------------------------


def _type_checking_guarded_lines(tree: ast.Module) -> set[int]:
    """Return line numbers of every statement inside ``if TYPE_CHECKING:`` blocks.

    These imports are type-annotation-only (never executed at runtime) and
    should not be counted as real dependency edges.
    """
    guarded: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        is_tc = (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
            isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
        )
        if not is_tc:
            continue
        for stmt in node.body:
            for child in ast.walk(stmt):
                if hasattr(child, "lineno"):
                    guarded.add(child.lineno)
    return guarded


def _find_butlers_imports(source_path: Path) -> list[tuple[int, str]]:
    """Return (lineno, module) for every runtime butlers.* import in a file.

    Both ``from X import Y`` and ``import X`` forms are detected.
    ``from . import Y`` relative imports are skipped (no module attribute).
    Imports inside ``if TYPE_CHECKING:`` blocks are excluded — those are
    annotation-only and carry no runtime dependency edge.
    """
    try:
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    except SyntaxError:
        return []

    guarded = _type_checking_guarded_lines(tree)
    results: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.lineno in guarded:
                continue
            module = node.module or ""
            if module.startswith("butlers."):
                results.append((node.lineno, module))
        elif isinstance(node, ast.Import):
            if node.lineno in guarded:
                continue
            for alias in node.names:
                if alias.name.startswith("butlers."):
                    results.append((node.lineno, alias.name))
    return results


def _collect_package_imports(
    package_dir: Path,
    src_dir: Path,
    prefix_filter: str,
) -> list[tuple[str, int, str]]:
    """Walk ``package_dir``, returning (file_rel, lineno, module) for imports matching prefix.

    ``file_rel`` is the path relative to ``src_dir`` (e.g. ``butlers/core/spawner.py``).
    """
    hits: list[tuple[str, int, str]] = []
    for py_file in sorted(package_dir.rglob("*.py")):
        rel = str(py_file.relative_to(src_dir))
        for lineno, module in _find_butlers_imports(py_file):
            if module.startswith(prefix_filter):
                hits.append((rel, lineno, module))
    return hits


def _is_known_violation(
    file_rel: str,
    module: str,
    allowlist: frozenset[tuple[str, str]],
) -> bool:
    """Return True if (file_rel, module) matches any allowlist entry.

    Matching is prefix-based on the module: a violation is considered known if
    there is an allowlist entry ``(file_rel, known_prefix)`` where
    ``module == known_prefix`` OR ``module.startswith(known_prefix + ".")``.
    This lets a single allowlist entry cover an entire subpackage tree.
    """
    for known_file, known_prefix in allowlist:
        if file_rel == known_file and (
            module == known_prefix or module.startswith(known_prefix + ".")
        ):
            return True
    return False


def _find_new_violations(
    hits: list[tuple[str, int, str]],
    allowlist: frozenset[tuple[str, str]],
) -> list[tuple[str, int, str]]:
    """Filter ``hits`` to those not covered by ``allowlist``."""
    return [(f, ln, m) for f, ln, m in hits if not _is_known_violation(f, m, allowlist)]


# ---------------------------------------------------------------------------
# Known violations — filed as follow-up issues; allowlisted so CI stays green.
#
# Format: frozenset of (relative_file_path, module_prefix)
#   - relative_file_path is relative to src/ (e.g. "butlers/core/spawner.py")
#   - module_prefix is the butlers.modules.* module being imported (exact or parent)
#
# When a violation is fixed, remove its entry here.
# ---------------------------------------------------------------------------

# core/* importing modules.*
_CORE_IMPORTS_MODULES: frozenset[tuple[str, str]] = frozenset(
    {
        # corrections.py calls memory_forget() directly instead of accepting
        # an injectable callable. Should be refactored to remove the upward import.
        ("butlers/core/corrections.py", "butlers.modules.memory.tools.management"),
        # spawner.py inline-imports memory tools to inject pre-session context
        # (memory_context, embeddings, writing) before spawning the LLM CLI.
        # Should be refactored to accept an optional async pre-session hook.
        ("butlers/core/spawner.py", "butlers.modules.pipeline"),
        ("butlers/core/spawner.py", "butlers.modules.memory.tools"),
    }
)

# core_tools/* importing modules.*
_CORE_TOOLS_IMPORTS_MODULES: frozenset[tuple[str, str]] = frozenset(
    {
        # _routing.py imports _routing_ctx_var from the pipeline module at the
        # top level. Should be moved to a core-owned context variable.
        ("butlers/core_tools/_routing.py", "butlers.modules.pipeline"),
        # _routing.py inline-imports email_guard to apply approval checks before
        # routing. Should accept an optional approval-gate hook instead.
        ("butlers/core_tools/_routing.py", "butlers.modules.approvals.email_guard"),
        # _notifications.py inline-imports approval guard to check email sends.
        # Should accept an optional hook instead of knowing about the approvals module.
        ("butlers/core_tools/_notifications.py", "butlers.modules.approvals.email_guard"),
        ("butlers/core_tools/_notifications.py", "butlers.modules.approvals.models"),
        # _switchboard.py inline-imports pipeline and telegram for MessagePipeline,
        # _routing_ctx_var, and Telegram reaction constants.
        # Should be refactored to use core-owned abstractions.
        ("butlers/core_tools/_switchboard.py", "butlers.modules.pipeline"),
        ("butlers/core_tools/_switchboard.py", "butlers.modules.telegram"),
    }
)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _src_dir() -> Path:
    """Return the absolute path to the ``src/`` directory."""
    return Path(__file__).resolve().parents[2] / "src"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDependencyDirection:
    """Doctrine: module plugin layer must only depend on core, never the reverse.

    Rules:
      1. butlers.core.*       must NOT import butlers.modules.*
      2. butlers.core_tools.* must NOT import butlers.modules.*
      3. butlers.modules.*    must NOT import butlers.core.spawner
    """

    def test_core_does_not_import_modules(self) -> None:
        """butlers.core.* must NOT import from butlers.modules.*.

        The module layer sits above core. Core importing modules inverts the
        plugin model: it couples core infrastructure to domain capabilities,
        making it impossible to run core independently of all modules.

        Known violations (10 total across core + core_tools) are allowlisted
        with follow-up issues filed. Any NEW violation fails this test.
        """
        src = _src_dir()
        core_dir = src / "butlers" / "core"

        all_hits = _collect_package_imports(core_dir, src, "butlers.modules")
        new_violations = _find_new_violations(all_hits, _CORE_IMPORTS_MODULES)

        assert not new_violations, (
            "NEW core→modules violations detected (not in allowlist):\n"
            + "\n".join(f"  {f}:{ln}  imports '{m}'" for f, ln, m in new_violations)
            + "\n\nForbidden: butlers.core.* must not import butlers.modules.*\n"
            "Doctrine: about/craft-and-care/interfaces-and-dependencies.md\n"
            "To allowlist an intentional exception, add a (file, module_prefix) entry\n"
            "to _CORE_IMPORTS_MODULES and file a follow-up issue for cleanup."
        )

    def test_core_tools_does_not_import_modules(self) -> None:
        """butlers.core_tools.* must NOT import from butlers.modules.*.

        core_tools provides the standard MCP tool surface registered on every
        butler. If core_tools imports specific modules (approvals, pipeline,
        telegram), those modules become required even on butlers that never
        enable them, breaking opt-in composition.

        Known violations are allowlisted; any NEW violation fails this test.
        """
        src = _src_dir()
        core_tools_dir = src / "butlers" / "core_tools"

        all_hits = _collect_package_imports(core_tools_dir, src, "butlers.modules")
        new_violations = _find_new_violations(all_hits, _CORE_TOOLS_IMPORTS_MODULES)

        assert not new_violations, (
            "NEW core_tools→modules violations detected (not in allowlist):\n"
            + "\n".join(f"  {f}:{ln}  imports '{m}'" for f, ln, m in new_violations)
            + "\n\nForbidden: butlers.core_tools.* must not import butlers.modules.*\n"
            "Doctrine: about/craft-and-care/interfaces-and-dependencies.md\n"
            "To allowlist an intentional exception, add a (file, module_prefix) entry\n"
            "to _CORE_TOOLS_IMPORTS_MODULES and file a follow-up issue for cleanup."
        )

    def test_modules_do_not_import_spawner(self) -> None:
        """butlers.modules.* must NOT import from butlers.core.spawner.

        The spawner is the engine that creates ephemeral LLM CLI sessions.
        Module code has no business spawning sessions; that authority belongs
        exclusively to core. This boundary is currently clean; this test guards
        against future drift.

        No allowlist needed — zero violations on current main.
        """
        src = _src_dir()
        modules_dir = src / "butlers" / "modules"

        hits = _collect_package_imports(modules_dir, src, "butlers.core.spawner")

        assert not hits, (
            "modules→spawner violations detected:\n"
            + "\n".join(f"  {f}:{ln}  imports '{m}'" for f, ln, m in hits)
            + "\n\nForbidden: butlers.modules.* must not import butlers.core.spawner\n"
            "Spawner access belongs exclusively to core (architecture.md)."
        )

    def test_known_violations_allowlist_is_non_vacuous(self) -> None:
        """Canary: at least one known violation still exists in the scanned packages.

        This test fails if ALL known violations have been fixed without updating
        the allowlist. An empty allowlist that still finds zero violations is
        correct; an allowlist with entries that find zero violations means the
        allowlist has become stale dead weight and should be trimmed.

        If this test fails because you fixed all violations — congratulations!
        Empty both _CORE_IMPORTS_MODULES and _CORE_TOOLS_IMPORTS_MODULES and
        remove or update this canary.
        """
        src = _src_dir()
        core_dir = src / "butlers" / "core"
        core_tools_dir = src / "butlers" / "core_tools"

        core_hits = _collect_package_imports(core_dir, src, "butlers.modules")
        core_tools_hits = _collect_package_imports(core_tools_dir, src, "butlers.modules")
        total = core_hits + core_tools_hits

        if not _CORE_IMPORTS_MODULES and not _CORE_TOOLS_IMPORTS_MODULES:
            # Both allowlists emptied — presumably all violations were fixed.
            # The test is vacuous but intentionally so; mark as expected.
            pytest.skip("All allowlists are empty — violations have been fixed.")

        assert total, (
            "No core→modules or core_tools→modules imports found, but the allowlists\n"
            "are non-empty. This means all known violations were fixed. Please:\n"
            "  1. Empty _CORE_IMPORTS_MODULES and _CORE_TOOLS_IMPORTS_MODULES\n"
            "  2. Remove or update this canary test\n"
            "  3. Close the corresponding follow-up issues"
        )
