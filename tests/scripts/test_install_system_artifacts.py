"""Closed Debian inputs fail before package-manager side effects."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture
def inputs(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "_system_artifacts", Path("scripts/runtime_clis/install_system_artifacts.py")
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    artifacts = tmp_path / "debs"
    artifacts.mkdir()
    records = []
    for name, data in (("runtime_1_amd64.deb", b"runtime"), ("compiler_1_amd64.deb", b"compiler")):
        (artifacts / name).write_bytes(data)
        records.append(
            {"filename": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        )
    value = {
        "schema": "butlers.debian-base-inputs/v1",
        "debs": records,
        "profiles": {
            "runtime": {"debs": [records[0]["filename"]]},
            "compiler": {"debs": [records[1]["filename"]]},
        },
    }
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(value))
    return module, artifacts, manifest, value


def test_exact_closed_superset_selects_each_finite_profile(inputs):
    module, artifacts, manifest, _ = inputs
    _, runtime = module.validate(manifest, artifacts, "runtime")
    _, compiler = module.validate(manifest, artifacts, "compiler")
    assert runtime == [artifacts / "runtime_1_amd64.deb"]
    assert compiler == [artifacts / "compiler_1_amd64.deb"]


@pytest.mark.parametrize("change", ["missing", "extra", "bytes", "size", "link", "directory"])
def test_changed_or_nonregular_context_refuses(inputs, change):
    module, artifacts, manifest, _ = inputs
    path = artifacts / "runtime_1_amd64.deb"
    if change == "missing":
        path.unlink()
    elif change == "extra":
        (artifacts / "foreign.deb").write_bytes(b"foreign")
    elif change == "bytes":
        path.write_bytes(b"RUNTIME")
    elif change == "size":
        path.write_bytes(b"longer-runtime")
    elif change == "link":
        path.unlink()
        path.symlink_to("compiler_1_amd64.deb")
    else:
        path.unlink()
        path.mkdir()
    with pytest.raises(ValueError):
        module.validate(manifest, artifacts, "runtime")


@pytest.mark.parametrize("change", ["duplicate", "escape", "empty-profile", "foreign-profile"])
def test_invalid_manifest_refuses(inputs, change):
    module, artifacts, manifest, value = inputs
    if change == "duplicate":
        value["debs"].append(value["debs"][0])
    elif change == "escape":
        value["debs"][0]["filename"] = "../escape.deb"
    elif change == "empty-profile":
        value["profiles"]["runtime"]["debs"] = []
    else:
        value["profiles"]["runtime"]["debs"] = ["unlisted.deb"]
    manifest.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        module.validate(manifest, artifacts, "runtime")


@pytest.mark.parametrize("corruption", [None, "bytes", "oversize", "foreign-url", "escape"])
def test_public_fetcher_publishes_only_complete_verified_inputs(inputs, monkeypatch, corruption):
    import io

    _, artifacts, manifest, value = inputs
    spec = importlib.util.spec_from_file_location(
        "_fetch_system_artifacts", Path("scripts/runtime_clis/fetch_system_artifacts.py")
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for item in value["debs"]:
        item["url"] = "http://deb.debian.org/debian/pool/main/f/fixture/" + item["filename"]
    if corruption == "foreign-url":
        value["debs"][0]["url"] = "https://foreign.invalid/fixture.deb"
    if corruption == "escape":
        value["debs"][0]["filename"] = "../escape.deb"
    manifest.write_text(json.dumps(value))
    requests = []

    class PublicInputs:
        def open(self, url, timeout):
            assert url.startswith("https://deb.debian.org/debian/pool/") and timeout == 60
            requests.append(url)
            data = (artifacts / url.rsplit("/", 1)[1]).read_bytes()
            if corruption == "bytes":
                data = b"R" + data[1:]
            if corruption == "oversize":
                data += b"extra"
            return io.BytesIO(data)

    def opener(proxy):
        assert proxy.proxies == {}
        return PublicInputs()

    monkeypatch.setattr(module.urllib.request, "build_opener", opener)
    output = manifest.parent / "context"
    if corruption:
        with pytest.raises(ValueError):
            module.fetch(manifest, output)
        assert not output.exists()
        assert not list(output.parent.glob("system-artifacts-*"))
        if corruption in {"foreign-url", "escape"}:
            assert not requests
    else:
        module.fetch(manifest, output)
        assert len(requests) == 2
        for item in value["debs"]:
            assert (output / "debs" / item["filename"]).read_bytes() == (
                artifacts / item["filename"]
            ).read_bytes()
        assert json.loads((output / "provenance.json").read_text())["archive_count"] == 2
