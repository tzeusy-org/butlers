"""Static safety contract for the isolated Route A meeting-prep harness."""

from __future__ import annotations

import copy
import importlib.util
import sys
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


def test_builds_are_offline_and_contexts_exclude_dotenv_variants() -> None:
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
        assert "npm ci --offline" in (ROOT / relative).read_text(encoding="utf-8")
    config = (ROOT / "frontend/playwright.route-a.config.ts").read_text(encoding="utf-8")
    assert "requires its disposable artifact mount" in config
    assert '?? "/artifacts"' not in config
    launcher_source = (ROOT / "scripts/run_meeting_prep_route_a_evidence.py").read_text(
        encoding="utf-8"
    )
    assert (
        '"--network",\n                    "none",\n                    "--pull=false"'
        in launcher_source
    )


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
