"""`make test-qg-serial` must actually be serial, and `make test-qg` parallel (bu-bcujm).

The defect this guards against is invisible in the Makefile. `test-qg-serial` never
passed `-n`, which reads as "no parallelism" -- but pytest prepends ``addopts`` to
every invocation, and this repo's ``addopts`` carries ``-n 3 --dist loadfile``. So the
target documented as the "serial fallback for order-dependent debugging" ran on three
xdist workers: the one tool you reach for when you suspect ordering was the one tool
guaranteed to reshuffle it.

Nothing noticed, because nothing could. A reviewer reading the recipe sees an absent
flag and infers the default; the effective value only exists after pytest merges
``addopts`` with argv. So this test refuses to read either half on its own:

* the target's argv comes from ``make -n``, not from parsing Makefile variables by
  hand, so it is the command line that would really run;
* the effective ``-n`` comes from a real pytest process started with that argv, which
  reports ``config.option.numprocesses`` and whether xdist registered its distributed
  session -- after ``addopts`` merged and after xdist normalised the result.

A grep for ``-n 0`` in the Makefile would pass while pinning nothing: the value that
matters is the merged one, and it is only knowable by asking pytest.

The probe exits at ``pytest_sessionstart``, so neither subprocess collects a test or
boots a worker; both are near-instant, including in the failure case.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.skipif(shutil.which("make") is None, reason="requires make to expand the target"),
]

REPO_ROOT = Path(__file__).resolve().parents[2]

# Records what pytest actually decided, then stops the session immediately.
#
# `pytest_sessionstart` is the one place both facts are already true and nothing costly has
# happened yet: xdist normalises `-n` in `pytest_cmdline_main` and registers its DSession in
# `pytest_configure`, both of which precede it, while DSession boots its workers *in* its own
# `pytest_sessionstart` -- so a `tryfirst` impl that exits here sees the settled configuration
# and stops before a single worker is spawned. `dsession` present is the direct observation of
# "this run is distributed", not an inference from the flags.
PROBE_PLUGIN = textwrap.dedent(
    '''
    """Reports the effective xdist configuration, then exits before any worker boots."""

    import json
    import os
    from pathlib import Path

    import pytest


    @pytest.hookimpl(tryfirst=True)
    def pytest_sessionstart(session):
        config = session.config
        if hasattr(config, "workerinput"):
            return  # an xdist worker: its config describes the worker, not the run
        Path(os.environ["QG_SERIAL_PROBE_OUT"]).write_text(
            json.dumps(
                {
                    "numprocesses": getattr(config.option, "numprocesses", "<absent>"),
                    "dist": getattr(config.option, "dist", "<absent>"),
                    "tx": list(getattr(config.option, "tx", None) or []),
                    "dsession": config.pluginmanager.get_plugin("dsession") is not None,
                }
            )
        )
        pytest.exit("qg-serial probe complete", returncode=0)
    '''
)


def _target_pytest_argv(target: str) -> list[str]:
    """The pytest arguments `make <target>` would really hand to the gate."""
    dry_run = subprocess.run(
        ["make", "-n", target],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    recipe = [line for line in dry_run.splitlines() if "pytest_gate.py run" in line]
    assert len(recipe) == 1, f"expected one gate invocation in `make -n {target}`:\n{dry_run}"
    # Strip the shell line-continuation and statement separator make prints.
    line = recipe[0].rstrip().removesuffix("\\").rstrip().removesuffix(";")
    tokens = shlex.split(line)
    assert "--" in tokens, f"gate invocation has no `--` separator: {line}"
    return tokens[tokens.index("--") + 1 :]


def _effective_xdist_config(argv: list[str], tmp_path: Path) -> dict:
    """Run pytest with `argv` for real and report the xdist config it settled on."""
    plugin_dir = tmp_path / "probe_pkg"
    plugin_dir.mkdir(exist_ok=True)
    (plugin_dir / "qg_serial_probe.py").write_text(PROBE_PLUGIN)
    out = tmp_path / "probe.json"

    # Drop the outer run's pytest and coverage plumbing so the child is decided purely by
    # the target's argv and the repo's ini file.
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST_", "COV_CORE_"))}
    env["PYTHONPATH"] = os.pathsep.join([str(plugin_dir), env.get("PYTHONPATH", "")]).rstrip(
        os.pathsep
    )
    env["QG_SERIAL_PROBE_OUT"] = str(out)

    # --noconftest keeps this to a ~1s process instead of a ~15s one: the repo's root
    # conftest is imported before `pytest_cmdline_main` and costs most of that. It has no
    # bearing on the *mode* -- `-n` is resolved from argv merged with the ini file's addopts,
    # neither of which lives in a conftest. It does change what `-n auto` resolves *to*, since
    # the root conftest's `pytest_xdist_auto_num_workers` cap is not loaded; nothing below
    # asserts an exact auto count, and nothing here should start to.
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "qg_serial_probe", "--noconftest", *argv],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=env,
    )
    assert out.exists(), (
        "probe never reported; pytest exited "
        f"{result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    return json.loads(out.read_text())


@pytest.mark.timeout(300)
def test_qg_serial_target_runs_on_one_process(tmp_path: Path) -> None:
    argv = _target_pytest_argv("test-qg-serial")
    effective = _effective_xdist_config(argv, tmp_path)

    assert effective["numprocesses"] == 0, (
        "`make test-qg-serial` is not serial: pytest resolved -n to "
        f"{effective['numprocesses']!r} from argv {argv} merged with pyproject addopts. "
        "The target must pass `-n 0` explicitly to override addopts."
    )
    # -n 0 is only useful because xdist derives these from it; if that ever changes, the
    # target needs `--dist no` too, and this is where we find out.
    assert effective["dist"] == "no", effective
    assert effective["tx"] == [], effective
    assert effective["dsession"] is False, (
        f"xdist registered a distributed session for the serial target: {effective}"
    )


@pytest.mark.timeout(300)
def test_qg_target_still_runs_in_parallel(tmp_path: Path) -> None:
    """The counterpart: the fix must not have made the default gate serial too."""
    argv = _target_pytest_argv("test-qg")
    effective = _effective_xdist_config(argv, tmp_path)

    assert effective["numprocesses"] not in (0, None), effective
    assert effective["dsession"] is True, f"`make test-qg` is no longer distributed: {effective}"
