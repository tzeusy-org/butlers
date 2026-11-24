"""record-deploy.sh execs the CLI where the image really installs it (bu-viat6h.5).

`kubectl exec` skips the image ENTRYPOINT (the sealed installed-only dispatcher) and the venv is not on PATH, so
the wrapper must name the console script by path. The wrapper tests stub kubectl and cannot see a
drift from the Dockerfile layout; this ties the two together.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def _wrapper_bin() -> str:
    match = re.search(
        r"^BUTLERS_BIN=(\S+)$", (_ROOT / "scripts/k8s/record-deploy.sh").read_text(), re.M
    )
    assert match, "record-deploy.sh must name the butlers console script in BUTLERS_BIN"
    return match.group(1)


def test_the_exec_path_is_the_project_venv_under_the_image_workdir() -> None:
    base = (_ROOT / "Dockerfile.base").read_text()
    image = (_ROOT / "Dockerfile").read_text()
    workdirs = re.findall(r"^WORKDIR (\S+)$", base, re.M)

    assert workdirs, "Dockerfile.base must set the runtime WORKDIR"
    assert _wrapper_bin() == f"{workdirs[-1]}/.venv/bin/butlers"
    assert "uv sync --frozen --offline --no-dev" in image  # creates the default project venv
    for relocating in ("UV_PROJECT_ENVIRONMENT", "VIRTUAL_ENV"):
        assert relocating not in base and relocating not in image


def test_the_image_entrypoint_still_runs_the_same_console_script() -> None:
    image = (_ROOT / "Dockerfile").read_text()

    assert (
        'ENTRYPOINT ["/usr/local/bin/python", "-I", '
        '"/app/scripts/runtime_clis/python_entrypoint.py", "butlers"]'
    ) in image
    dispatcher = (_ROOT / "scripts/runtime_clis/python_entrypoint.py").read_text()
    assert '_ENVIRONMENT = Path("/app/.venv")' in dispatcher
    assert 'environment / "bin/butlers"' in dispatcher
