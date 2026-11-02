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
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.contract

# park.py contains the one shared pending INSERT used by ordinary and prepared
# transactional admission. gate.py contains only owner/rule auto-approved
# INSERTs; its PENDING branch calls park_pending_action.
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


# Spec: REQ-approval-delivery-intent-recovery-008; scans production source for admissions bypassing the shared helper.
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


# Spec: REQ-approval-delivery-intent-recovery-008; the gate exception permits only explicitly auto-approved SQL inserts.
def test_gate_direct_inserts_are_only_explicit_auto_approvals() -> None:
    """The gate allowlist cannot hide a future direct PENDING insertion."""
    gate_path = _repo_root() / "src" / "butlers" / "modules" / "approvals" / "gate.py"
    tree = ast.parse(gate_path.read_text(encoding="utf-8"))
    inserts: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        # The rule-claim INSERT is built as ``"WITH claimed AS (" + sql + ") INSERT ..."``,
        # so look at every string constant inside the first argument, not just a literal.
        if any(
            isinstance(part, ast.Constant)
            and isinstance(part.value, str)
            and "insert into pending_actions" in part.value.lower()
            for part in ast.walk(node.args[0])
        ):
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


def test_atomic_helper_owns_exactly_one_pending_insert() -> None:
    """Ordinary and prepared admission share one pending-row writer."""
    helper_path = _repo_root() / "src/butlers/modules/approvals/park.py"
    tree = ast.parse(helper_path.read_text(encoding="utf-8"))
    inserts = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and "insert into pending_actions" in node.value.lower()
    ]
    assert len(inserts) == 1


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


def test_every_atomic_park_producer_supplies_origin() -> None:
    """No producer may defer owning-schema attribution to the parking helper."""
    violations: list[str] = []
    repo_root = _repo_root()
    for source_root in (repo_root / "src", repo_root / "roster"):
        for py_file in sorted(source_root.rglob("*.py")):
            if any(part in {"tests", "testing", "migrations"} for part in py_file.parts):
                continue
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                called = node.func
                name = called.id if isinstance(called, ast.Name) else None
                if isinstance(called, ast.Attribute):
                    name = called.attr
                if name not in {"park_pending_action", "park_prepared_action"}:
                    continue
                if py_file.name == "approvals_hooks.py" and isinstance(called, ast.Attribute):
                    continue
                if not any(keyword.arg == "origin_butler" for keyword in node.keywords):
                    violations.append(f"{py_file.relative_to(repo_root)}:{node.lineno}")
    assert not violations, "Atomic park producer omitted origin_butler:\n" + "\n".join(violations)


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


_PENDING_ACTIONS_UPDATE = re.compile(r"\bupdate\s+(?:\w+\.)?pending_actions\b", re.IGNORECASE)
_SETS_STATUS = re.compile(r"\bset\b(?:(?!\bwhere\b).)*?\bstatus\s*=", re.IGNORECASE | re.DOTALL)
# The one canonical pending-to-terminal writer. Every decision, expiry and
# rejection path reaches it so presentation cancellation and cohort eligibility
# commit with the status change.
_CANONICAL_TERMINAL_WRITER = (
    "src/butlers/modules/approvals/delivery_lifecycle.py",
    "transition_pending_action",
)
# Complete current inventory of production status writers on pending_actions.
# Only the canonical helper may leave ``pending``; the others move an already
# approved action to executed/abandoned and must bind APPROVED as their guard.
_PENDING_ACTION_STATUS_WRITERS: dict[tuple[str, str], int] = {
    _CANONICAL_TERMINAL_WRITER: 1,
    ("src/butlers/modules/approvals/executor.py", "execute_approved_action"): 1,
    ("src/butlers/modules/approvals/operations.py", "mark_executed"): 1,
    ("src/butlers/modules/approvals/operations.py", "abandon_approved_action"): 1,
}


def _status_writers(rel: str, source: str) -> tuple[dict[tuple[str, str], int], list[str]]:
    """Return (status-writer inventory, violations) for one source file."""
    inventory: dict[tuple[str, str], int] = {}
    violations: list[str] = []
    tree = ast.parse(source)

    def _visit(node: ast.AST, function: str) -> None:
        for child in ast.iter_child_nodes(node):
            name = function
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                name = child.name
            if isinstance(child, ast.Call) and child.args:
                sql = " ".join(
                    part.value
                    for part in ast.walk(child.args[0])
                    if isinstance(part, ast.Constant) and isinstance(part.value, str)
                )
                if _PENDING_ACTIONS_UPDATE.search(sql) and _SETS_STATUS.search(sql):
                    key = (rel, function)
                    inventory[key] = inventory.get(key, 0) + 1
                    guard = {
                        arg.value.attr
                        for arg in child.args[1:]
                        if isinstance(arg, ast.Attribute)
                        and arg.attr == "value"
                        and isinstance(arg.value, ast.Attribute)
                    }
                    if key != _CANONICAL_TERMINAL_WRITER and (
                        "pending" in sql.lower().split("where", 1)[-1]
                        or "APPROVED" not in guard
                        or not guard & {"EXECUTED", "ABANDONED"}
                    ):
                        violations.append(f"{rel}:{child.lineno} ({function})")
            _visit(child, name)

    _visit(tree, "<module>")
    return inventory, violations


# Spec: REQ-approval-delivery-intent-recovery-005, REQ-module-approvals-002; only the canonical helper may move an action out of pending, so cancellation commits with every decision.
def test_pending_to_terminal_writers_are_the_canonical_inventory() -> None:
    repo_root = _repo_root()
    inventory: dict[tuple[str, str], int] = {}
    violations: list[str] = []
    for source_root in (repo_root / "src", repo_root / "roster"):
        for py_file in sorted(source_root.rglob("*.py")):
            if any(part in {"tests", "testing", "migrations"} for part in py_file.parts):
                continue
            rel = str(py_file.relative_to(repo_root))
            found, bad = _status_writers(rel, py_file.read_text(encoding="utf-8"))
            inventory.update(found)
            violations.extend(bad)
    assert not violations, "pending_actions status writer bypasses the canonical helper:\n" + (
        "\n".join(violations)
    )
    assert inventory == _PENDING_ACTION_STATUS_WRITERS

    planted = (
        "async def expire_quietly(conn, action_id):\n"
        "    await conn.execute(\n"
        "        \"UPDATE relationship.pending_actions SET status = 'expired' \"\n"
        "        \"WHERE id = $1 AND status = 'pending'\",\n"
        "        action_id,\n"
        "    )\n"
    )
    found, bad = _status_writers("roster/relationship/planted.py", planted)
    assert found == {("roster/relationship/planted.py", "expire_quietly"): 1}
    assert bad == ["roster/relationship/planted.py:2 (expire_quietly)"]
    non_status = (
        "async def defer(conn):\n"
        '    await conn.execute("UPDATE pending_actions SET expires_at = $1 "\n'
        "                       \"WHERE id = $2 AND status = 'pending'\")\n"
    )
    assert _status_writers("src/x.py", non_status) == ({}, [])
