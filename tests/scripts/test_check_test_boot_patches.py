"""The patched-boot ratchet (bu-rvgzmf.2).

A test that boots a daemon uses ``booted_daemon``; one whose subject is a single
phase calls that phase on a ``tool_host``. Neither patches the lifecycle.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import check_test_boot_patches as guard  # noqa: E402

pytestmark = pytest.mark.unit

# Built from parts so this file does not itself hold the targets it plants.
LIFECYCLE = "butlers." + "lifecycle"
PATCHED_PHASE = f'from unittest.mock import patch\n\nP = patch("{LIFECYCLE}.run_migrations")\n'
PATCHED_METHOD = (
    "from unittest.mock import patch\nfrom butlers.daemon import ButlerDaemon\n\n"
    'P = patch.object(ButlerDaemon, "_start_mcp_server")\n'
)
SETATTR_PHASE = (
    f'def test_x(monkeypatch):\n    monkeypatch.setattr("{LIFECYCLE}.init_telemetry", 1)\n'
)


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for path, text in files.items():
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    return tmp_path


def _run(root: Path, baseline: dict, *extra: str) -> tuple[int, dict]:
    path = root / "baseline.json"
    path.write_text(json.dumps(baseline))
    code = guard.main(["--root", str(root), "--baseline", str(path), *extra])
    return code, json.loads(path.read_text())


@pytest.mark.parametrize("source", [PATCHED_PHASE, PATCHED_METHOD, SETATTR_PHASE])
@pytest.mark.parametrize("path", ["tests/daemon/test_new.py", "tests/e2e/conftest.py"])
def test_a_new_patched_boot_fails(tmp_path: Path, path: str, source: str) -> None:
    assert _run(_repo(tmp_path, {path: source}), {})[0] == 1


# Object-form patches on the lifecycle module, bound at module or function scope.
OBJECT_FORMS = {
    "import-as": (
        f"import {LIFECYCLE} as lc\n\n"
        'def test_x(monkeypatch):\n    monkeypatch.setattr(lc, "run_migrations", 1)\n'
    ),
    "from-import": (
        "from butlers import lifecycle\nfrom unittest.mock import patch\n\n"
        'P = patch.object(lifecycle, "init_telemetry")\n'
    ),
    "from-import-as": (
        "from unittest import mock\n\n"
        "def test_x():\n    from butlers import lifecycle as phases\n"
        '    with mock.patch.object(phases, "sync_schedules"):\n        pass\n'
    ),
    "dotted": (
        f"import {LIFECYCLE}\n\n"
        f'def test_x(monkeypatch):\n    monkeypatch.setattr({LIFECYCLE}, "Spawner", 1)\n'
    ),
    "daemon-setattr": (
        "from butlers.daemon import ButlerDaemon\n\n"
        'def test_x(monkeypatch):\n    monkeypatch.setattr(ButlerDaemon, "_connect_switchboard", 1)\n'
    ),
}


@pytest.mark.parametrize("form", sorted(OBJECT_FORMS))
def test_an_object_form_lifecycle_patch_counts(form: str) -> None:
    assert guard.boot_patches(OBJECT_FORMS[form]) == 1


@pytest.mark.parametrize(
    "source",
    [
        # Another module bound under the same name.
        "from butlers.jobs import secrets_lifecycle as lifecycle\n\n"
        'def test_x(monkeypatch):\n    monkeypatch.setattr(lifecycle, "run", 1)\n',
        # The name is a fixture argument in this scope, not the import.
        f"import {LIFECYCLE} as lifecycle\n\n"
        'def test_x(monkeypatch, lifecycle):\n    monkeypatch.setattr(lifecycle, "run", 1)\n',
        # Bound only inside another function.
        "def helper():\n    from butlers import lifecycle\n\n"
        'def test_x(monkeypatch):\n    monkeypatch.setattr(lifecycle, "x", 1)\n',
        # Unbound name.
        'def test_x(monkeypatch):\n    monkeypatch.setattr(lifecycle, "x", 1)\n',
        # Calling a phase is not patching it.
        f"import {LIFECYCLE} as lifecycle\n\n"
        "async def test_x(daemon, state):\n    await lifecycle._startup_modules(daemon, state)\n",
        # A public daemon attribute.
        "from butlers.daemon import ButlerDaemon\n\n"
        'def test_x(monkeypatch):\n    monkeypatch.setattr(ButlerDaemon, "start", 1)\n',
    ],
)
def test_unrelated_object_forms_do_not_count(source: str) -> None:
    assert guard.boot_patches(source) == 0


def test_frozen_patches_pass_and_the_baseline_only_shrinks(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"tests/test_a.py": PATCHED_PHASE})
    code, written = _run(root, {"tests/test_a.py": 3, "tests/gone.py": 2}, "--update-baseline")
    assert code == 0
    assert written == {"tests/test_a.py": 1}
    (root / "tests/test_a.py").write_text(PATCHED_PHASE + PATCHED_METHOD)
    assert _run(root, written)[0] == 1


@pytest.mark.parametrize(
    "path, source",
    [
        ("src/butlers/testing/daemon.py", PATCHED_PHASE),  # the kit itself
        ("tests/test_b.py", 'P = patch("butlers.daemon.helper")\n'),  # not a phase
        ("tests/test_c.py", 'P = patch.object(Other, "_start_mcp_server")\n'),  # not the daemon
    ],
)
def test_the_kit_and_non_lifecycle_patches_pass(tmp_path: Path, path: str, source: str) -> None:
    assert _run(_repo(tmp_path, {path: source}), {})[0] == 0


def test_the_committed_baseline_holds() -> None:
    assert guard.main([]) == 0
