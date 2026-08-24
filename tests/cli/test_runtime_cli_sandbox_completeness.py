"""Repository-wide routing contracts for Dashboard runtime CLI children."""

from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DASHBOARD_SCAN_ROOTS = (
    _REPO_ROOT / "src" / "butlers" / "api",
    _REPO_ROOT / "src" / "butlers" / "cli_auth",
)
_DASHBOARD_ALIAS_JOB = _REPO_ROOT / "src" / "butlers" / "jobs" / "secrets_staleness.py"
_DIRECT_CHILD_CALLS = {
    "asyncio.create_subprocess_exec",
    "asyncio.create_subprocess_shell",
    "subprocess.run",
    "subprocess.Popen",
    "subprocess.call",
    "subprocess.check_call",
    "subprocess.check_output",
    "subprocess.getoutput",
    "subprocess.getstatusoutput",
    "os.popen",
    "os.posix_spawn",
    "os.posix_spawnp",
    "os.system",
}
_SANDBOX_SPAWN_SOURCE = _REPO_ROOT / "src" / "butlers" / "cli_auth" / "sandbox_platform.py"


def _import_bindings(tree: ast.AST) -> dict[str, str]:
    bindings: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for imported in node.names:
                bindings[imported.asname or imported.name.split(".", maxsplit=1)[0]] = imported.name
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            for imported in node.names:
                bindings[imported.asname or imported.name] = f"{node.module}.{imported.name}"
    return bindings


def _dotted_name(node: ast.expr, bindings: dict[str, str]) -> str | None:
    if isinstance(node, ast.Name):
        return bindings.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        parent = _dotted_name(node.value, bindings)
        return f"{parent}.{node.attr}" if parent is not None else None
    return None


def _direct_child_references(source: Path) -> set[str]:
    """Find direct child-spawn APIs, including injected callable defaults."""
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    bindings = _import_bindings(tree)
    return {
        dotted
        for node in ast.walk(tree)
        if isinstance(node, ast.expr)
        and (dotted := _dotted_name(node, bindings)) in _DIRECT_CHILD_CALLS
    }


def _invoke_calls(source: Path) -> int:
    """Count actual ``.invoke()`` syntax, excluding prose/docstring mentions."""
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    return sum(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "invoke"
        for node in ast.walk(tree)
    )


def _dashboard_cli_auth_sources() -> tuple[Path, ...]:
    return tuple(source for root in _DASHBOARD_SCAN_ROOTS for source in root.rglob("*.py")) + (
        _DASHBOARD_ALIAS_JOB,
    )


def test_dashboard_runtime_cli_paths_have_one_concrete_sandbox_spawn_boundary() -> None:
    """REQ-core-credentials-002: known Dashboard aliases cannot regain a direct child."""
    direct_child_references = {
        source.relative_to(_REPO_ROOT): _direct_child_references(source)
        for source in _dashboard_cli_auth_sources()
        if _direct_child_references(source)
    }

    assert direct_child_references == {
        _SANDBOX_SPAWN_SOURCE.relative_to(_REPO_ROOT): {"asyncio.create_subprocess_exec"}
    }
    assert "launch_device_auth" in (
        _REPO_ROOT / "src" / "butlers" / "cli_auth" / "session.py"
    ).read_text(encoding="utf-8")
    assert "run_readonly_command" in (
        _REPO_ROOT / "src" / "butlers" / "cli_auth" / "health.py"
    ).read_text(encoding="utf-8")
    assert "run_readonly_command" in (
        _REPO_ROOT / "src" / "butlers" / "api" / "routers" / "cli_auth.py"
    ).read_text(encoding="utf-8")


def test_direct_spawn_inventory_resolves_import_bound_bypass_apis(tmp_path: Path) -> None:
    """The AST guard catches aliases rather than trusting import spelling."""
    synthetic_source = tmp_path / "synthetic_dashboard_bypass.py"
    synthetic_source.write_text(
        "\n".join(
            (
                "import os",
                "import subprocess as process",
                "from os import popen as shell_pipe",
                "from subprocess import getoutput as capture_output",
                "",
                "os.system('never run')",
                "os.posix_spawn('/bin/true', ('true',), {})",
                "os.posix_spawnp('true', ('true',), {})",
                "shell_pipe('never run')",
                "process.getstatusoutput('never run')",
                "capture_output('never run')",
                "",
            )
        ),
        encoding="utf-8",
    )

    assert _direct_child_references(synthetic_source) == {
        "os.system",
        "os.posix_spawn",
        "os.posix_spawnp",
        "os.popen",
        "subprocess.getstatusoutput",
        "subprocess.getoutput",
    }


def test_no_dashboard_source_invokes_a_runtime_adapter() -> None:
    """REQ-core-credentials-002 AC4: the deferred exception is gone, not merely smaller.

    The sandbox slice left exactly one adapter invocation behind ---
    ``model_settings.py``'s local verification probe --- and pinned it here so
    the cutover would have to be visible.  bu-0uqgo.11 removed it: Test,
    verify-all, and the hourly sweep now sign a capability and let Switchboard
    hold the runtime.  The empty set is the assertion; a Dashboard source that
    starts invoking an adapter again fails here rather than quietly running a
    model beside a mounted signer.
    """
    adapter_invocations = {
        source.relative_to(_REPO_ROOT)
        for source in _dashboard_cli_auth_sources()
        if _invoke_calls(source)
    }

    assert adapter_invocations == set()
    secrets_v2 = (_REPO_ROOT / "src" / "butlers" / "api" / "routers" / "secrets_v2.py").read_text(
        encoding="utf-8"
    )
    assert "from butlers.cli_auth.session import CLIAuthSession, store_session" in secrets_v2
    assert "CLIAuthSession(" in secrets_v2
