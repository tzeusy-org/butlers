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


def _looks_like_git_push_call(node: ast.Call) -> bool:
    values = [_literal_string(arg) for arg in node.args]
    return values[:2] == ["git", "push"]


def _function_egresses(module: ModuleType) -> list[FunctionEgress]:
    egresses: list[FunctionEgress] = []
    for node in ast.walk(_module_tree(module)):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for child in ast.walk(node):
            if _looks_like_gh_pr_create_sequence(child):
                egresses.append(FunctionEgress(node.name, node.lineno, child.lineno))
            elif isinstance(child, ast.Call) and (
                _looks_like_git_commit_m_call(child) or _looks_like_git_push_call(child)
            ):
                egresses.append(FunctionEgress(node.name, node.lineno, child.lineno))
    return egresses


def test_qa_dispatch_has_no_in_process_github_or_commit_egress():
    """Publication fails closed: no ``gh pr create``, ``git commit -m`` or ``git push``.

    When the isolated publisher re-enables publication, its egress path must
    anonymize and validate before the boundary (see bu-mc01y5.6).
    """
    assert _function_egresses(dispatch) == []


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
