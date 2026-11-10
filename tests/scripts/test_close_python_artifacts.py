"""Closed seed assembly checks; no resolver, installer or image is executed."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def _module(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, Path("scripts/runtime_clis") / file)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def seed(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    for name in ("pyproject.toml", "uv.lock"):
        (project / name).write_text("synthetic project metadata\n")
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    (wheelhouse / "fixture-1-py3-none-any.whl").write_bytes(b"synthetic wheel bytes")
    cache = tmp_path / "cache"
    (cache / "archive" / "fixture").mkdir(parents=True)
    (cache / "archive" / "fixture" / "payload.py").write_bytes(b"synthetic payload bytes")
    (cache / "reference").symlink_to("archive/fixture", target_is_directory=True)
    constraints = tmp_path / "constraints.txt"
    constraints.write_text("hatchling==1.33.0\neditables==0.6\n")
    return dict(
        project=project,
        wheelhouse=wheelhouse,
        cache=cache,
        constraints=constraints,
        output=tmp_path / "closed",
        extras=["live-listener", "live-listener"],
    )


def test_closed_context_matches_image_validator_and_owns_independent_bytes(seed):
    producer = _module("_closed_python_artifacts", "close_python_artifacts.py")
    image = _module("_closed_python_image_validator", "python_entrypoint.py")
    original = seed["cache"] / "archive/fixture/payload.py"
    original.chmod(0o6751)
    value = producer.close_context(**seed)
    output = seed["output"]
    assert value["profiles"] == {
        "production": {"groups": [], "extras": ["live-listener"]},
        "development": {"groups": ["dev"], "extras": ["live-listener"]},
    }
    assert len(value["inputs"]) == 4
    assert all(not path.is_symlink() for path in output.rglob("*"))
    assert (output / "uv-cache/archive/fixture/payload.py").stat().st_mode & 0o7777 == 0o755
    assert (output / "uv-cache/reference/payload.py").stat().st_mode & 0o7777 == 0o755
    assert next((output / "wheelhouse").iterdir()).stat().st_mode & 0o7777 == 0o644
    image.verify_build(output, seed["project"], "production", ["live-listener"])
    image.verify_build(output, seed["project"], "development", ["live-listener"])
    copied = output / "uv-cache/reference/payload.py"
    assert original.stat().st_ino != copied.stat().st_ino
    original.write_bytes(b"later seed mutation")
    assert copied.read_bytes() == b"synthetic payload bytes"
    image.verify_build(output, seed["project"], "production", ["live-listener"])
    copied.write_bytes(b"context drift")
    with pytest.raises(ValueError, match="changed inputs"):
        image.verify_build(output, seed["project"], "production", ["live-listener"])


@pytest.mark.parametrize("kind", ["escape", "cycle", "dangling", "special"])
def test_invalid_cache_entry_refuses_without_partial_output(seed, tmp_path, kind):
    producer = _module("_closed_python_artifacts_invalid", "close_python_artifacts.py")
    path = seed["cache"] / "invalid"
    if kind == "escape":
        external = tmp_path / "external"
        external.write_bytes(b"outside declared seed")
        path.symlink_to(external)
    elif kind == "cycle":
        path.symlink_to(".", target_is_directory=True)
    elif kind == "dangling":
        path.symlink_to("missing")
    else:
        import os

        os.mkfifo(path)
    with pytest.raises((ValueError, OSError, RuntimeError)):
        producer.close_context(**seed)
    assert not seed["output"].exists()
    assert not list(tmp_path.glob("python-artifacts-*"))


@pytest.mark.parametrize("kind", ["existing", "overlap", "constraint", "project", "wheel", "extra"])
def test_invalid_context_inputs_refuse_and_preserve_existing_output(seed, kind):
    producer = _module("_closed_python_artifacts_inputs", "close_python_artifacts.py")
    if kind == "existing":
        seed["output"].mkdir()
        (seed["output"] / "survivor").write_text("preserve")
    elif kind == "overlap":
        seed["output"] = seed["cache"] / "nested"
    elif kind == "constraint":
        seed["constraints"].write_text("")
    elif kind == "project":
        (seed["project"] / "uv.lock").unlink()
    elif kind == "wheel":
        next(seed["wheelhouse"].iterdir()).unlink()
    else:
        seed["extras"] = ["../outside"]
    with pytest.raises(ValueError):
        producer.close_context(**seed)
    if kind == "existing":
        assert (seed["output"] / "survivor").read_text() == "preserve"
    else:
        assert not seed["output"].exists()
