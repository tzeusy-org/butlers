"""Contract test: every production PENDING park routes through one choke point.

bu-g27ib: an audit found five ``INSERT INTO pending_actions ... status='pending'``
call sites inside ``roster/relationship/`` that bypassed
``butlers.core.approvals_hooks.park_pending_action`` (the single choke point
established by bu-mda0r), so the owner was never pushed for those curation
proposals. All five now route through the choke point (via
``butlers.core.approvals_hooks.park_pending_action`` or, transitively, the
relationship library's own ``_create_pending_action`` helper, which itself
calls the choke point).

This guards the complete source tree: a new direct ``INSERT INTO
pending_actions`` outside the atomic admission helper would create an action
without its durable delivery intent. The only other production SQL is the
approval gate's explicitly auto-approved path.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.contract

# The atomic helper also contains the explicitly digest-only prepared-action
# exception. gate.py contains only owner/rule auto-approved INSERTs; its PENDING
# branch calls park_pending_action.
_ALLOWED_DIRECT_INSERT_FILES: frozenset[str] = frozenset(
    {
        "src/butlers/modules/approvals/gate.py",
        "src/butlers/modules/approvals/park.py",
    }
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _relationship_dir() -> Path:
    return _repo_root() / "roster" / "relationship"


def test_production_tree_has_no_direct_pending_actions_insert() -> None:
    """No production file may bypass atomic pending-action admission.

    Every PENDING park must go through
    ``butlers.core.approvals_hooks.park_pending_action`` (or the relationship
    library's ``_create_pending_action`` helper, which delegates to it) so the
    owner-facing push is never silently skipped.
    """
    repo_root = _repo_root()
    violations: list[str] = []
    for source_root in (repo_root / "src", repo_root / "roster"):
        for py_file in sorted(source_root.rglob("*.py")):
            rel = str(py_file.relative_to(repo_root))
            if rel in _ALLOWED_DIRECT_INSERT_FILES or any(
                part in {"tests", "testing"} for part in py_file.parts
            ):
                continue
            if "migrations" in py_file.parts:
                continue
            if "insert into pending_actions" in py_file.read_text(encoding="utf-8").lower():
                violations.append(rel)

    assert not violations, (
        "Direct 'INSERT INTO pending_actions' found outside the approvals "
        "atomic admission choke point (bu-umii8n.1):\n"
        + "\n".join(f"  {f}" for f in violations)
        + "\n\nRoute the park through butlers.core.approvals_hooks.park_pending_action "
        "instead of inserting directly, so a pending action cannot commit without "
        "its delivery intent."
    )


def test_gate_direct_inserts_are_only_explicit_auto_approvals() -> None:
    """The gate allowlist cannot hide a future direct PENDING insertion."""
    gate_path = _repo_root() / "src" / "butlers" / "modules" / "approvals" / "gate.py"
    tree = ast.parse(gate_path.read_text(encoding="utf-8"))
    inserts: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        sql = node.args[0]
        if isinstance(sql, ast.Constant) and isinstance(sql.value, str):
            if "insert into pending_actions" in sql.value.lower():
                inserts.append(node)
    assert len(inserts) == 2
    for call in inserts:
        assert any(
            isinstance(arg, ast.Attribute)
            and arg.attr == "value"
            and isinstance(arg.value, ast.Attribute)
            and arg.value.attr == "APPROVED"
            for arg in call.args[1:]
        ), "Every direct gate INSERT must bind ActionStatus.APPROVED.value"


def test_production_parking_never_calls_legacy_push_writer() -> None:
    """The additive admission rollout leaves approval_push_emissions read-only."""
    violations: list[str] = []
    repo_root = _repo_root()
    for source_root in (repo_root / "src", repo_root / "roster"):
        for py_file in sorted(source_root.rglob("*.py")):
            if any(part in {"tests", "testing", "migrations"} for part in py_file.parts):
                continue
            if py_file.name == "notifications.py":
                continue
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                called = node.func
                name = called.id if isinstance(called, ast.Name) else None
                if isinstance(called, ast.Attribute):
                    name = called.attr
                if name == "emit_approval_push":
                    violations.append(f"{py_file.relative_to(repo_root)}:{node.lineno}")
    assert not violations, "Legacy approval push writer called from production:\n" + "\n".join(
        violations
    )


def test_relationship_library_helper_delegates_to_choke_point() -> None:
    """The shared relationship helper must call the core choke point, not raw SQL."""
    helper_path = _relationship_dir() / "tools" / "relationship_assert_fact.py"
    jobs_path = _relationship_dir() / "jobs" / "relationship_jobs.py"
    assert helper_path.is_file()
    assert jobs_path.is_file()

    for path in (helper_path, jobs_path):
        text = path.read_text(encoding="utf-8")
        assert "from butlers.core.approvals_hooks import park_pending_action" in text, (
            f"{path} must import the approvals choke point "
            "(butlers.core.approvals_hooks.park_pending_action) to park PENDING actions."
        )
        assert "park_pending_action(" in text
