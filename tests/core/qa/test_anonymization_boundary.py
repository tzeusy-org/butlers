"""Static guards for QA dispatch egress anonymization boundaries."""

from __future__ import annotations

import ast
import inspect
import textwrap
from types import ModuleType
from typing import NamedTuple

from butlers.core.qa import dispatch, prompts


class FunctionEgress(NamedTuple):
    function_name: str
    function_line: int
    egress_line: int


def _module_tree(module: ModuleType) -> ast.Module:
    return ast.parse(textwrap.dedent(inspect.getsource(module)))


def _literal_string(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _looks_like_gh_pr_create_sequence(node: ast.AST) -> bool:
    if not isinstance(node, ast.List | ast.Tuple):
        return False
    values = [_literal_string(elt) for elt in node.elts]
    return values[:3] == ["gh", "pr", "create"]


def _looks_like_git_commit_m_call(node: ast.Call) -> bool:
    values = [_literal_string(arg) for arg in node.args]
    return len(values) >= 3 and values[0] == "git" and values[1] == "commit" and "-m" in values


def _function_egresses(module: ModuleType) -> list[FunctionEgress]:
    egresses: list[FunctionEgress] = []
    for node in ast.walk(_module_tree(module)):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for child in ast.walk(node):
            if _looks_like_gh_pr_create_sequence(child):
                egresses.append(FunctionEgress(node.name, node.lineno, child.lineno))
            elif isinstance(child, ast.Call) and _looks_like_git_commit_m_call(child):
                egresses.append(FunctionEgress(node.name, node.lineno, child.lineno))
    return egresses


def _calls_named_before(
    function: ast.FunctionDef | ast.AsyncFunctionDef, name: str, line: int
) -> bool:
    for child in ast.walk(function):
        if not isinstance(child, ast.Call) or getattr(child, "lineno", line + 1) >= line:
            continue
        if isinstance(child.func, ast.Name) and child.func.id == name:
            return True
        if isinstance(child.func, ast.Attribute) and child.func.attr == name:
            return True
    return False


def test_qa_dispatch_egress_calls_anonymize_before_github_or_commit_boundary():
    """Every in-process GitHub/commit egress builder anonymizes in the same function first."""
    tree = _module_tree(dispatch)
    functions = {
        (node.name, node.lineno): node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }

    egresses = _function_egresses(dispatch)

    assert egresses, "Expected to find at least one QA GitHub/commit egress path"
    for egress in egresses:
        assert _calls_named_before(
            functions[(egress.function_name, egress.function_line)], "anonymize", egress.egress_line
        ), (
            f"{egress.function_name} reaches GitHub/commit egress without anonymize() "
            "earlier in the same function body"
        )


def test_qa_pr_creation_validates_anonymized_content_before_github_boundary():
    """PR-bound content must pass validate_anonymized() before gh pr create."""
    tree = _module_tree(dispatch)
    functions = {
        (node.name, node.lineno): node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }

    for egress in _function_egresses(dispatch):
        assert _calls_named_before(
            functions[(egress.function_name, egress.function_line)],
            "validate_anonymized",
            egress.egress_line,
        ), (
            f"{egress.function_name} reaches GitHub/commit egress without "
            "validate_anonymized() earlier in the same function body"
        )


def test_no_pr_creation_path_accepts_evidence_lines_parameter():
    """PR construction must not accept raw evidence_lines as a parameter."""
    checked_modules = (dispatch, prompts)
    offenders: list[str] = []

    for module in checked_modules:
        for node in ast.walk(_module_tree(module)):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            arg_names = [
                arg.arg for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
            ]
            if "evidence_lines" in arg_names:
                offenders.append(f"{module.__name__}.{node.name}")

    assert offenders == []


def test_qa_pr_creation_keeps_raw_evidence_guard_at_github_boundary():
    """Lock the explicit runtime guard immediately before gh pr create."""
    tree = _module_tree(dispatch)
    create_pr = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_create_qa_pr"
    )
    gh_line = next(
        child.lineno for child in ast.walk(create_pr) if _looks_like_gh_pr_create_sequence(child)
    )

    guard_line = next(
        child.lineno
        for child in ast.walk(create_pr)
        if isinstance(child, ast.If)
        and isinstance(child.test, ast.Call)
        and isinstance(child.test.func, ast.Name)
        and child.test.func.id == "_qa_pr_body_contains_raw_evidence_marker"
    )

    assert guard_line < gh_line
    assert _calls_named_before(create_pr, "validate_anonymized", guard_line)


def test_qa_pr_raw_evidence_guard_allows_harmless_identifier_mentions():
    """Mentioning evidence_lines in prose is not itself raw evidence egress."""
    harmless_body = "Investigation notes mention evidence_lines as a variable name."
    raw_body = "## Fix Summary\n\nevidence_lines:\n- raw traceback"

    assert not dispatch._qa_pr_body_contains_raw_evidence_marker(harmless_body)
    assert dispatch._qa_pr_body_contains_raw_evidence_marker(raw_body)
