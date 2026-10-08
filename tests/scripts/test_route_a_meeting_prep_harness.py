"""Static safety contract for the isolated Route A meeting-prep harness."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


launcher = _load("route_a_launcher", "scripts/run_meeting_prep_route_a_evidence.py")
fixture = _load("route_a_fixture", "scripts/route_a_meeting_prep_fixture.py")


def test_compose_topology_is_exactly_the_route_a_allowlist(tmp_path: Path) -> None:
    compose = launcher.load_compose()
    launcher.validate_compose(compose, worktree=ROOT, artifact_dir=tmp_path)
    assert set(compose["services"]) == launcher.ALLOWED_SERVICES
    assert compose["networks"]["evidence-db"]["internal"] is True
    assert compose["networks"]["evidence-ui"]["internal"] is True


def test_compose_never_references_the_stock_launcher_or_host_ports() -> None:
    source = (ROOT / "docker-compose.meeting-prep-evidence.yml").read_text(encoding="utf-8")
    assert "scripts/compose.sh" not in source
    assert "docker-compose.yml" not in source
    assert "ports:" not in source
    assert "egress" not in source


def test_launcher_waits_for_the_service_graph_then_returns_the_browser_exit_code() -> None:
    source = (ROOT / "scripts/run_meeting_prep_route_a_evidence.py").read_text(encoding="utf-8")
    assert "_write_provenance_receipt" in source
    assert "dashboard runtime GIT_SHA does not match" in source
    assert '"--wait"' in source
    assert 'arguments=["run", "--build", "--no-deps", "--rm", "browser"]' in source
    assert "--abort-on-container-exit" not in source


def test_bootstrap_preserves_postgres_dollar_quotes_through_compose_and_shell() -> None:
    command = launcher.load_compose()["services"]["bootstrap"]["command"]
    # Compose reduces $$$$ to $$; the shell single quotes keep that $$ literal
    # so PostgreSQL, not /bin/sh, sees the dollar-quoted DO block.
    assert "'DO $$$$ BEGIN" in command
    assert '"DO $$$$ BEGIN' not in command
    assert "rolname = '\\''route_a_app'\\''" in command


def test_environment_rejects_ambient_credentials() -> None:
    with pytest.raises(launcher.SafetyError, match="credential"):
        launcher.validate_ambient_environment({"BWS_TOKEN": "never-forward"})
    with pytest.raises(launcher.SafetyError, match="credential"):
        launcher.validate_ambient_environment({"POSTGRES_PASSWORD": "never-forward"})


def test_launcher_rejects_and_scrubs_ambient_route_a_compose_and_docker_inputs(
    tmp_path: Path,
) -> None:
    with pytest.raises(launcher.SafetyError, match="Route A, Compose, or Docker"):
        launcher.validate_ambient_environment({"ROUTE_A_ARTIFACT_DIR": "/home/unsafe"})
    with pytest.raises(launcher.SafetyError, match="Route A, Compose, or Docker"):
        launcher.validate_ambient_environment({"DOCKER_HOST": "tcp://unsafe.example"})

    generated = {"ROUTE_A_ARTIFACT_DIR": "/tmp/route-a-safe"}
    docker_env = launcher.docker_environment(generated, tmp_path.resolve())
    assert docker_env["ROUTE_A_ARTIFACT_DIR"] == generated["ROUTE_A_ARTIFACT_DIR"]
    assert "COMPOSE_FILE" not in docker_env
    assert "DOCKER_HOST" not in docker_env


def test_launcher_requires_its_own_validated_worktree() -> None:
    with pytest.raises(launcher.SafetyError, match="validated Route A worktree"):
        launcher.compose_file_for(Path("/tmp/not-the-route-a-worktree"))


def test_generated_environment_requires_pinned_images_and_tmp_artifacts(tmp_path: Path) -> None:
    digest = "example.invalid/image@sha256:" + "a" * 64
    values = launcher.route_a_environment(
        target_sha="cfe0f848bce0596e4d17ea58dab7b56957aa5e49",
        artifact_dir=Path("/tmp/route-a-test-artifacts"),
        base_image=digest,
        go_image=digest,
        go_deps_image=digest,
        uv_cache_image=digest,
        npm_cache_image=digest,
        postgres_image=digest,
        node_image=digest,
        playwright_image=digest,
        project="routea-cfe0f848-0123456789ab",
    )
    assert "POSTGRES_PASSWORD" not in values
    with pytest.raises(launcher.SafetyError, match="digest-pinned"):
        launcher.route_a_environment(
            target_sha="cfe0f848bce0596e4d17ea58dab7b56957aa5e49",
            artifact_dir=tmp_path,
            base_image=digest,
            go_image=digest,
            go_deps_image=digest,
            uv_cache_image=digest,
            npm_cache_image=digest,
            postgres_image="postgres:latest",
            node_image=digest,
            playwright_image=digest,
            project="routea-cfe0f848-0123456789ab",
        )


def test_rendered_compose_cannot_bypass_the_bind_allowlist(tmp_path: Path) -> None:
    compose = copy.deepcopy(launcher.load_compose())
    compose["services"]["browser"]["volumes"][0]["source"] = str(tmp_path / "outside-artifacts")
    with pytest.raises(launcher.SafetyError, match="bind mounts exceed"):
        launcher.validate_compose(
            compose,
            worktree=ROOT,
            artifact_dir=tmp_path / "route-a-artifacts",
            rendered=True,
        )


def test_builds_are_offline_and_contexts_exclude_dotenv_variants(tmp_path: Path) -> None:
    compose = launcher.load_compose()
    for service in ("frontend", "browser"):
        build = compose["services"][service]["build"]
        assert build["network"] == "none"
        assert build["pull"] is False
    assert all(service["pull_policy"] == "never" for service in compose["services"].values())
    assert ".env*" in (ROOT / ".dockerignore").read_text(encoding="utf-8")
    assert ".env*" in (ROOT / "frontend/.dockerignore").read_text(encoding="utf-8")
    for relative in (
        "frontend/Dockerfile.meeting-prep-evidence",
        "frontend/Dockerfile.meeting-prep-browser",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "ARG ROUTE_A_NPM_CACHE_IMAGE" in source
        assert "FROM ${ROUTE_A_NPM_CACHE_IMAGE} AS route-a-npm-cache" in source
        assert "COPY --from=route-a-npm-cache" in source
        assert "npm ci --offline --cache=/root/.npm" in source
    route_a_dockerfile = (ROOT / launcher.ROUTE_A_DOCKERFILE).read_text(encoding="utf-8")
    assert "# syntax=" not in route_a_dockerfile
    assert "FROM ${ROUTE_A_GO_DEPS_IMAGE} AS route-a-go-deps" in route_a_dockerfile
    assert "FROM ${ROUTE_A_UV_CACHE_IMAGE} AS route-a-uv-cache" in route_a_dockerfile
    assert "COPY --from=route-a-go-deps" in route_a_dockerfile
    assert "COPY --from=route-a-uv-cache" in route_a_dockerfile
    assert "GOPROXY=off" in route_a_dockerfile
    assert "uv sync --offline" in route_a_dockerfile
    config = (ROOT / "frontend/playwright.route-a.config.ts").read_text(encoding="utf-8")
    assert "requires all isolated fixture and artifact variables" in config
    assert '?? "/artifacts"' not in config
    launcher_source = (ROOT / "scripts/run_meeting_prep_route_a_evidence.py").read_text(
        encoding="utf-8"
    )
    assert (
        '"--network",\n                    "none",\n                    "--pull=false"'
        in launcher_source
    )
    launcher.validate_sealed_build_inputs(ROOT)
    workflow = launcher.yaml.safe_load(
        (ROOT / ".github/workflows/migration-chain-main.yml").read_text()
    )
    events = workflow.get("on", workflow.get(True))
    assert events["workflow_dispatch"]["inputs"]["offline-route-a-build-proof"]["default"] is False
    proof_job = workflow["jobs"]["offline-route-a-build-proof"]
    guard = proof_job["steps"][0]
    assert guard["name"] == "Reject conflicting manual build modes"
    for flag, expected in (("false", 0), ("", 0), ("true", 2)):
        done = subprocess.run(
            ["bash", "-eu", "-c", guard["run"]],
            env={**os.environ, "OTHER_BUILD_MODE": flag},
            capture_output=True,
            timeout=5,
            check=False,
        )
        assert done.returncode == expected
    recipes = (
        launcher.ROUTE_A_DOCKERFILE,
        "frontend/Dockerfile.meeting-prep-evidence",
        "frontend/Dockerfile.meeting-prep-browser",
    )
    for relative in recipes:
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text((ROOT / relative).read_text())
    for relative, argument, alias in (
        (recipes[0], "ROUTE_A_GO_DEPS_IMAGE", "route-a-go-deps"),
        (recipes[0], "ROUTE_A_UV_CACHE_IMAGE", "route-a-uv-cache"),
        (recipes[1], "ROUTE_A_NPM_CACHE_IMAGE", "route-a-npm-cache"),
        (recipes[2], "ROUTE_A_NPM_CACHE_IMAGE", "route-a-npm-cache"),
    ):
        recipe = tmp_path / relative
        valid = recipe.read_text()
        for invalid in (
            valid.replace(f"COPY --from={alias}", f"COPY --from=${{{argument}}}"),
            valid.replace(f"ARG {argument}\n", ""),
            valid.replace(f" AS {alias}", " AS wrong-cache"),
            valid.replace(f" AS {alias}", " AS "),
            valid + f"\nFROM scratch AS {alias}\n",
        ):
            recipe.write_text(invalid)
            with pytest.raises(launcher.SafetyError, match="sealed Route A input"):
                launcher.validate_sealed_build_inputs(tmp_path)
        recipe.write_text(valid)
        launcher.validate_sealed_build_inputs(tmp_path)


def test_dependency_cache_contracts_bind_cache_images_to_the_current_locks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    contracts = launcher.dependency_cache_contracts(ROOT)
    assert set(contracts) == {
        "ROUTE_A_GO_DEPS_IMAGE",
        "ROUTE_A_UV_CACHE_IMAGE",
        "ROUTE_A_NPM_CACHE_IMAGE",
    }
    assert contracts["ROUTE_A_GO_DEPS_IMAGE"][launcher.CACHE_KIND_LABEL] == "go-modules"
    assert contracts["ROUTE_A_UV_CACHE_IMAGE"][launcher.CACHE_KIND_LABEL] == "uv-cache"
    assert contracts["ROUTE_A_NPM_CACHE_IMAGE"][launcher.CACHE_KIND_LABEL] == "npm-cache"
    assert all(
        len(contract[launcher.CACHE_INPUT_SHA_LABEL]) == 64 for contract in contracts.values()
    )
    monkeypatch.setitem(sys.modules, "run_meeting_prep_route_a_evidence", launcher)
    proof = _load("route_a_build_proof", "scripts/prove_route_a_offline_builds.py")
    # Closed diagnostic software controls, never actual builder evidence.
    recipe = tmp_path / "public-control.Dockerfile"
    recipe.write_text("FROM scratch\n\nCOPY sentinel /sentinel\n")
    failure = (
        b'Dockerfile:3\nfailed to parse stage name "${CACHE}": invalid reference format\n'
        b"synthetic-private-output-must-not-leave-the-process\n"
    )
    for stdout, stderr in ((failure, b""), (b"", failure)):
        assert proof.closed_build_failure(stderr, stdout) == "invalid_copy_or_image_reference"
        capsule = proof.closed_builder_diagnostic(stdout, stderr, recipe)
        assert capsule["indicators"]["invalid_reference_format"] is True
        assert capsule["indicators"]["stage_name_parse_failure"] is True
        assert capsule["recipe_line_numbers"] == [3]
        assert capsule["recipe_instructions"] == ["COPY"]
        assert "synthetic-private-output" not in json.dumps(capsule)
        assert "${CACHE}" not in json.dumps(capsule)
    unrelated = (
        b"Dockerfile:2\nDockerfile:999\nfailed to resolve source metadata: permission denied"
    )
    assert proof.closed_build_failure(unrelated) == "unclassified_builder_failure"
    capsule = proof.closed_builder_diagnostic(b"", unrelated, recipe)
    assert capsule["recipe_line_numbers"] == [2]
    assert capsule["recipe_instructions"] == ["OTHER"]
    assert capsule["indicators"]["source_metadata_resolution_failure"] is True
    assert capsule["indicators"]["permission_denied"] is True
    bounded = proof.closed_builder_diagnostic(b"x" * 70000, failure, recipe)
    assert bounded["complete_streams_examined"] is False
    assert bounded["stdout_bytes"] == 70000
    assert bounded["indicators"]["invalid_reference_format"] is True
    labels = contracts["ROUTE_A_NPM_CACHE_IMAGE"]
    config = json.dumps({"config": {"Labels": labels}}).encode()
    config_sha = hashlib.sha256(config).hexdigest()
    manifest = json.dumps(
        {
            "config": {
                "mediaType": "application/vnd.oci.image.config.v1+json",
                "digest": "sha256:" + config_sha,
                "size": len(config),
            },
            "layers": [],
        }
    ).encode()
    manifest_sha = hashlib.sha256(manifest).hexdigest()
    members = {
        "oci-layout": b'{"imageLayoutVersion":"1.0.0"}',
        "index.json": json.dumps(
            {
                "manifests": [
                    {
                        "mediaType": "application/vnd.oci.image.manifest.v1+json",
                        "digest": "sha256:" + manifest_sha,
                        "size": len(manifest),
                    }
                ]
            }
        ).encode(),
        "blobs/sha256/" + manifest_sha: manifest,
        "blobs/sha256/" + config_sha: config,
    }

    def archive(name: str, payloads: dict[str, bytes]) -> Path:
        path = tmp_path / (name + ".tar")
        with tarfile.open(path, "w") as output:
            for name_, body in payloads.items():
                member = tarfile.TarInfo(name_)
                member.size = len(body)
                output.addfile(member, io.BytesIO(body))
        return path

    # Software OCI validation only, never credited as actual Docker builds.
    sealed = proof.seal_oci(archive("valid", members), tmp_path / "valid", labels)
    assert sealed["manifest_digest"] == "sha256:" + manifest_sha
    assert sealed["config_digest"] == "sha256:" + config_sha
    assert sealed["all_blob_sizes_and_digests_verified"] is True
    with pytest.raises(proof.ProofRefusal, match="blob_mismatch"):
        proof.seal_oci(
            archive("corrupt", {**members, "blobs/sha256/" + config_sha: b"corrupt"}),
            tmp_path / "corrupt",
            labels,
        )
    with pytest.raises(proof.ProofRefusal, match="label_mismatch"):
        proof.seal_oci(
            archive("wrong_label", members), tmp_path / "wrong_label", {"wrong": "label"}
        )
    with pytest.raises(proof.ProofRefusal, match="invalid_oci_archive"):
        proof.seal_oci(archive("outside", {"../outside": b"sentinel"}), tmp_path / "outside", {})
    with pytest.raises(proof.ProofRefusal, match="hosted_exact_source_required"):
        proof.run_proof(tmp_path / "must-not-build", "invalid")
    assert not (tmp_path / "must-not-build").exists()
    # Actual Git mismatch refuses before Docker; cleanup must retain that phase.
    # The synthetic hosted flag is a software receipt control, not hosted proof.
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr(proof, "validate_ambient_environment", lambda _: None)
    refused = tmp_path / "source-refused"
    assert proof.run_proof(refused, "b" * 40) == 1
    refused_receipt = json.loads((refused / "offline-build-receipt.json").read_text())
    assert refused_receipt["failed_stage"] == "source_validation"
    assert refused_receipt["refusal"] == "source_mismatch"
    assert refused_receipt["active_stage"] == "cleanup"
    historical = ROOT / "tests/fixtures/route_a_9ff_offline_recipes"
    source = json.loads((historical / "source-manifest.json").read_text())
    assert source["source"] == proof.BASE_SOURCE
    for relative, expected in source["recipes"].items():
        assert proof.digest(historical / relative) == expected


def test_preloaded_cache_image_must_match_its_sealed_lock_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image = "cache@sha256:" + "a" * 64

    def inspect(arguments, *, env, cwd=ROOT):
        if "{{json .RepoDigests}}" in arguments:
            return json.dumps([image])
        if "{{json .Config.Labels}}" in arguments:
            return json.dumps(
                {launcher.CACHE_KIND_LABEL: "npm-cache", launcher.CACHE_INPUT_SHA_LABEL: "b" * 64}
            )
        raise AssertionError(arguments)

    monkeypatch.setattr(launcher, "_docker_run", inspect)
    launcher._assert_local_pinned_image(
        image,
        label="npm cache",
        docker_env={},
        required_labels={
            launcher.CACHE_KIND_LABEL: "npm-cache",
            launcher.CACHE_INPUT_SHA_LABEL: "b" * 64,
        },
    )
    with pytest.raises(launcher.SafetyError, match="sealed dependency contract"):
        launcher._assert_local_pinned_image(
            image,
            label="npm cache",
            docker_env={},
            required_labels={
                launcher.CACHE_KIND_LABEL: "npm-cache",
                launcher.CACHE_INPUT_SHA_LABEL: "c" * 64,
            },
        )


def test_normalized_compose_receipt_binds_a_sanitized_stable_topology(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "route-a-artifacts"
    config = {
        "name": "routea-cfe0f848-0123456789ab",
        "services": {"browser": {"volumes": [{"source": str(artifact_dir)}]}},
    }
    normalized, digest = launcher.normalized_compose_receipt(
        config,
        artifact_dir=artifact_dir,
        project="routea-cfe0f848-0123456789ab",
    )
    serialized = json.dumps(normalized, sort_keys=True)
    assert str(artifact_dir) not in serialized
    assert "routea-cfe0f848-0123456789ab" not in serialized
    assert "<ROUTE_A_ARTIFACT_DIR>" in serialized
    assert len(digest) == 64


def test_provenance_binds_compose_artifact_and_every_generated_image(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    values = {
        "ROUTE_A_APP_IMAGE": "app",
        "ROUTE_A_FRONTEND_IMAGE": "frontend",
        "ROUTE_A_BROWSER_IMAGE": "browser",
        "ROUTE_A_BASE_IMAGE": "base@sha256:" + "a" * 64,
        "ROUTE_A_GO_IMAGE": "go@sha256:" + "a" * 64,
        "ROUTE_A_GO_DEPS_IMAGE": "go-cache@sha256:" + "a" * 64,
        "ROUTE_A_UV_CACHE_IMAGE": "uv-cache@sha256:" + "a" * 64,
        "ROUTE_A_NPM_CACHE_IMAGE": "npm-cache@sha256:" + "a" * 64,
        "ROUTE_A_POSTGRES_IMAGE": "postgres@sha256:" + "a" * 64,
        "ROUTE_A_NODE_IMAGE": "node@sha256:" + "a" * 64,
        "ROUTE_A_PLAYWRIGHT_IMAGE": "playwright@sha256:" + "a" * 64,
    }
    monkeypatch.setattr(
        launcher,
        "_generated_image_receipts",
        lambda _values, *, docker_env: {
            "app": {"reference": "app", "image_id": "sha256:app"},
            "frontend": {"reference": "frontend", "image_id": "sha256:frontend"},
            "browser": {"reference": "browser", "image_id": "sha256:browser"},
        },
    )
    monkeypatch.setattr(
        launcher,
        "_docker_run",
        lambda arguments, *, env, cwd=ROOT: "GIT_SHA=abc123\n" if arguments[0] == "inspect" else "",
    )
    launcher._write_provenance_receipt(
        artifact_dir=tmp_path,
        target_sha="abc123",
        project="routea-abc12345-0123456789ab",
        values=values,
        docker_env={},
        dashboard_container="dashboard",
        sanitized_compose_sha256="b" * 64,
    )
    receipt = json.loads((tmp_path / "provenance.json").read_text(encoding="utf-8"))
    assert receipt["artifact_dir"] == str(tmp_path)
    assert receipt["sanitized_normalized_compose_sha256"] == "b" * 64
    assert set(receipt["generated_images"]) == {"app", "frontend", "browser"}


def test_teardown_records_all_stages_after_down_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[str] = []
    values = {
        "ROUTE_A_TARGET_SHA": "cfe0f848bce0596e4d17ea58dab7b56957aa5e49",
        "COMPOSE_PROJECT_NAME": "routea-cfe0f848-0123456789ab",
    }

    def fail_down(*_args, **_kwargs):
        raise subprocess.CalledProcessError(42, ["docker", "compose", "down"])

    monkeypatch.setattr(launcher, "_run", fail_down)
    monkeypatch.setattr(
        launcher,
        "_remove_generated_images",
        lambda _values, _docker_env: calls.append("remove_generated_images"),
    )
    monkeypatch.setattr(launcher, "_docker_run", lambda _arguments, *, env, cwd=ROOT: "")
    with pytest.raises(launcher.SafetyError, match="teardown was incomplete"):
        launcher.run_project_teardown(
            worktree=ROOT,
            compose_file=ROOT / "docker-compose.meeting-prep-evidence.yml",
            project=values["COMPOSE_PROJECT_NAME"],
            env_file=tmp_path / "route-a.env",
            docker_env={},
            values=values,
            artifact_dir=tmp_path,
        )
    receipt = json.loads((tmp_path / "teardown.json").read_text(encoding="utf-8"))
    assert calls == ["remove_generated_images"]
    assert receipt["outcomes"][0]["stage"] == "compose_down"
    assert receipt["outcomes"][0]["status"] == "failed"
    assert all(not identifiers for identifiers in receipt["residuals"].values())


def test_fixture_commitments_are_allowlisted_and_synthetic() -> None:
    payload = fixture._commitment_payload()
    assert all(set(item) == fixture.ALLOWED_COMMITMENT_KEYS for item in payload)
    assert {item["escalation_level"] for item in payload} == {"L1", "L3"}
    assert all(item["summary"].startswith("Route A synthetic ") for item in payload)
    # The Calendar workspace only renders MeetingPrepRail for provider events.
    assert fixture.PROVIDER_EVENT_SOURCE_KIND == "provider_event"


def test_fixture_rejects_non_synthetic_summary() -> None:
    commitment = fixture.SyntheticCommitment(
        kind="promise",
        direction="owner_to_other",
        summary="Personal data must not enter Route A",
        deadline=None,
        escalation_level="L3",
        fingerprint="route-a-test",
    )
    with pytest.raises(ValueError, match="Route A prefix"):
        commitment.validate()


def test_fixture_requires_the_exact_disposable_database_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = {
        "POSTGRES_HOST": "postgres",
        "POSTGRES_PORT": "5432",
        "POSTGRES_DB": "route_a",
        "POSTGRES_USER": "route_a_app",
        "POSTGRES_SSLMODE": "disable",
    }
    for key, value in expected.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    assert fixture._environment() == expected

    monkeypatch.setenv("POSTGRES_DB", "other")
    with pytest.raises(RuntimeError, match="exactly the declared disposable database identity"):
        fixture._environment()
