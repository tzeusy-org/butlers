"""Helm render and exporter-script guard for the beads export bridge (bu-viat6h.1)."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CHART = _REPO_ROOT / "deploy" / "helm" / "butlers"
_SCRIPT = _CHART / "files" / "beads_export.sh"
_ENABLE = [
    "--set", "beadsExport.enabled=true",
    "--set", "beadsExport.image=registry.invalid/bd:test",
    "--set", "beadsExport.doltHost=dolt.invalid",
    "--set", "beadsExport.credentialSecretName=beads-export-dolt",
]  # fmt: skip

needs_helm = pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")


def _render(env: str, *extra: str) -> list[dict]:
    result = subprocess.run(
        ["helm", "template", "butlers", str(_CHART), "-f", str(_CHART / "values.yaml"),
         "-f", str(_CHART / f"values.{env}.yaml"), "--set", "image.tag=t",
         "--set", "frontendImage.tag=t", *extra],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    return [d for d in yaml.safe_load_all(result.stdout) if d]


def _by_kind_name(docs: list[dict]) -> dict[tuple[str, str], dict]:
    return {(d["kind"], d["metadata"]["name"]): d for d in docs}


def _pod_spec(doc: dict) -> dict:
    if doc["kind"] == "CronJob":
        return doc["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    return doc["spec"]["template"]["spec"]


@needs_helm
@pytest.mark.parametrize("env", ["dev", "prod"])
def test_disabled_renders_no_beads_export_and_no_bead_mounts(env: str) -> None:
    docs = _render(env)
    names = _by_kind_name(docs)
    assert ("CronJob", "butlers-beads-export") not in names
    assert ("PersistentVolumeClaim", "butlers-beads-export") not in names
    for deploy in ("dashboard-api", "butlers-up"):
        spec = _pod_spec(names[("Deployment", deploy)])
        assert all(v["name"] != "beads-export" for v in spec["volumes"])
        assert all(
            m["mountPath"] != "/app/.beads"
            for c in spec["containers"]
            for m in c.get("volumeMounts", [])
        )
    # The explicit default is the same render as omitting the flag.
    assert _render(env, "--set", "beadsExport.enabled=false") == docs


@needs_helm
@pytest.mark.parametrize("env", ["dev", "prod"])
def test_enabled_mounts_pvc_read_only_as_directory_for_runtime_pods(env: str) -> None:
    names = _by_kind_name(_render(env, *_ENABLE))
    assert ("CronJob", "butlers-beads-export") in names
    assert ("PersistentVolumeClaim", "butlers-beads-export") in names
    for deploy in ("dashboard-api", "butlers-up"):
        spec = _pod_spec(names[("Deployment", deploy)])
        mounts = [
            m for c in spec["containers"] for m in c.get("volumeMounts", [])
            if m["mountPath"] == "/app/.beads"
        ]  # fmt: skip
        assert mounts == [{"name": "beads-export", "mountPath": "/app/.beads", "readOnly": True}]
        (volume,) = [v for v in spec["volumes"] if v["name"] == "beads-export"]
        assert volume["persistentVolumeClaim"]["claimName"] == "butlers-beads-export"


@needs_helm
@pytest.mark.parametrize("env", ["dev", "prod"])
def test_enabled_keeps_dolt_credential_and_host_paths_out_of_runtime_pods(env: str) -> None:
    docs = _render(env, *_ENABLE)
    for doc in docs:
        if doc["kind"] not in ("Deployment", "CronJob"):
            continue
        is_exporter = doc["metadata"]["name"] == "butlers-beads-export"
        spec = _pod_spec(doc)
        text = yaml.safe_dump(spec)
        assert (
            "beads-export-dolt" in text or "BEADS_DOLT" in text or "DOLT" in text
        ) == is_exporter, doc["metadata"]["name"]
        assert ".beads-credential-key" not in text
        assert all("hostPath" not in v for v in spec.get("volumes", []))


@needs_helm
def test_enabled_requires_image_host_and_secret() -> None:
    with pytest.raises(subprocess.CalledProcessError) as exc:
        _render("dev", "--set", "beadsExport.enabled=true")
    assert "beadsExport." in exc.value.stderr


def _run_exporter(tmp_path: Path, stub_body: str) -> subprocess.CompletedProcess[str]:
    export_dir = tmp_path / "export"
    export_dir.mkdir(exist_ok=True)
    stub = tmp_path / "bd"
    stub.write_text("#!/bin/sh\n" + stub_body)
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    return subprocess.run(
        ["sh", str(_SCRIPT)],
        env={**os.environ, "EXPORT_DIR": str(export_dir), "BD_BIN": str(stub)},
        capture_output=True, text=True,
    )  # fmt: skip


def test_exporter_writes_final_file_via_rename(tmp_path: Path) -> None:
    # The stub asserts the output is a temp name, never the final one.
    body = 'case "$3" in *issues.export.jsonl) exit 9;; esac\nprintf \'{"id":"a"}\\n\' > "$3"\n'
    result = _run_exporter(tmp_path, body)
    assert result.returncode == 0, result.stderr
    export_dir = tmp_path / "export"
    assert (export_dir / "issues.export.jsonl").read_text() == '{"id":"a"}\n'
    assert [p.name for p in export_dir.iterdir()] == ["issues.export.jsonl"]


@pytest.mark.parametrize(
    "stub_body",
    ['printf partial > "$3"\nexit 3\n', "exit 0\n", ': > "$3"\n'],
    ids=["nonzero-after-partial-write", "no-output", "empty-output"],
)
def test_exporter_failure_keeps_previous_file_byte_identical(
    tmp_path: Path, stub_body: str
) -> None:
    export_dir = tmp_path / "export"
    export_dir.mkdir()
    final = export_dir / "issues.export.jsonl"
    final.write_bytes(b'{"id":"old"}\n')
    result = _run_exporter(tmp_path, stub_body)
    assert result.returncode != 0
    assert final.read_bytes() == b'{"id":"old"}\n'
    assert [p.name for p in export_dir.iterdir()] == ["issues.export.jsonl"]
