"""Pins the root conftest's package-source guard.

A worktree whose ``.venv`` is a symlink to another checkout's venv inherits that
checkout's editable-install ``.pth``, which hardcodes the *other* checkout's
``src/``.  ``import butlers`` then resolves outside the worktree, so a local run
validates that other working copy while looking like it validated the branch
diff.  The damage is false confidence, which is why the guard refuses to run
instead of warning into a scrollback nobody reads (bu-1redj).

The subprocess tests below are the point of this file.  A guard that cannot be
made to fire is worse than no guard, so the hazard is constructed for real: a
``butlers`` package is planted outside the checkout and put ahead of the
editable install via ``PYTHONPATH``.  Two controls establish that the banner
comes from the guard rather than from the wreckage that follows it -- an
identical run without the injection must collect cleanly, and an injected run
with the opt-out set must fail on the foreign package's missing submodules with
no banner at all.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CONFTEST_PATH = REPO_ROOT / "conftest.py"


@pytest.fixture(scope="module")
def root_conftest() -> ModuleType:
    """Load the root conftest by path (its module-level patches are idempotent)."""
    spec = importlib.util.spec_from_file_location(
        "_butlers_root_conftest_package_source_under_test", CONFTEST_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def foreign_butlers_src(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A ``butlers`` package outside the checkout, standing in for another tree's src/."""
    src = tmp_path_factory.mktemp("foreign-checkout") / "src"
    (src / "butlers").mkdir(parents=True)
    (src / "butlers" / "__init__.py").write_text("NOT_THE_REAL_BUTLERS_PACKAGE = True\n")
    return src


def _collect_only(**env_overrides: str) -> subprocess.CompletedProcess[str]:
    """Collect this one file in a subprocess, so the root conftest is imported fresh.

    ``-n 0`` overrides the ``-n 3`` in ``addopts``: xdist workers would add
    startup cost and route the conftest failure through a less direct path.
    """
    env = dict(os.environ)
    env.pop("BUTLERS_ALLOW_EXTERNAL_PACKAGE", None)
    env.pop("PYTHONPATH", None)
    env.update(env_overrides)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-n",
            "0",
            "-p",
            "no:cacheprovider",
            str(Path(__file__).resolve()),
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )


def test_package_under_the_checkout_src_is_accepted(root_conftest: ModuleType) -> None:
    assert (
        root_conftest._external_package_error(
            Path("/repo"),
            [Path("/repo/src/butlers/__init__.py"), Path("/repo/src/butlers")],
        )
        is None
    )


def test_package_from_another_checkout_is_rejected(root_conftest: ModuleType) -> None:
    message = root_conftest._external_package_error(
        Path("/repo"), [Path("/elsewhere/src/butlers/__init__.py")]
    )
    assert message is not None
    assert "/elsewhere/src/butlers/__init__.py" in message
    assert "/repo/src" in message


def test_stale_copy_inside_the_checkout_but_outside_src_is_rejected(
    root_conftest: ModuleType,
    tmp_path: Path,
) -> None:
    """Being *somewhere* in the tree is not the invariant; being this tree's src/ is.

    A non-editable install into the worktree's own ``.venv`` is the same hazard:
    the code under test is a copy, not the working tree.
    """
    message = root_conftest._external_package_error(
        Path("/repo"),
        [Path("/repo/.venv/lib/python3.12/site-packages/butlers/__init__.py")],
    )
    assert message is not None

    # REQ-testing-048: an advisory real environment cannot keep another
    # checkout's editable path after a cache hit. No mocked UV/import success.
    import json
    import shutil

    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import ci_environment

    source = tmp_path / "src/butlers"
    source.mkdir(parents=True)
    (source / "__init__.py").write_text("CACHE_CONTROL = True\n")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="butlers"\nversion="0.0.0"\nrequires-python=">=3.12"\n'
        '[build-system]\nrequires=["hatchling"]\nbuild-backend="hatchling.build"\n'
    )
    (tmp_path / "scripts").mkdir()
    shutil.copyfile(REPO_ROOT / "scripts/ci_environment.py", tmp_path / "scripts/ci_environment.py")
    subprocess.run(
        ["uv", "lock", "--offline"], cwd=tmp_path, check=True, capture_output=True, timeout=30
    )
    cold = ci_environment.prepare(tmp_path)
    assert cold["cache"] == "miss" and cold["own_source"] == "passed"
    warm = ci_environment.prepare(tmp_path)
    assert warm["cache"] == "hit" and warm["compatibility"] == cold["compatibility"]
    foreign = tmp_path / "other-src/butlers"
    foreign.mkdir(parents=True)
    (foreign / "__init__.py").write_text("FOREIGN = True\n")
    pth = next((tmp_path / ".venv").glob("lib/python*/site-packages/*butlers.pth"))
    pth.write_text(str(foreign.parent) + "\n")
    with pytest.raises(subprocess.CalledProcessError):
        ci_environment.validate_source(tmp_path)
    assert ci_environment.prepare(tmp_path)["cache"] == "hit"
    ci_environment.validate_source(tmp_path)
    (tmp_path / ".venv/ci-environment.json").write_text("not-json")
    assert ci_environment.prepare(tmp_path)["cache"] == "invalid"
    receipt = json.loads((tmp_path / ".venv/ci-environment.json").read_text())
    assert receipt == ci_environment.compatibility(tmp_path)
    project = tmp_path / "pyproject.toml"
    prior_project = project.read_text()
    prior_lock = (tmp_path / "uv.lock").read_bytes()
    project.write_text(prior_project.replace('version="0.0.0"', 'version="0.0.1"'))
    with pytest.raises(subprocess.CalledProcessError):
        ci_environment.prepare(tmp_path)
    assert (tmp_path / "uv.lock").read_bytes() == prior_lock
    project.write_text(prior_project)
    assert ci_environment.prepare(tmp_path)["own_source"] == "passed"
    linked = tmp_path / "linked"
    linked.mkdir()
    for name in ("pyproject.toml", "uv.lock"):
        shutil.copyfile(tmp_path / name, linked / name)
    (linked / "scripts").mkdir()
    shutil.copyfile(tmp_path / "scripts/ci_environment.py", linked / "scripts/ci_environment.py")
    (linked / ".venv").symlink_to(tmp_path / ".venv", target_is_directory=True)
    with pytest.raises(ValueError, match="owned real directory"):
        ci_environment.prepare(linked)
    assert (linked / ".venv").is_symlink()
    ci_environment.validate_source(tmp_path)


def test_absent_package_is_left_to_the_import_error(root_conftest: ModuleType) -> None:
    """No spec means no install; ``ImportError`` already says that clearly."""
    assert root_conftest._external_package_error(Path("/repo"), []) is None
    assert root_conftest._package_source_paths(None) == []


def test_guard_fires_when_butlers_resolves_outside_the_checkout(
    root_conftest: ModuleType, foreign_butlers_src: Path
) -> None:
    result = _collect_only(PYTHONPATH=str(foreign_butlers_src))
    output = result.stdout + result.stderr

    assert result.returncode != 0, output[-2000:]
    assert root_conftest.PACKAGE_SOURCE_GUARD_BANNER in output, output[-2000:]
    # It must name the offender and the expectation, not just complain.
    assert str(foreign_butlers_src / "butlers" / "__init__.py") in output
    assert str(REPO_ROOT / "src") in output


def test_guard_is_silent_for_an_ordinary_run(root_conftest: ModuleType) -> None:
    """Negative control: the identical command, minus the injection, must be clean.

    Without this, a banner asserted above could equally well be produced by the
    subprocess harness itself.
    """
    result = _collect_only()
    output = result.stdout + result.stderr

    assert result.returncode == 0, output[-2000:]
    assert root_conftest.PACKAGE_SOURCE_GUARD_BANNER not in output


def test_opt_out_suppresses_the_guard_and_nothing_else(
    root_conftest: ModuleType, foreign_butlers_src: Path
) -> None:
    """Opt-out control: same injection, guard disabled -> a different failure, no banner.

    The run still fails, on the foreign package's missing submodules.  That is
    the proof the banner in the positive case is the guard's doing and not a
    side effect of the broken import that would otherwise follow.
    """
    result = _collect_only(PYTHONPATH=str(foreign_butlers_src), BUTLERS_ALLOW_EXTERNAL_PACKAGE="1")
    output = result.stdout + result.stderr

    assert result.returncode != 0, output[-2000:]
    assert root_conftest.PACKAGE_SOURCE_GUARD_BANNER not in output, output[-2000:]
    assert "butlers.testing.shared_fixtures" in output, output[-2000:]
