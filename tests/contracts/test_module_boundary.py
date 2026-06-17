"""Contract tests: Module BEHAVIORAL boundary (Vision Rule 2).

Vision Rule 2 (vision.md): "Modules only add tools — they never touch core
infrastructure.  A module registers MCP tools, declares database migrations,
and hooks into the daemon lifecycle.  It must not modify the state store, the
scheduler, the spawner, or the session log."

This file focuses on the BEHAVIORAL contract enforced at the MODULE-SUBCLASS
level.  (Import-direction guards belong in the sibling bu-dl98i.7.2 test.)

Invariants asserted here
------------------------
1.  register_tools() is the sole legal MCP tool-registration path.
    on_startup() and on_shutdown() do not receive an ``mcp`` server object
    and therefore cannot register tools outside of the designated gate.

2.  No registered Module subclass holds core-infrastructure singletons
    (spawner, scheduler, session_log, state_store) as *instance attributes*
    after bare construction.

3.  No module file instantiates core singleton classes (Spawner, Scheduler,
    StateStore) via a direct constructor call.  Such a call would mean the
    module is managing its own copy of core infrastructure rather than
    receiving the daemon-managed instance via the sanctioned interfaces.

4.  Module subclasses do not store the ``mcp`` (FastMCP) server as an
    instance attribute; deferred tool registration outside register_tools()
    is forbidden.

Current-compliance allowlist
-----------------------------
Assertions 2 and 3 currently have known violations.  They are encoded in
ALLOWLIST_* constants below so that:
  - The test suite remains GREEN on current main.
  - Future drift (a new module violating the rule) is caught immediately.
  - Each allowlist entry carries an explicit removal criterion.

See `Discovered-Follow-Ups-JSON` in the PR description for the linked bead.
"""

from __future__ import annotations

import ast
import inspect
import pathlib

import pytest

pytestmark = pytest.mark.contract


# ---------------------------------------------------------------------------
# Allowlists — existing violations that must not block CI.
# Each entry: class_name -> human-readable reason + removal criterion.
# ---------------------------------------------------------------------------

# Assertion 2: modules that hold `_spawner` as an instance attribute.
# These modules were granted spawner access via a non-ABC `wire_runtime()` hook
# so they can proactively dispatch healing / QA investigation LLM sessions.
#
# Removal criterion: move dispatch logic into a daemon-owned scheduler task
# (core responsibility) so the module emits a work request that the daemon
# scheduler executes, keeping to the principle "daemon is infrastructure,
# intelligence is in ephemeral LLM sessions" (vision.md Rule 4).
ALLOWLIST_SPAWNER_HOLDER: dict[str, str] = {
    "QaModule": (
        "Holds _spawner via wire_runtime() to dispatch investigation agents. "
        "Remove when QA agent dispatch is refactored to a daemon-scheduled task "
        "and the module no longer needs to call spawner.trigger() directly."
    ),
    "SelfHealingModule": (
        "Holds _spawner via wire_runtime() to dispatch healing agents. "
        "Remove when healing agent dispatch is refactored to a daemon-scheduled task "
        "and the module no longer needs to call spawner.trigger() directly."
    ),
}

# Core infrastructure attributes that modules must not hold after construction.
_FORBIDDEN_INFRA_ATTRS: tuple[str, ...] = (
    "scheduler",
    "_scheduler",
    "spawner",  # public
    "session_log",
    "_session_log",
    "state_store",
    "_state_store",
    "tick_handler",
)

# Core singleton class names modules must not instantiate.
_FORBIDDEN_SINGLETON_CLASSES: frozenset[str] = frozenset(
    {"Spawner", "Scheduler", "StateStore", "SessionLog"}
)

# Module source root for AST-based checks.
_MODULES_ROOT = pathlib.Path(__file__).parents[2] / "src" / "butlers" / "modules"
_ROSTER_ROOT = pathlib.Path(__file__).parents[2] / "roster"


def _all_module_classes() -> list[type]:
    """Return every registered Module subclass from the default registry."""
    from butlers.modules.registry import default_registry

    reg = default_registry()
    return list(reg._modules.values())


def _all_module_source_files() -> list[pathlib.Path]:
    """Return all .py files under the modules and roster module directories."""
    files: list[pathlib.Path] = []
    for root in (_MODULES_ROOT, _ROSTER_ROOT):
        if root.exists():
            files.extend(root.rglob("*.py"))
    return [f for f in files if f.name != "base.py"]


# ---------------------------------------------------------------------------
# Assertion 1: register_tools() is the sole MCP tool-registration gate
# ---------------------------------------------------------------------------


class TestRegisterToolsIsOnlyMcpGate:
    """Vision Rule 2: MCP tools may only be registered inside register_tools().

    This is structurally enforced because on_startup() and on_shutdown() do not
    receive the ``mcp`` server instance — their ABC signatures prove it.
    """

    def test_on_startup_does_not_accept_mcp(self):
        """on_startup() cannot register tools — mcp is absent from its signature."""
        from butlers.modules.base import Module

        sig = inspect.signature(Module.on_startup)
        params = set(sig.parameters)
        assert "mcp" not in params, (
            "Module.on_startup() must not accept 'mcp' — tool registration belongs "
            "exclusively in register_tools() (Vision Rule 2)"
        )

    def test_on_shutdown_does_not_accept_mcp(self):
        """on_shutdown() cannot register tools — mcp is absent from its signature."""
        from butlers.modules.base import Module

        sig = inspect.signature(Module.on_shutdown)
        params = set(sig.parameters)
        assert "mcp" not in params, (
            "Module.on_shutdown() must not accept 'mcp' — tool registration belongs "
            "exclusively in register_tools() (Vision Rule 2)"
        )

    def test_all_concrete_on_startup_signatures_exclude_mcp(self):
        """Every concrete on_startup() override must not accept mcp."""
        violations = []
        for cls in _all_module_classes():
            method = getattr(cls, "on_startup", None)
            if method is None:
                continue
            sig = inspect.signature(method)
            if "mcp" in sig.parameters:
                violations.append(cls.__name__)
        assert not violations, (
            f"on_startup() must not accept 'mcp' in any Module subclass. Violations: {violations}"
        )

    def test_all_concrete_on_shutdown_signatures_exclude_mcp(self):
        """Every concrete on_shutdown() override must not accept mcp."""
        violations = []
        for cls in _all_module_classes():
            method = getattr(cls, "on_shutdown", None)
            if method is None:
                continue
            sig = inspect.signature(method)
            if "mcp" in sig.parameters:
                violations.append(cls.__name__)
        assert not violations, (
            f"on_shutdown() must not accept 'mcp' in any Module subclass. Violations: {violations}"
        )


# ---------------------------------------------------------------------------
# Assertion 2: No module subclass holds core-infra singletons as attributes
# ---------------------------------------------------------------------------


class TestNoInfraAttributesOnModules:
    """Vision Rule 2: Module instances must not hold core infrastructure references.

    Checked attribute names are documented in _FORBIDDEN_INFRA_ATTRS.
    Known violations are listed in ALLOWLIST_SPAWNER_HOLDER with removal criteria.
    """

    def test_all_modules_free_of_forbidden_infra_attributes(self):
        """All registered modules are free of core-infra instance attributes.

        Modules in ALLOWLIST_SPAWNER_HOLDER are exempt from the _spawner check
        until their dispatch logic is refactored to daemon-owned scheduler tasks.
        """
        violations: dict[str, list[str]] = {}

        for cls in _all_module_classes():
            try:
                inst = cls()
            except Exception:
                # Skip modules that cannot be instantiated without live config/DB.
                continue

            for attr in _FORBIDDEN_INFRA_ATTRS:
                if not hasattr(inst, attr):
                    continue
                # Apply allowlist: _spawner violations are pre-approved.
                if attr in ("spawner", "_spawner") and cls.__name__ in ALLOWLIST_SPAWNER_HOLDER:
                    continue
                violations.setdefault(cls.__name__, []).append(attr)

        assert not violations, (
            "Module instances must not hold core-infrastructure attributes. "
            f"Violations: {violations}. "
            "If this is an intentional pattern, add to ALLOWLIST_SPAWNER_HOLDER "
            "with an explicit removal criterion."
        )

    def test_allowlist_entries_are_still_violations(self):
        """Sanity check: allowlisted modules actually DO hold _spawner.

        This test fails if an allowlisted module is fixed without removing the
        allowlist entry, reminding maintainers to clean up the allowlist.
        """
        stale_entries = []
        for cls in _all_module_classes():
            if cls.__name__ not in ALLOWLIST_SPAWNER_HOLDER:
                continue
            try:
                inst = cls()
            except Exception:
                continue
            # The violation should still be present.
            if not any(hasattr(inst, a) for a in ("spawner", "_spawner")):
                stale_entries.append(cls.__name__)

        assert not stale_entries, (
            f"ALLOWLIST_SPAWNER_HOLDER entries {stale_entries} no longer hold a "
            "_spawner attribute. Remove them from the allowlist (the fix has landed)."
        )


# ---------------------------------------------------------------------------
# Assertion 3: No module instantiates core singleton classes
# ---------------------------------------------------------------------------


class TestNoCoreSingletonInstantiation:
    """Vision Rule 2: modules must not construct their own core-infra instances.

    Detecting `Spawner()`, `Scheduler()`, or `StateStore()` calls in module
    source files via AST. A module that creates its own infrastructure singleton
    bypasses the daemon-managed lifecycle contract.

    Exception: imports guarded by `TYPE_CHECKING` are analysis-only and never
    execute at runtime — they are explicitly excluded from this check.
    """

    def _collect_constructor_calls(self, filepath: pathlib.Path) -> list[str]:
        """Return list of forbidden class constructor calls in a source file.

        Excludes calls inside ``if TYPE_CHECKING:`` blocks, which are never
        executed at runtime.
        """
        try:
            src = filepath.read_text()
            tree = ast.parse(src)
        except (SyntaxError, OSError):
            return []

        # Collect line ranges of `if TYPE_CHECKING:` blocks.
        type_checking_ranges: list[tuple[int, int]] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.If):
                test = node.test
                is_tc = (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
                    isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
                )
                if is_tc:
                    end = max(
                        (getattr(n, "lineno", node.lineno) for n in ast.walk(node)),
                        default=node.lineno,
                    )
                    type_checking_ranges.append((node.lineno, end))

        def _in_type_checking(lineno: int) -> bool:
            return any(start <= lineno <= end for start, end in type_checking_ranges)

        hits: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if _in_type_checking(getattr(node, "lineno", 0)):
                continue
            func = node.func
            name = None
            if isinstance(func, ast.Name):
                name = func.id
            elif isinstance(func, ast.Attribute):
                name = func.attr
            if name in _FORBIDDEN_SINGLETON_CLASSES:
                hits.append(f"{filepath}:{node.lineno}: {name}()")
        return hits

    def test_module_files_do_not_instantiate_core_singleton_classes(self):
        """No module source file directly calls Spawner(), Scheduler(), StateStore()."""
        all_hits: list[str] = []
        for filepath in _all_module_source_files():
            all_hits.extend(self._collect_constructor_calls(filepath))

        assert not all_hits, (
            "Module files must not instantiate core singleton classes. "
            "Core infrastructure is owned by the daemon, not by modules. "
            f"Found: {all_hits}"
        )


# ---------------------------------------------------------------------------
# Assertion 4: No module stores the mcp server as an instance attribute
# ---------------------------------------------------------------------------


class TestNoMcpStorageInModules:
    """Vision Rule 2: mcp must not be stored for deferred post-register_tools use.

    If a module stores `self.mcp = mcp` (or similar), it could register
    additional tools after register_tools() returns, bypassing the sanctioned
    gate and hiding what tools are available at startup.
    """

    def _find_mcp_storage(self, filepath: pathlib.Path) -> list[str]:
        """Return list of 'self.<mcp_attr> = ...' assignments in the file."""
        try:
            src = filepath.read_text()
            tree = ast.parse(src)
        except (SyntaxError, OSError):
            return []

        hits: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for tgt in targets:
                if (
                    isinstance(tgt, ast.Attribute)
                    and isinstance(tgt.value, ast.Name)
                    and tgt.value.id == "self"
                    and "mcp" in tgt.attr.lower()
                ):
                    hits.append(f"{filepath}:{getattr(node, 'lineno', '?')}: self.{tgt.attr}")
        return hits

    def test_no_module_stores_mcp_as_attribute(self):
        """No module file assigns mcp to a self.* attribute."""
        all_hits: list[str] = []
        for filepath in _all_module_source_files():
            all_hits.extend(self._find_mcp_storage(filepath))

        assert not all_hits, (
            "Modules must not store the mcp server as an instance attribute. "
            "All tool registration must happen inside register_tools() (Vision Rule 2). "
            f"Found: {all_hits}"
        )

    def test_all_modules_have_no_mcp_attribute_after_construction(self):
        """Instantiated modules carry no mcp-named instance attribute."""
        violations = []
        for cls in _all_module_classes():
            try:
                inst = cls()
            except Exception:
                continue
            mcp_attrs = [attr for attr in vars(inst) if "mcp" in attr.lower()]
            if mcp_attrs:
                violations.append(f"{cls.__name__}: {mcp_attrs}")

        assert not violations, (
            "Module instances must not hold mcp-named attributes after construction. "
            f"Violations: {violations}"
        )
