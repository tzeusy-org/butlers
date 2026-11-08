"""Artifact substitution verifies real bytes and preserves unrelated source."""

import hashlib
import json

import pytest

from scripts.runtime_clis.materialize_inputs import materialize


@pytest.fixture
def inputs(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    (root / "source.txt").write_text("original")
    artifact = tmp_path / "sdk.tgz"
    artifact.write_bytes(b"owned artifact")
    entry = {
        "name": "sdk",
        "path": "vendor/sdk.tgz",
        "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        "mode": 0o644,
    }
    manifest = tmp_path / "inputs.json"
    data = {"schema": "butlers.vendor-build-inputs/v1", "artifacts": [entry]}
    manifest.write_text(json.dumps(data))
    return manifest, root, {"sdk": artifact}, data


def test_exact_artifact_is_materialized_and_repeatable(inputs):
    manifest, root, supplied, _ = inputs
    first = materialize(manifest, root, supplied)
    assert materialize(manifest, root, supplied) == first
    assert (root / "vendor/sdk.tgz").read_bytes() == supplied["sdk"].read_bytes()
    assert (root / "vendor/sdk.tgz").stat().st_mode & 0o777 == 0o644
    assert (root / "source.txt").read_text() == "original"
    assert first["build_performed"] is False


@pytest.mark.parametrize("defect", ["missing", "bytes", "escape", "symlink", "existing"])
def test_missing_changed_or_unsafe_artifact_refuses_before_copy(inputs, tmp_path, defect):
    manifest, root, supplied, data = inputs
    if defect == "missing":
        supplied.clear()
    elif defect == "bytes":
        supplied["sdk"].write_bytes(b"different artifact")
    elif defect == "escape":
        data["artifacts"][0]["path"] = "../outside.tgz"
    elif defect == "symlink":
        (root / "vendor").symlink_to(tmp_path, target_is_directory=True)
    else:
        (root / "vendor").mkdir()
        (root / "vendor/sdk.tgz").write_bytes(b"preserve")
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        materialize(manifest, root, supplied)
    assert (root / "source.txt").read_text() == "original"
    assert not (tmp_path / "outside.tgz").exists()
    if defect == "existing":
        assert (root / "vendor/sdk.tgz").read_bytes() == b"preserve"
    elif defect == "symlink":
        assert supplied["sdk"].read_bytes() == b"owned artifact"
    else:
        assert not (root / "vendor/sdk.tgz").exists()
