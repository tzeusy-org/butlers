"""Helm render and bootstrap-secrets.sh guard for localSecrets.source (bu-viat6h.2)."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CHART = _REPO_ROOT / "deploy" / "helm" / "butlers"
_SCRIPT = _REPO_ROOT / "scripts" / "k8s" / "bootstrap-secrets.sh"
_FIXTURE_VALUE = "fixture-secret-value-d41d8cd9"

needs_helm = pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")


def _render(env: str, *extra: str) -> list[dict]:
    result = subprocess.run(
        ["helm", "template", "butlers", str(_CHART), "-f", str(_CHART / "values.yaml"),
         "-f", str(_CHART / f"values.{env}.yaml"), "--set", "image.tag=t",
         "--set", "frontendImage.tag=t", *extra],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    return [d for d in yaml.safe_load_all(result.stdout) if d]


def _external_secrets(docs: list[dict]) -> dict[str, dict]:
    return {d["spec"]["target"]["name"]: d for d in docs if d["kind"] == "ExternalSecret"}


def _without_external_secrets(docs: list[dict]) -> list[dict]:
    return [d for d in docs if d["kind"] != "ExternalSecret"]


@needs_helm
@pytest.mark.parametrize("env", ["dev", "prod"])
def test_source_bws_adds_exactly_the_two_listed_externalsecrets(env: str) -> None:
    local = _render(env)
    bws = _render(env, "--set", "localSecrets.source=bws")

    assert len(_external_secrets(local)) == 1
    stores = _external_secrets(bws)
    assert len(stores) == 3
    assert {d["metadata"]["name"] for d in bws if d["kind"] == "ExternalSecret"} == set(stores)

    probe = stores["butlers-runtime-probe-control"]["spec"]
    env_es = stores["butlers-local-env"]["spec"]
    assert {e["secretKey"] for e in probe["data"]} == {
        "runtime_probe_control_signing_key",
        "runtime_probe_control_verifiers",
    }
    assert {e["secretKey"] for e in env_es["data"]} == {
        "DASHBOARD_API_KEY",
        "DASHBOARD_AUTH_DB_USER",
    }
    for spec in (probe, env_es):
        assert spec["target"]["creationPolicy"] == "Owner"
        assert "dataFrom" not in spec
        assert all(e["remoteRef"]["key"] and "*" not in e["remoteRef"]["key"] for e in spec["data"])


@needs_helm
def test_source_bws_remote_keys_come_from_values_and_optional_key_can_be_omitted() -> None:
    docs = _render(
        "dev",
        "--set", "localSecrets.source=bws",
        "--set", "externalSecrets.runtimeProbeControl.signingKey=CUSTOM_SIGNER",
        "--set", "externalSecrets.localEnv.DASHBOARD_API_KEY=",
    )  # fmt: skip
    stores = _external_secrets(docs)
    probe_keys = {
        e["secretKey"]: e["remoteRef"]["key"]
        for e in stores["butlers-runtime-probe-control"]["spec"]["data"]
    }
    assert probe_keys["runtime_probe_control_signing_key"] == "CUSTOM_SIGNER"
    env_keys = [e["secretKey"] for e in stores["butlers-local-env"]["spec"]["data"]]
    assert env_keys == ["DASHBOARD_AUTH_DB_USER"]


@needs_helm
@pytest.mark.parametrize("env", ["dev", "prod"])
def test_source_bws_leaves_every_workload_manifest_unchanged(env: str) -> None:
    local = _without_external_secrets(_render(env))
    bws = _without_external_secrets(_render(env, "--set", "localSecrets.source=bws"))
    assert local == bws


@needs_helm
def test_signing_key_is_referenced_only_by_dashboard_api() -> None:
    docs = _render("dev", "--set", "localSecrets.source=bws")
    referencing = {
        d["metadata"]["name"]
        for d in docs
        if d["kind"] != "ExternalSecret" and "runtime_probe_control_signing_key" in yaml.dump(d)
    }
    assert referencing == {"dashboard-api"}


def _run_script(tmp_path: Path, owner_kind: str) -> tuple[subprocess.CompletedProcess[str], str]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    calls = tmp_path / "calls"
    calls.touch()
    stub = fake_bin / "kubectl"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'printf "kubectl %s\\n" "$*" >> "$TEST_CALLS"\n'
        'case "$*" in *"get secret"*) printf "%s\\n" "$TEST_OWNER_KIND"; exit 0;; esac\n'
        "exit 0\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    env_file = tmp_path / "env"
    env_file.write_text(f"DASHBOARD_API_KEY={_FIXTURE_VALUE}\n", encoding="utf-8")
    result = subprocess.run(
        [str(_SCRIPT), "dev"],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "TEST_CALLS": str(calls),
            "TEST_OWNER_KIND": owner_kind,
            "BUTLERS_ENV_FILE": str(env_file),
        },
    )
    return result, calls.read_text(encoding="utf-8")


def test_bootstrap_refuses_externalsecret_owned_secret_without_applying(tmp_path: Path) -> None:
    result, calls = _run_script(tmp_path, "ExternalSecret")

    assert result.returncode != 0
    assert "owned by an ExternalSecret" in result.stderr
    assert "apply" not in calls and "create" not in calls
    assert _FIXTURE_VALUE not in result.stdout + result.stderr + calls


def test_bootstrap_still_applies_when_secret_is_not_externalsecret_owned(tmp_path: Path) -> None:
    result, calls = _run_script(tmp_path, "")

    assert result.returncode == 0, result.stderr
    assert "apply -f -" in calls
    assert _FIXTURE_VALUE not in result.stdout + result.stderr
