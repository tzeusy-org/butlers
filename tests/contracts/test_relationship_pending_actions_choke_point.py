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
# INSERTs; its PENDING branch calls park_pending_action. goal_park.py likewise
# INSERTs only the standing-rule auto-approved goal effect (RFC 0038 D13); its
# PENDING branch calls park_pending_action.
_ALLOWED_DIRECT_INSERT_FILES: frozenset[str] = frozenset(
    {
        "src/butlers/modules/approvals/gate.py",
        "src/butlers/modules/approvals/goal_park.py",
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
    """Retired senders cannot reappear or bypass durable admission anywhere."""
    violations: list[str] = []
    repo_root = _repo_root()
    for source_root in (repo_root / "src", repo_root / "roster"):
        for py_file in sorted(source_root.rglob("*.py")):
            if any(part in {"tests", "testing", "migrations"} for part in py_file.parts):
                continue
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names: set[str | None] = set()
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    names.add(node.name)
                elif isinstance(node, ast.Call):
                    called = node.func
                    if isinstance(called, ast.Name):
                        names.add(called.id)
                    elif isinstance(called, ast.Attribute):
                        names.add(called.attr)
                elif isinstance(node, ast.ImportFrom):
                    names.update(alias.name for alias in node.names)
                if names & {"emit_approval_push", "reserve_approval_push"}:
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
    ("src/butlers/modules/approvals/goal_park.py", "_abandon_unexecuted"): 1,
}


def _string_value(node: ast.AST) -> str | None:
    """Static text of a str literal, f-string, or ``+`` chain of them."""
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.JoinedStr):
        return "".join(
            part.value
            for part in node.values
            if isinstance(part, ast.Constant) and isinstance(part.value, str)
        )
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _string_value(node.left), _string_value(node.right)
        if left is not None or right is not None:
            return (left or "") + (right or "")
    return None


def _status_guards(call: ast.Call) -> set[str]:
    """``ActionStatus.<X>.value`` members bound anywhere in one call."""
    return {
        node.value.attr
        for node in ast.walk(call)
        if isinstance(node, ast.Attribute)
        and node.attr == "value"
        and isinstance(node.value, ast.Attribute)
    }


def _status_writers(rel: str, source: str) -> tuple[dict[tuple[str, str], int], list[str]]:
    """Return (status-writer inventory, violations) for one source file.

    Writers are found by their SQL text wherever it sits (call argument,
    keyword, variable, f-string or ``+`` chain) and attributed to the
    enclosing function, so the inventory check sees every shape.
    """
    inventory: dict[tuple[str, str], int] = {}
    violations: list[str] = []
    tree = ast.parse(source)
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}

    def _enclosing(node: ast.AST, kinds: tuple[type, ...]) -> ast.AST | None:
        while node in parents:
            node = parents[node]
            if isinstance(node, kinds):
                return node
        return None

    for node in ast.walk(tree):
        parent = parents.get(node)
        if isinstance(parent, ast.JoinedStr) or (
            isinstance(parent, ast.BinOp)
            and isinstance(parent.op, ast.Add)
            and _string_value(parent) is not None
        ):
            continue  # counted once at the outermost string expression
        sql = _string_value(node)
        if not sql or not _PENDING_ACTIONS_UPDATE.search(sql) or not _SETS_STATUS.search(sql):
            continue
        function_node = _enclosing(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        function = getattr(function_node, "name", "<module>")
        key = (rel, function)
        inventory[key] = inventory.get(key, 0) + 1
        if key == _CANONICAL_TERMINAL_WRITER:
            continue
        # The consuming call either holds the SQL directly or a name bound to it.
        call = _enclosing(node, (ast.Call,))
        assigned = parents.get(node)
        if isinstance(assigned, ast.Assign) and function_node is not None:
            names = {t.id for t in assigned.targets if isinstance(t, ast.Name)}
            call = next(
                (
                    candidate
                    for candidate in ast.walk(function_node)
                    if isinstance(candidate, ast.Call)
                    and any(
                        isinstance(arg, ast.Name) and arg.id in names
                        for arg in [*candidate.args, *(k.value for k in candidate.keywords)]
                    )
                ),
                None,
            )
        guards = _status_guards(call) if call is not None else set()
        if (
            "pending" in sql.lower().split("where", 1)[-1]
            or "APPROVED" not in guards
            or not guards & {"EXECUTED", "ABANDONED"}
        ):
            violations.append(f"{rel}:{node.lineno} ({function})")
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

    planted = {
        "direct": (
            "async def expire_quietly(conn, action_id):\n"
            "    await conn.execute(\n"
            "        \"UPDATE relationship.pending_actions SET status = 'expired' \"\n"
            "        \"WHERE id = $1 AND status = 'pending'\",\n"
            "        action_id,\n"
            "    )\n"
        ),
        "variable": (
            "async def expire_quietly(conn, action_id):\n"
            "    sql = f\"UPDATE pending_actions SET status = 'expired' WHERE id = $1\"\n"
            "    await conn.execute(sql, action_id)\n"
        ),
        "keyword": (
            "async def expire_quietly(conn, action_id):\n"
            "    await run(\n"
            '        query="UPDATE pending_actions SET status = $1 " + "WHERE id = $2",\n'
            "        args=(ActionStatus.APPROVED.value, action_id),\n"
            "    )\n"
        ),
    }
    for shape, text in planted.items():
        found, bad = _status_writers("roster/relationship/planted.py", text)
        assert found == {("roster/relationship/planted.py", "expire_quietly"): 1}, shape
        assert len(bad) == 1 and bad[0].endswith("(expire_quietly)"), shape
    non_status = (
        "async def defer(conn):\n"
        '    await conn.execute("UPDATE pending_actions SET expires_at = $1 "\n'
        "                       \"WHERE id = $2 AND status = 'pending'\")\n"
    )
    assert _status_writers("src/x.py", non_status) == ({}, [])
