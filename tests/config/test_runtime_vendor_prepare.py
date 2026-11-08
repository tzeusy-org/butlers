"""Hermetic source preparation: exact archives, finite context, and complete output."""

from __future__ import annotations

import hashlib
import io
import json
import tarfile

import pytest

from scripts.runtime_clis.prepare_vendor import prepare


def sha(content):
    return hashlib.sha256(content).hexdigest()


@pytest.fixture
def package(tmp_path):
    archive = tmp_path / "source.tgz"
    with tarfile.open(archive, "w:gz") as output:
        item = tarfile.TarInfo("sample/source.txt")
        item.size = len(b"before\n")
        item.mode = 0o644
        output.addfile(item, io.BytesIO(b"before\n"))
    patch = tmp_path / "change.patch"
    patch.write_bytes(b"--- a/source.txt\n+++ b/source.txt\n@@ -1 +1 @@\n-before\n+after\n")
    entry = ["source.txt", "file", 0o644, sha(b"after\n")]
    source_tree = sha(json.dumps(entry, separators=(",", ":")).encode() + b"\n")
    series = {
        "schema": "butlers.vendor-patch-series/v1",
        "vendor": "sample",
        "version": "1.0",
        "commit": "synthetic",
        "archive": {"sha256": sha(archive.read_bytes()), "root": "sample"},
        "patches": [
            {
                "path": "change.patch",
                "sha256": sha(patch.read_bytes()),
                "files": [
                    {
                        "path": "source.txt",
                        "before_sha256": sha(b"before\n"),
                        "after_sha256": sha(b"after\n"),
                    }
                ],
            }
        ],
        "source_tree_sha256": source_tree,
    }
    manifest = tmp_path / "series.json"
    manifest.write_text(json.dumps(series))
    return manifest, archive, tmp_path / "output", series


def test_exact_series_replays_identical_complete_source_without_a_build(package, tmp_path):
    manifest, archive, output, _ = package
    first = prepare(manifest, archive, output)
    second = prepare(manifest, archive, tmp_path / "repeated")
    assert first == second
    assert (output / "source.txt").read_bytes() == b"after\n"
    assert first["patches"] == 1
    assert first["build_performed"] is False


@pytest.mark.parametrize("change", ["archive", "patch", "context", "output", "tree", "unsafe_path"])
def test_input_or_output_drift_refuses_without_publishing_destination(package, change):
    manifest, archive, output, series = package
    patch = series["patches"][0]
    item = patch["files"][0]
    if change == "archive":
        series["archive"]["sha256"] = "0" * 64
    elif change == "patch":
        patch["sha256"] = "0" * 64
    elif change == "context":
        item["before_sha256"] = "0" * 64
    elif change == "output":
        item["after_sha256"] = "0" * 64
    elif change == "tree":
        series["source_tree_sha256"] = "0" * 64
    else:
        item["path"] = "../outside.txt"
    manifest.write_text(json.dumps(series))
    with pytest.raises(ValueError):
        prepare(manifest, archive, output)
    assert not output.exists()


def test_existing_destination_is_preserved(package):
    manifest, archive, output, _ = package
    output.mkdir()
    (output / "owned.txt").write_text("preserved")
    with pytest.raises(ValueError, match="already exists"):
        prepare(manifest, archive, output)
    assert (output / "owned.txt").read_text() == "preserved"
