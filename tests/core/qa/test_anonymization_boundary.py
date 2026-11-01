"""Static guards for QA dispatch egress anonymization boundaries."""

from __future__ import annotations

import ast
import dataclasses
import inspect
import textwrap
import zlib
from pathlib import Path
from types import ModuleType
from typing import NamedTuple

import pytest

from butlers.core.qa import dispatch, prompts
from butlers.core.qa.publication.export import (
    ExportRefused,
    GitIdentity,
    SealedExport,
    build_sealed_export,
)
from tests.core.qa.publication_fakes import QA_IDENTITY, commit_file, git, init_repo


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
    """No in-process ``gh pr create``, ``git commit -m`` or ``git push`` in the dispatcher.

    All egress goes through the publisher's closed transport, which sanitizes
    before any provider call (tests/core/qa/test_publication_transport.py).
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


# ---------------------------------------------------------------------------
# Sealed export (REQ-qa-publication-authority-004): every newly reachable commit,
# path, added line and author identity is validated before the first push, the
# validated bytes are frozen, and nothing from the agent repository executes.
# ---------------------------------------------------------------------------


async def _export(repo: Path, base: str, head: str) -> SealedExport:
    return await build_sealed_export(
        repo / ".git" / "objects", base_sha=base, head_sha=head, identity=QA_IDENTITY
    )


async def test_clean_branch_exports_only_new_objects(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    base = init_repo(repo)  # the base carries an email address: already public, not new
    commit_file(repo, "src/app.py", "value = 2\nowner = 'ops@example.com'\n", "fix: bump value")
    head = commit_file(repo, "tests/test_app.py", "assert True\n", "test: cover value")

    export = await _export(repo, base, head)

    export.verify()
    assert len(export.commits) == 2 and export.commits[-1] == head
    assert base not in {obj.oid for obj in export.objects}


@pytest.mark.parametrize(
    ("leak", "code"),
    [
        ("prior_commit", "content_leak"),
        ("author", "author_metadata"),
        ("message", "message_leak"),
    ],
)
async def test_leak_anywhere_in_reachable_history_refuses(
    tmp_path: Path, leak: str, code: str
) -> None:
    repo = tmp_path / "repo"
    base = init_repo(repo)
    if leak == "prior_commit":
        # The head tree is clean, but the leaking blob stays reachable through history.
        commit_file(repo, "src/app.py", "value = 1\ntoken = 'sk-ant-abcdefghijklmnop'\n", "wip")
        head = commit_file(repo, "src/app.py", "value = 2\n", "fix: drop debug")
    elif leak == "author":
        intruder = GitIdentity("Jane Doe", "jane.doe@corp.internal")
        head = commit_file(repo, "src/app.py", "value = 2\n", "fix", identity=intruder)
    else:
        head = commit_file(repo, "src/app.py", "value = 2\n", "fix for jane.doe@corp.example")

    with pytest.raises(ExportRefused) as refused:
        await _export(repo, base, head)

    assert refused.value.code == code


async def test_bytes_swapped_after_validation_or_in_the_store_refuse(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    base = init_repo(repo)
    head = commit_file(repo, "src/app.py", "value = 2\n", "fix")
    export = await _export(repo, base, head)

    blob = next(obj for obj in export.objects if obj.kind == "blob")
    swapped_objects = tuple(
        dataclasses.replace(obj, data=b"value = 'sk-ant-abcdefghijklmnop'\n")
        if obj is blob
        else obj
        for obj in export.objects
    )
    with pytest.raises(ExportRefused, match="export_tampered"):
        dataclasses.replace(export, objects=swapped_objects).verify()

    # A rewritten loose object cannot substitute bytes for a known id either.
    loose = repo / ".git" / "objects" / blob.oid[:2] / blob.oid[2:]
    loose.chmod(0o644)
    leaked = b"value = 'sk-ant-abcdefghijklmnop'\n"
    loose.write_bytes(zlib.compress(b"blob %d\0" % len(leaked) + leaked))
    with pytest.raises(ExportRefused, match="object_hash_mismatch"):
        await _export(repo, base, head)


async def test_planted_hooks_config_and_credential_helper_never_execute(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    base = init_repo(repo)
    head = commit_file(repo, "src/app.py", "value = 2\n", "fix")
    marker = tmp_path / "executed"
    payload = tmp_path / "payload.sh"
    payload.write_text(f"#!/bin/sh\necho ran >> {marker}\nexit 0\n")
    payload.chmod(0o755)
    hooks = tmp_path / "planted-hooks"
    hooks.mkdir()
    for hook in ("pre-push", "post-checkout", "reference-transaction", "pre-auto-gc"):
        (hooks / hook).symlink_to(payload)
        (repo / ".git" / "hooks" / hook).symlink_to(payload)
    git(repo, "config", "core.hooksPath", str(hooks))
    git(repo, "config", "core.fsmonitor", str(payload))
    git(repo, "config", "credential.helper", f"!{payload}")

    export = await _export(repo, base, head)

    export.verify()
    assert not marker.exists()
    # Control: the planted fsmonitor does run for an ordinary git command in that repo.
    git(repo, "status")
    assert marker.exists()
