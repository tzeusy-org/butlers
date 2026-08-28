"""Keep secrets-router contract citations on live OpenSpec baselines.

The router's endpoint docstrings are a maintainer-facing trace from the
interface to the behaviour it implements.  An archived change directory is
historical evidence, not a live contract, so this guard deliberately inspects
only ``secrets_v2.py`` docstrings labelled ``Spec anchor``.  It does not create
a repository-wide documentation policy or reinterpret other citation forms.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SECRETS_V2 = _REPO_ROOT / "src" / "butlers" / "api" / "routers" / "secrets_v2.py"
_SPEC_ANCHOR_HEADING = re.compile(r"^Spec anchor\s*$", re.MULTILINE)
_SPEC_PATH = re.compile(r"openspec/[^\s]+/spec\.md")
_SPEC_REFERENCE_HEADING = re.compile(r"§(.+)")

_EXPECTED_ANCHORED_ROUTES: dict[str, tuple[str, str, str]] = {
    "get_audit_history": ("get", "/audit/{scope}/{key}", "ApiResponse[list[AuditEvent]]"),
    "get_breaks_catalogue": ("get", "/breaks-catalogue", "ApiResponse[list[BreakEntry]]"),
    "rotate_user_credential": ("post", "/user/{provider}/rotate", "ApiResponse[UserSecretDetail]"),
    "disconnect_user_credential": (
        "post",
        "/user/{provider}/disconnect",
        "ApiResponse[DisconnectStatus]",
    ),
    "probe_user_credential": ("post", "/user/{provider}/probe", "ApiResponse[TestResult]"),
    "reauthorize_user_credential": (
        "post",
        "/user/{provider}/reauthorize",
        "ApiResponse[ReauthorizeResponse]",
    ),
    "set_system_credential": ("post", "/system/{key}", "ApiResponse[SystemCredentialDetail]"),
    "probe_system_credential": ("post", "/system/{key}/probe", "ApiResponse[TestResult]"),
    "delete_system_credential": ("delete", "/system/{key}", "ApiResponse[SystemDeleteStatus]"),
    "rotate_cli_credential": (
        "post",
        "/cli/{credential_id:path}/rotate",
        "ApiResponse[CliRotateResult]",
    ),
    "revoke_cli_credential": (
        "post",
        "/cli/{credential_id:path}/revoke",
        "ApiResponse[CliRevokeResult]",
    ),
    "reauthorize_cli_credential": (
        "post",
        "/cli/{credential_id:path}/reauthorize",
        "ApiResponse[CliReauthorizeResponse]",
    ),
}


class _StripDocstrings(ast.NodeTransformer):
    """Remove docstrings before checking the router's executable route surface."""

    def _strip(self, node: ast.AST) -> ast.AST:
        body = getattr(node, "body", None)
        if isinstance(body, list) and body:
            first = body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                node.body = body[1:]
        self.generic_visit(node)
        return node

    visit_Module = _strip
    visit_FunctionDef = _strip
    visit_AsyncFunctionDef = _strip
    visit_ClassDef = _strip


def _router_tree() -> ast.Module:
    return ast.parse(_SECRETS_V2.read_text(encoding="utf-8"), filename=str(_SECRETS_V2))


def _spec_anchor_docstrings(tree: ast.Module) -> list[tuple[str, str]]:
    """Return the named interface docstrings that declare a Spec anchor."""
    anchored: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            continue
        docstring = ast.get_docstring(node)
        if docstring is None or _SPEC_ANCHOR_HEADING.search(docstring) is None:
            continue
        name = getattr(node, "name", "module")
        anchored.append((name, docstring))
    return anchored


def _assert_spec_anchors(tree: ast.Module) -> None:
    """Require every labelled path and heading to resolve in a live baseline."""
    findings: list[str] = []
    anchored = _spec_anchor_docstrings(tree)
    assert anchored, "secrets_v2.py has no Spec anchor docstrings to verify"

    for name, docstring in anchored:
        anchor = _SPEC_ANCHOR_HEADING.search(docstring)
        assert anchor is not None  # narrowed above; preserves the type invariant.
        current_path: str | None = None
        current_path_has_heading = False
        path_count = 0

        for raw_line in docstring[anchor.end() :].splitlines():
            line = raw_line.strip()
            if _SPEC_PATH.fullmatch(line):
                if current_path is not None and not current_path_has_heading:
                    findings.append(f"{name}: {current_path} names no § heading")
                current_path = line
                current_path_has_heading = False
                path_count += 1
                if not current_path.startswith("openspec/specs/"):
                    findings.append(f"{name}: {current_path} is not a live baseline path")
                elif not (_REPO_ROOT / current_path).is_file():
                    findings.append(f"{name}: {current_path} does not exist")
                continue

            heading = _SPEC_REFERENCE_HEADING.fullmatch(line)
            if heading is None:
                continue
            if current_path is None:
                findings.append(f"{name}: §{heading.group(1)} appears before a spec path")
                continue

            current_path_has_heading = True
            baseline = _REPO_ROOT / current_path
            if not baseline.is_file():
                continue
            baseline_heading = re.compile(
                rf"^#{{1,6}}\s+(?:Requirement|Scenario):\s+{re.escape(heading.group(1))}\s*$",
                re.MULTILINE,
            )
            if baseline_heading.search(baseline.read_text(encoding="utf-8")) is None:
                findings.append(f"{name}: {current_path} lacks §{heading.group(1)}")

        if path_count == 0:
            findings.append(f"{name}: Spec anchor names no OpenSpec baseline path")
        elif current_path is not None and not current_path_has_heading:
            findings.append(f"{name}: {current_path} names no § heading")

    assert not findings, "secrets_v2.py Spec anchor drift:\n" + "\n".join(findings)


def _route_shape(node: ast.AsyncFunctionDef) -> tuple[str, str, str]:
    """Return the route method, path, and response-model expression for one handler."""
    routes = [
        decorator
        for decorator in node.decorator_list
        if isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Attribute)
        and isinstance(decorator.func.value, ast.Name)
        and decorator.func.value.id == "router"
    ]
    assert len(routes) == 1, f"router decorator drift: {node.name}"
    route = routes[0]
    assert len(route.args) == 1 and isinstance(route.args[0], ast.Constant)
    assert isinstance(route.args[0].value, str)
    response_model = next(
        (keyword.value for keyword in route.keywords if keyword.arg == "response_model"),
        None,
    )
    assert response_model is not None, f"response model drift: {node.name}"
    return route.func.attr, route.args[0].value, ast.unparse(response_model)


def _assert_router_boundary(tree: ast.Module) -> None:
    """Pin anchored handler names, route decorators, and response models only."""
    stripped = _StripDocstrings().visit(tree)
    assert isinstance(stripped, ast.Module)
    actual = {
        node.name: _route_shape(node)
        for node in stripped.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name in _EXPECTED_ANCHORED_ROUTES
    }
    assert set(actual) == set(_EXPECTED_ANCHORED_ROUTES), "handler name drift"

    for name, expected in _EXPECTED_ANCHORED_ROUTES.items():
        method, path, response_model = actual[name]
        assert (method, path) == expected[:2], f"router decorator drift: {name}"
        assert response_model == expected[2], f"response model drift: {name}"


def test_secrets_v2_spec_anchors_resolve_to_live_baseline_specs() -> None:
    """Every labelled router anchor names a live baseline path and heading."""
    _assert_spec_anchors(_router_tree())


def test_router_boundary_is_unchanged_after_stripping_docstrings() -> None:
    """The anchor repair changes documentation rather than routed API behavior."""
    _assert_router_boundary(_router_tree())


def test_spec_anchor_guard_rejects_missing_baseline_heading() -> None:
    """A live path alone is insufficient when the cited heading is stale."""
    source = _SECRETS_V2.read_text(encoding="utf-8")
    mutated = source.replace(
        "§Secrets Mutation Endpoints",
        "§Missing Secrets Mutation Requirement",
        1,
    )
    assert mutated != source

    with pytest.raises(AssertionError, match="Missing Secrets Mutation Requirement"):
        _assert_spec_anchors(ast.parse(mutated))


@pytest.mark.parametrize(
    ("name", "before", "after"),
    [
        (
            "handler name",
            "async def reauthorize_cli_credential(",
            "async def reauthorize_cli_credential_changed(",
        ),
        (
            "router decorator",
            '"/cli/{credential_id:path}/reauthorize",',
            '"/cli/{credential_id:path}/reauthorize-mutated",',
        ),
        (
            "response model",
            "response_model=ApiResponse[CliReauthorizeResponse]",
            "response_model=ApiResponse[CliRevokeResult]",
        ),
    ],
)
def test_router_boundary_guard_rejects_interface_drift(
    name: str,
    before: str,
    after: str,
) -> None:
    """The anchor-only change must not silently alter a routed interface."""
    source = _SECRETS_V2.read_text(encoding="utf-8")
    mutated = source.replace(before, after, 1)
    assert mutated != source, name

    with pytest.raises(AssertionError, match=name):
        _assert_router_boundary(ast.parse(mutated))
