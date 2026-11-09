"""Offline startup binds lock inputs and the actual editable import target."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.runtime_clis.python_environment import seal, validate


@pytest.fixture
def installed(tmp_path):
    project = tmp_path / "project"
    source = project / "src/butlers"
    source.mkdir(parents=True)
    (source / "__init__.py").write_text("VALUE = 'first'\n")
    (project / "pyproject.toml").write_text('[project]\nname = "butlers"\nversion = "0.1.0"\n')
    (project / "uv.lock").write_text("owned fixture lock\n")
    environment = tmp_path / "environment"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(environment)], check=True)
    site = next(environment.glob("lib/python*/site-packages"))
    (site / "butlers.pth").write_text(str(project / "src") + "\n")
    metadata = site / "butlers-0.1.0.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text("Metadata-Version: 2.1\nName: butlers\nVersion: 0.1.0\n")
    (metadata / "RECORD").write_text("butlers-0.1.0.dist-info/METADATA,,\n")
    manifest = tmp_path / "environment.json"
    manifest.write_text(json.dumps(seal(environment, project, ["dev"], ["live-listener"])))
    return environment, project, manifest, site


def test_valid_environment_keeps_current_editable_source_without_resolution(installed):
    environment, project, manifest, _ = installed
    validate(manifest, environment, project)
    (project / "src/butlers/__init__.py").write_text("VALUE = 'mounted source'\n")
    validate(manifest, environment, project)
    result = subprocess.run(
        [str(environment / "bin/python"), "-I", "-c", "import butlers; print(butlers.VALUE)"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == "mounted source"


def test_actual_entrypoint_seals_checks_and_runs_without_uv(installed):
    environment, project, manifest, _ = installed
    (environment / "bin/butlers").write_text(
        "import butlers, json, sys; print(json.dumps([butlers.VALUE, *sys.argv[1:]]))\n"
    )
    script = Path(__file__).resolve().parents[2] / "scripts/runtime_clis/python_environment.py"
    options = [
        "--environment",
        str(environment),
        "--project",
        str(project),
        "--manifest",
        str(manifest),
    ]
    for action in ["seal", "check", "run"]:
        payload = ["--", "--project", "child-value"] if action == "run" else []
        result = subprocess.run(
            [sys.executable, str(script), action, *options, *payload],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        if action == "run":
            assert json.loads(result.stdout) == ["first", "--project", "child-value"]


@pytest.mark.parametrize("changed", ["lock", "project", "editable", "metadata"])
def test_stale_lock_wrong_checkout_or_dependency_graph_refuses(installed, tmp_path, changed):
    environment, project, manifest, site = installed
    if changed == "lock":
        (project / "uv.lock").write_text("new lock\n")
    elif changed == "project":
        (project / "pyproject.toml").write_text("different project\n")
    elif changed == "metadata":
        (site / "butlers-0.1.0.dist-info/METADATA").write_text("Name: butlers\nVersion: 9.0\n")
    else:
        foreign = tmp_path / "other/src/butlers"
        foreign.mkdir(parents=True)
        (foreign / "__init__.py").write_text("VALUE = 'wrong checkout'\n")
        (site / "butlers.pth").write_text(str(foreign.parent) + "\n")
    with pytest.raises((ValueError, subprocess.CalledProcessError)):
        validate(manifest, environment, project)


def test_inventory_seal_and_validation_preserve_closed_editable_source_population(installed):
    environment, project, manifest, _ = installed
    expected = {"src/butlers/__init__.py", "pyproject.toml", "uv.lock"}
    actual = {path.relative_to(project).as_posix() for path in project.rglob("*") if path.is_file()}
    assert actual == expected
    first = json.loads(manifest.read_text())
    assert first["environment"]["source_relative"] == "src/butlers/__init__.py"
    assert first["environment"]["distributions"][0]["name"] == "butlers"
    for _ in range(2):
        current = seal(environment, project, ["dev"], ["live-listener"])
        assert current == first
        validate(manifest, environment, project)
        assert {
            path.relative_to(project).as_posix() for path in project.rglob("*") if path.is_file()
        } == expected
