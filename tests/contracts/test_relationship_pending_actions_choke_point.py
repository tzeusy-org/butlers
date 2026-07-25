"""Contract test: relationship butler PENDING parks route through one choke point.

bu-g27ib: an audit found five ``INSERT INTO pending_actions ... status='pending'``
call sites inside ``roster/relationship/`` that bypassed
``butlers.core.approvals_hooks.park_pending_action`` (the single choke point
established by bu-mda0r), so the owner was never pushed for those curation
proposals. All five now route through the choke point (via
``butlers.core.approvals_hooks.park_pending_action`` or, transitively, the
relationship library's own ``_create_pending_action`` helper, which itself
calls the choke point).

This guards against a regression: a new direct ``INSERT INTO pending_actions``
statement anywhere under ``roster/relationship/`` (outside this test's
allowlist) reintroduces a silent owner-notification bypass.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.contract

# The ONE sanctioned direct INSERT: modules.approvals.park.park_pending_action
# itself, which every relationship park site must route through instead of
# writing pending_actions directly. Relative to the repo root.
_ALLOWED_DIRECT_INSERT_FILES: frozenset[str] = frozenset(
    {
        "src/butlers/modules/approvals/park.py",
    }
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _relationship_dir() -> Path:
    return _repo_root() / "roster" / "relationship"


def test_relationship_has_no_direct_pending_actions_insert() -> None:
    """No file under roster/relationship/ may INSERT INTO pending_actions directly.

    Every PENDING park must go through
    ``butlers.core.approvals_hooks.park_pending_action`` (or the relationship
    library's ``_create_pending_action`` helper, which delegates to it) so the
    owner-facing push is never silently skipped.
    """
    repo_root = _repo_root()
    relationship_dir = _relationship_dir()
    assert relationship_dir.is_dir(), f"expected {relationship_dir} to exist"

    violations: list[str] = []
    for py_file in sorted(relationship_dir.rglob("*.py")):
        rel = str(py_file.relative_to(repo_root))
        if rel in _ALLOWED_DIRECT_INSERT_FILES:
            continue
        # Test fixtures legitimately seed rows with raw SQL (e.g. to set up an
        # "already pending" dedup scenario) -- this guard is about production
        # park sites, not test setup.
        if py_file.relative_to(relationship_dir).parts[0] == "tests":
            continue
        text = py_file.read_text(encoding="utf-8")
        # Match the statement loosely (whitespace/newlines vary across call
        # sites) rather than the exact multi-line literal used elsewhere.
        if "insert into pending_actions" in text.lower():
            violations.append(rel)

    assert not violations, (
        "Direct 'INSERT INTO pending_actions' found outside the approvals "
        "choke point (bu-mda0r/bu-g27ib):\n"
        + "\n".join(f"  {f}" for f in violations)
        + "\n\nRoute the park through butlers.core.approvals_hooks.park_pending_action "
        "instead of inserting directly, so the owner-facing push cannot be silently "
        "skipped."
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
