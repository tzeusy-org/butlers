# source-guard: synthetic-changes-tree registry contains synthetic snippets and matcher text only
"""S1a structural-rule registry, not a general lint or test-deletion framework.

The three initial rules ratchet source sites; they do not assert AC1/AC2 are
complete. Their existing offenders remain for the packet's subsequent slices.
"""

from __future__ import annotations

import ast
import hashlib
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from butlers.testing.source_guard import (
    Violation,
    import_edges,
    parse_file,
    pragma_declaration,
    python_files,
    strip_docstrings,
)


@dataclass(frozen=True)
class SourceFile:
    path: Path  # repository-relative for diagnostics and ratchet identity
    text: str
    _absolute_path: Path | None = None

    @cached_property
    def tree(self) -> ast.Module:
        if self._absolute_path is None:
            return ast.parse(self.text, filename=str(self.path))
        if self._absolute_path.read_text(encoding="utf-8") != self.text:
            raise ValueError(f"source changed while scanning: {self.path}")
        return parse_file(self._absolute_path)

    @classmethod
    def read(cls, root: Path, path: Path) -> SourceFile:
        return cls(path.relative_to(root), path.read_text(encoding="utf-8"), path)


@dataclass(frozen=True)
class Rule:
    id: str
    governs: str
    scope: tuple[str, ...]
    check: Callable[[SourceFile], Iterable[Violation]]
    bad: str
    good: str
    pragma: str | None


# No rules scan foreign CI contracts/tooling or the #6048 duplicate-name checker.
# API files are read only, as agreed with the bu-rvgzmf.3 coordinator.
EXCLUDE = (
    "tests/contracts/test_ci_*.py",
    "tests/contracts/test_frontend_guard_steps.py",
    "tests/contracts/test_qg_serial_target.py",
    "tests/contracts/test_standard_lint_tools.py",
    "tests/scripts/test_ci_*.py",
    "tests/scripts/test_check_ci_*.py",
    "tests/scripts/test_check_duplicate_toplevel_names.py",
)
TEST_SCOPE = ("tests/**/*.py", "roster/*/tests/**/*.py")


def _site(source: SourceFile, node: ast.AST, rule: str, detail: str) -> Violation:
    digest = hashlib.sha256(ast.dump(node, include_attributes=False).encode()).hexdigest()[:16]
    return Violation(source.path, node.lineno, rule, f"{detail} (shape {digest})")


def _excepted(source: SourceFile, pragma: str, rule: str) -> tuple[bool, list[Violation]]:
    if not source.tree.body:
        return False, []
    present, reason = pragma_declaration(source.tree.body[0], source.text.splitlines(), pragma)
    if present and not reason:
        return False, [Violation(source.path, 1, rule, f"missing-reason: {pragma}")]
    return bool(reason), []


def _path_segments(node: ast.AST) -> Iterator[tuple[ast.Constant, str] | None]:
    if isinstance(node, ast.BinOp):
        yield from _path_segments(node.left)
        yield from _path_segments(node.right)
    elif isinstance(node, ast.Constant) and isinstance(node.value, str):
        yield node, node.value.strip("/")
    else:
        yield None


def no_change_prose_reads(source: SourceFile) -> Iterable[Violation]:
    rule = "no-change-prose-reads"
    # Escaped literals must still be parsed; the filter only skips files whose
    # text cannot spell the forbidden constant or an exception declaration.
    if (
        "\\" not in source.text
        and ("openspec" not in source.text or "changes" not in source.text)
        and "source-guard: synthetic-changes-tree" not in source.text
    ):
        return
    excused, errors = _excepted(source, "source-guard: synthetic-changes-tree", rule)
    yield from errors
    if excused:
        return
    tree = strip_docstrings(source.tree)
    seen_pairs: set[tuple[ast.Constant, ast.Constant]] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if "openspec/changes" in node.value:
                yield _site(source, node, rule, "change-prose path")
            continue

        # Path('openspec', 'changes'), root / 'openspec' / 'changes', and
        # adjacent constant strings. The bare word 'changes' never qualifies.
        if not isinstance(node, ast.BinOp | ast.Call | ast.List | ast.Tuple):
            continue
        parts = (
            node.args
            if isinstance(node, ast.Call)
            else (node.elts if isinstance(node, ast.List | ast.Tuple) else [node])
        )
        segments = [segment for part in parts for segment in _path_segments(part)]
        for left, right in zip(segments, segments[1:], strict=False):
            if left is None or right is None or (left[1], right[1]) != ("openspec", "changes"):
                continue
            pair = (left[0], right[0])
            # An enclosing expression can visit the same literals as its child.
            # Another pair on the same line is a separate occurrence.
            if pair not in seen_pairs:
                seen_pairs.add(pair)
                yield _site(source, node, rule, "change-prose path")


def _bindings(tree: ast.Module, module: str) -> dict[str, str]:
    bindings = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == module:
                    bindings[alias.asname or alias.name] = module
        elif isinstance(node, ast.ImportFrom) and node.module == module:
            for alias in node.names:
                bindings[alias.asname or alias.name] = f"{module}.{alias.name}"
    return bindings


def _name(node: ast.AST, bindings: dict[str, str]) -> str:
    if isinstance(node, ast.Name):
        return bindings.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        return f"{_name(node.value, bindings)}.{node.attr}"
    return ""


def ast_tests_use_source_guard(source: SourceFile) -> Iterable[Violation]:
    rule = "ast-tests-use-source-guard"
    if "ast" not in source.text:
        return
    excused, errors = _excepted(source, "source-guard: non-scan-ast", rule)
    yield from errors
    if excused or source.path.parts[:2] == ("tests", "architecture"):
        return
    tree = source.tree
    if not any(module == "ast" for _, module, _ in import_edges(tree)):
        return
    bindings = _bindings(tree, "ast")
    uses = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Name | ast.Attribute)
        and isinstance(node.ctx, ast.Load)
        and _name(node, bindings).startswith("ast.")
    ]
    if not uses:
        return
    shared = _bindings(tree, "butlers.testing.source_guard")
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "butlers.testing":
            shared.update(
                {
                    a.asname or a.name: "butlers.testing.source_guard"
                    for a in node.names
                    if a.name == "source_guard"
                }
            )
    if any(
        isinstance(node, ast.Name | ast.Attribute)
        and isinstance(node.ctx, ast.Load)
        and _name(node, shared).startswith("butlers.testing.source_guard")
        for node in ast.walk(tree)
    ):
        return
    # One exact module obligation, anchored to all AST uses rather than a filename
    # allow-list. A token import without a reference does not discharge it.
    anchor = ast.Tuple(elts=uses, ctx=ast.Load())
    anchor.lineno = min(node.lineno for node in uses)
    yield _site(source, anchor, rule, "AST use without a source_guard reference")


def no_private_walkers(source: SourceFile) -> Iterable[Violation]:
    if "NodeVisitor" not in source.text and "NodeTransformer" not in source.text:
        return
    bindings = _bindings(source.tree, "ast")
    for node in ast.walk(source.tree):
        if isinstance(node, ast.ClassDef) and any(
            _name(base, bindings) in {"ast.NodeVisitor", "ast.NodeTransformer"}
            for base in node.bases
        ):
            yield _site(source, node, "no-private-walkers", f"private walker {node.name}")


RULES = (
    Rule(
        "no-change-prose-reads",
        "bu-rvgzmf.4 AC1",
        TEST_SCOPE,
        no_change_prose_reads,
        "path = root / 'openspec' / 'changes' / 'active' / 'design.md'",
        "assert 'changes' not in jobs\npath = root / 'openspec' / 'specs'",
        "source-guard: synthetic-changes-tree",
    ),
    Rule(
        "ast-tests-use-source-guard",
        "bu-rvgzmf.4 AC2",
        TEST_SCOPE,
        ast_tests_use_source_guard,
        "import ast\nast.parse(source)",
        "import ast\nfrom butlers.testing.source_guard import calls_with_owner\n"
        "calls_with_owner(ast.parse(source))",
        "source-guard: non-scan-ast",
    ),
    Rule(
        "no-private-walkers",
        "bu-rvgzmf.4 AC2",
        TEST_SCOPE,
        no_private_walkers,
        "from ast import NodeVisitor as Visitor\nclass Scan(Visitor):\n pass",
        "from butlers.testing.source_guard import calls_with_owner\ncalls_with_owner(tree)",
        None,
    ),
)


def findings(root: Path, rule: Rule) -> tuple[Violation, ...]:
    result = []
    for path in python_files(rule.scope, EXCLUDE, root=root):
        source = SourceFile.read(root, path)
        result.extend(rule.check(source))
    return tuple(sorted(result))


@dataclass(frozen=True)
class Offender:
    violation: Violation
    reason: str


def ratchet(actual: Iterable[Violation], approved: Iterable[Offender]) -> tuple[Violation, ...]:
    """Reject new, obsolete, duplicate and unreasoned exact-site entries."""
    observed = Counter(actual)
    approved = tuple(approved)
    known = {entry.violation for entry in approved}
    errors = [
        Violation(v.path, v.line, v.rule, f"new offender: {v.detail}")
        for v in (observed - Counter(known)).elements()
    ]
    errors += [
        Violation(v.path, v.line, v.rule, f"obsolete offender: {v.detail}")
        for v in known - observed.keys()
    ]
    errors += [
        Violation(
            e.violation.path, e.violation.line, e.violation.rule, "missing-reason: offender entry"
        )
        for e in approved
        if not e.reason.strip()
    ]
    if len(known) != len(approved):
        errors.append(
            Violation(
                Path("tests/architecture/source_rules.py"), 1, "ratchet", "duplicate offender entry"
            )
        )
    return tuple(sorted(errors))
