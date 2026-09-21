# /// script
# requires-python = ">=3.12"
# dependencies = ["pyyaml>=6"]
# ///
"""Fail-closed launcher for isolated Route A meeting-prep browser evidence."""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILENAME = "docker-compose.meeting-prep-evidence.yml"
COMPOSE_FILE = REPO_ROOT / COMPOSE_FILENAME
ALLOWED_SERVICES = frozenset(
    {
        "postgres",
        "bootstrap",
        "migrations",
        "fixture-loader",
        "dashboard-api",
        "frontend",
        "browser",
    }
)
ALLOWED_NETWORKS = frozenset({"evidence-db", "evidence-ui"})
ALLOWED_VOLUMES = frozenset({"postgres-data"})
SERVICE_NETWORKS = {
    "postgres": frozenset({"evidence-db"}),
    "bootstrap": frozenset({"evidence-db"}),
    "migrations": frozenset({"evidence-db"}),
    "fixture-loader": frozenset({"evidence-db"}),
    "dashboard-api": frozenset({"evidence-db", "evidence-ui"}),
    "frontend": frozenset({"evidence-ui"}),
    "browser": frozenset({"evidence-ui"}),
}
DENIED_ENV_PREFIXES = ("BWS_", "AWS_", "GOOGLE_", "SPOTIFY_", "OPENAI_", "ANTHROPIC_")
CONTROLLED_ENV_PREFIXES = ("ROUTE_A_", "COMPOSE_", "DOCKER_")
DENIED_ENV_NAMES = frozenset(
    {
        "POSTGRES_PASSWORD",
        "DATABASE_URL",
        "DASHBOARD_API_KEY",
        "DASHBOARD_AUTH_DB_PASSWORD",
        "GOOGLE_OAUTH_CLIENT_SECRET",
    }
)
BUILD_SERVICES = {
    "frontend": "Dockerfile.meeting-prep-evidence",
    "browser": "Dockerfile.meeting-prep-browser",
}
PROJECT_LABEL = "org.butlers.route-a.project"
TARGET_SHA_LABEL = "org.butlers.route-a.target-sha"
GO_BUILDER_IMAGE = (
    "golang:1.25.11-bookworm@sha256:"
    "bbb255b0e131db500cf0520adc97441d2260cf629c7fa7e39e025ddf53995a24"
)
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
PINNED_IMAGE_RE = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")


class SafetyError(RuntimeError):
    """Raised before the launcher can create or remove a Docker resource."""


def _run(
    command: Sequence[str],
    *,
    cwd: Path = REPO_ROOT,
    env: Mapping[str, str] | None = None,
) -> str:
    completed = subprocess.run(
        list(command),
        cwd=cwd,
        env=dict(env) if env is not None else None,
        check=True,
        text=True,
        capture_output=True,
    )
    return completed.stdout


def _git(worktree: Path, *args: str) -> str:
    return _run(["git", "-C", str(worktree), *args], cwd=worktree).strip()


def compose_file_for(worktree: Path) -> Path:
    """Bind the launcher, Compose file, and validated worktree to one checkout."""
    if REPO_ROOT.resolve() != worktree.resolve():
        raise SafetyError("launcher checkout must be the validated Route A worktree")
    compose_file = worktree / COMPOSE_FILENAME
    if not compose_file.is_file():
        raise SafetyError("validated Route A worktree does not contain its Compose topology")
    return compose_file


def validate_worktree(worktree: Path, target_sha: str) -> None:
    if not SHA_RE.fullmatch(target_sha):
        raise SafetyError("target SHA must be a full lowercase 40-character commit")
    if not worktree.is_dir():
        raise SafetyError("worktree path does not exist")
    checkout_root = Path(_git(worktree, "rev-parse", "--show-toplevel")).resolve()
    if checkout_root != worktree.resolve():
        raise SafetyError("launcher must run from the supplied worktree root")
    compose_file_for(checkout_root)
    common_dir = Path(_git(worktree, "rev-parse", "--git-common-dir"))
    if not common_dir.is_absolute():
        common_dir = (worktree / common_dir).resolve()
    if checkout_root == common_dir.parent.resolve():
        raise SafetyError("root checkout is forbidden; use a linked clean worktree")
    if _git(worktree, "rev-parse", "HEAD") != target_sha:
        raise SafetyError("worktree HEAD does not match the required Route A revision")
    if _git(worktree, "status", "--porcelain=v1"):
        raise SafetyError("worktree must be clean before Route A build provenance is recorded")
    venv = worktree / ".venv"
    if venv.is_symlink() or (venv.exists() and not venv.is_dir()):
        raise SafetyError("worktree .venv must be absent or a real directory, never a symlink")


def validate_ambient_environment(env: Mapping[str, str]) -> None:
    """Reject all host inputs that could alter the isolated invocation."""
    for key, value in env.items():
        if key.startswith(DENIED_ENV_PREFIXES) and value:
            raise SafetyError(f"ambient credential/provider variable is forbidden: {key}")
        if key.startswith(CONTROLLED_ENV_PREFIXES) and value:
            raise SafetyError(f"ambient Route A, Compose, or Docker variable is forbidden: {key}")
    for key in DENIED_ENV_NAMES:
        if env.get(key, ""):
            raise SafetyError(f"ambient database or dashboard credential is forbidden: {key}")
    for key, value in env.items():
        if ".env" in value.lower() or ".bws" in value.lower():
            raise SafetyError(f"environment references a forbidden credential file: {key}")


def load_compose(path: Path | None = None) -> dict[str, Any]:
    payload = yaml.safe_load((path or COMPOSE_FILE).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SafetyError("Route A compose file must be a mapping")
    return payload


def _network_names(value: Any) -> frozenset[str]:
    if isinstance(value, Mapping):
        return frozenset(str(name) for name in value)
    if isinstance(value, list):
        return frozenset(str(name) for name in value)
    raise SafetyError("Route A service networks must be an explicit list or mapping")


def _resolve_bind_source(source: object, *, worktree: Path, artifact_dir: Path) -> Path:
    source_text = str(source)
    artifact_reference = "${ROUTE_A_ARTIFACT_DIR:?set a run-specific /tmp artifact directory}"
    if "$" in source_text:
        if source_text != artifact_reference:
            raise SafetyError("Route A bind mount may not interpolate an untrusted source")
        return artifact_dir.resolve()
    candidate = Path(source_text)
    return (worktree / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()


def _expected_bind_sources(worktree: Path, artifact_dir: Path) -> dict[str, frozenset[Path]]:
    return {
        "bootstrap": frozenset({(worktree / "scripts/init-db.sql").resolve()}),
        "migrations": frozenset(),
        "fixture-loader": frozenset({artifact_dir.resolve()}),
        "dashboard-api": frozenset(
            {
                (
                    worktree / "deploy/runtime-probe-control/signing-key-unprovisioned.json"
                ).resolve(),
                (worktree / "deploy/runtime-probe-control/verifiers-unprovisioned.json").resolve(),
            }
        ),
        "frontend": frozenset(),
        "browser": frozenset({artifact_dir.resolve()}),
        "postgres": frozenset(),
    }


def _validate_service_environment(name: str, value: object) -> None:
    if value is None:
        return
    if not isinstance(value, Mapping):
        raise SafetyError(f"service {name} environment must be an explicit mapping")
    for key, raw_value in value.items():
        variable = str(key)
        normalized = "" if raw_value is None else str(raw_value)
        if (
            variable.startswith(DENIED_ENV_PREFIXES)
            or variable in DENIED_ENV_NAMES
            or variable.startswith("BWS_")
        ) and normalized:
            raise SafetyError(f"service {name} contains a nonempty credential variable: {variable}")


def _validate_build(
    name: str, service: Mapping[str, Any], *, worktree: Path, rendered: bool
) -> None:
    build = service.get("build")
    expected_dockerfile = BUILD_SERVICES.get(name)
    if expected_dockerfile is None:
        if build is not None:
            raise SafetyError(f"service {name} may not build an image")
        return
    if not isinstance(build, Mapping):
        raise SafetyError(f"service {name} build must be an explicit mapping")
    context = build.get("context")
    if not isinstance(context, str) or "$" in context:
        raise SafetyError(f"service {name} build context must be a literal clean-worktree path")
    context_path = (
        (worktree / context).resolve()
        if not Path(context).is_absolute()
        else Path(context).resolve()
    )
    if context_path != (worktree / "frontend").resolve():
        raise SafetyError(f"service {name} build context must be the worktree frontend directory")
    if build.get("network") != "none":
        raise SafetyError(f"service {name} build must run with network=none")
    if not rendered and build.get("pull") is not False:
        raise SafetyError(f"service {name} build must refuse image pulls")
    if not rendered and build.get("dockerfile") != expected_dockerfile:
        raise SafetyError(f"service {name} uses an unexpected Dockerfile")
    for forbidden in (
        "additional_contexts",
        "cache_from",
        "cache_to",
        "entitlements",
        "secrets",
        "ssh",
    ):
        if build.get(forbidden):
            raise SafetyError(f"service {name} build uses forbidden {forbidden}")


def validate_compose(
    compose: Mapping[str, Any],
    *,
    worktree: Path,
    artifact_dir: Path,
    rendered: bool = False,
) -> None:
    """Validate raw YAML and Docker's rendered Compose model before lifecycle work."""
    services = compose.get("services")
    if not isinstance(services, Mapping) or set(services) != ALLOWED_SERVICES:
        raise SafetyError(
            "Route A compose service allowlist must contain exactly seven named services"
        )
    networks = compose.get("networks")
    if not isinstance(networks, Mapping) or set(networks) != ALLOWED_NETWORKS:
        raise SafetyError("Route A compose file must define only the two evidence networks")
    if any(
        not isinstance(item, Mapping) or item.get("internal") is not True
        for item in networks.values()
    ):
        raise SafetyError("all Route A networks must be internal")
    volumes = compose.get("volumes")
    if not isinstance(volumes, Mapping) or set(volumes) != ALLOWED_VOLUMES:
        raise SafetyError("Route A compose file must define only its disposable PostgreSQL volume")
    if any(isinstance(item, Mapping) and item.get("external") for item in volumes.values()):
        raise SafetyError("Route A volumes must never be external")
    if any(compose.get(key) for key in ("configs", "include", "secrets")):
        raise SafetyError("Route A compose topology may not import configs, includes, or secrets")
    raw = json.dumps(compose, sort_keys=True)
    if any(token in raw for token in ("docker-compose.yml", "compose.sh", "docker.sock", ".bws")):
        raise SafetyError("Route A compose topology references a forbidden host or stock runtime")

    expected_binds = _expected_bind_sources(worktree, artifact_dir)
    expected_volumes = {"postgres": frozenset({"postgres-data"})}
    forbidden_service_keys = (
        "configs",
        "devices",
        "dns",
        "dns_search",
        "env_file",
        "external_links",
        "network_mode",
        "pid",
        "ports",
        "privileged",
        "secrets",
        "userns_mode",
    )
    for name, service in services.items():
        if not isinstance(service, Mapping):
            raise SafetyError(f"service {name} must be a mapping")
        if any(service.get(key) for key in forbidden_service_keys):
            raise SafetyError(f"service {name} has a forbidden lifecycle or host setting")
        if not rendered and service.get("pull_policy") != "never":
            raise SafetyError(f"service {name} must refuse image pulls")
        if _network_names(service.get("networks")) != SERVICE_NETWORKS[name]:
            raise SafetyError(f"service {name} has an unexpected network attachment")
        _validate_service_environment(name, service.get("environment"))
        _validate_build(name, service, worktree=worktree, rendered=rendered)

        mounts = service.get("volumes", [])
        if not isinstance(mounts, list):
            raise SafetyError(f"service {name} mounts must use the long-form list syntax")
        bound_sources: set[Path] = set()
        volume_sources: set[str] = set()
        for mount in mounts:
            if not isinstance(mount, Mapping):
                raise SafetyError(f"service {name} mounts must use long-form mappings")
            mount_type = mount.get("type")
            source = mount.get("source")
            if not source:
                raise SafetyError(f"service {name} mount has no source")
            if mount_type == "bind":
                resolved = _resolve_bind_source(
                    source, worktree=worktree, artifact_dir=artifact_dir
                )
                if any(token in str(resolved) for token in ("docker.sock", ".env", ".bws")):
                    raise SafetyError(f"service {name} has a forbidden bind source")
                bound_sources.add(resolved)
            elif mount_type == "volume":
                if str(source) not in ALLOWED_VOLUMES:
                    raise SafetyError(f"service {name} uses an unknown disposable volume")
                volume_sources.add(str(source))
            else:
                raise SafetyError(f"service {name} uses an unsupported mount type")
        if bound_sources != expected_binds[name]:
            raise SafetyError(f"service {name} bind mounts exceed the Route A allowlist")
        if volume_sources != expected_volumes.get(name, frozenset()):
            raise SafetyError(f"service {name} volume mounts exceed the Route A allowlist")


def require_pinned_image(value: str, *, label: str) -> None:
    if not PINNED_IMAGE_RE.fullmatch(value):
        raise SafetyError(f"{label} must be a digest-pinned image reference")


def route_a_environment(
    *,
    target_sha: str,
    artifact_dir: Path,
    base_image: str,
    postgres_image: str,
    node_image: str,
    playwright_image: str,
    project: str,
) -> dict[str, str]:
    if not SHA_RE.fullmatch(target_sha):
        raise SafetyError("Route A image provenance requires a full lowercase target SHA")
    for label, image in (
        ("Route A base", base_image),
        ("Route A PostgreSQL", postgres_image),
        ("Route A Node", node_image),
        ("Route A Playwright", playwright_image),
    ):
        require_pinned_image(image, label=label)
    if not artifact_dir.is_absolute() or not str(artifact_dir).startswith("/tmp/route-a-"):
        raise SafetyError("Route A artifacts must live in a run-specific /tmp/route-a-* directory")
    if not re.fullmatch(r"routea-[0-9a-f]{8}-[0-9a-f]{12}", project):
        raise SafetyError("Route A project name does not have the required generated form")
    return {
        "COMPOSE_PROJECT_NAME": project,
        "ROUTE_A_PROJECT_NAME": project,
        "ROUTE_A_TARGET_SHA": target_sha,
        "ROUTE_A_ARTIFACT_DIR": str(artifact_dir),
        "ROUTE_A_BASE_IMAGE": base_image,
        "ROUTE_A_POSTGRES_IMAGE": postgres_image,
        "ROUTE_A_NODE_IMAGE": node_image,
        "ROUTE_A_PLAYWRIGHT_IMAGE": playwright_image,
        "ROUTE_A_APP_IMAGE": f"butlers-route-a-app:{project}",
        "ROUTE_A_FRONTEND_IMAGE": f"butlers-route-a-frontend:{project}",
        "ROUTE_A_BROWSER_IMAGE": f"butlers-route-a-browser:{project}",
    }


def generate_project(target_sha: str) -> str:
    return f"routea-{target_sha[:8]}-{secrets.token_hex(6)}"


def compose_command(
    *,
    worktree: Path,
    compose_file: Path,
    project: str,
    env_file: Path,
    arguments: Sequence[str],
) -> list[str]:
    return [
        "docker",
        "--context",
        "default",
        "compose",
        "--project-name",
        project,
        "--project-directory",
        str(worktree),
        "--env-file",
        str(env_file),
        "--file",
        str(compose_file),
        *arguments,
    ]


def docker_environment(values: Mapping[str, str], docker_config: Path) -> dict[str, str]:
    """Return the only environment inherited by Docker or Docker Compose."""
    if not docker_config.is_absolute():
        raise SafetyError("Route A Docker configuration must be temporary and absolute")
    return {
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "HOME": str(docker_config),
        "DOCKER_CONFIG": str(docker_config),
        **{str(key): str(value) for key, value in values.items()},
    }


def write_env_file(directory: Path, values: Mapping[str, str]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    env_file = directory / "route-a.env"
    env_file.write_text(
        "".join(f"{key}={value}\n" for key, value in sorted(values.items())), encoding="utf-8"
    )
    env_file.chmod(0o600)
    return env_file


def _docker_command(*arguments: str) -> list[str]:
    return ["docker", "--context", "default", *arguments]


def _docker_run(
    arguments: Sequence[str],
    *,
    env: Mapping[str, str],
    cwd: Path = REPO_ROOT,
) -> str:
    return _run(_docker_command(*arguments), cwd=cwd, env=env)


def validate_local_docker_context(docker_env: Mapping[str, str]) -> None:
    host = _docker_run(
        ["context", "inspect", "default", "--format", "{{.Endpoints.docker.Host}}"],
        env=docker_env,
    ).strip()
    if not host.startswith("unix://"):
        raise SafetyError("Route A requires the local default Unix Docker context")


def _assert_local_pinned_image(image: str, *, label: str, docker_env: Mapping[str, str]) -> None:
    require_pinned_image(image, label=label)
    try:
        raw = _docker_run(
            ["image", "inspect", "--format", "{{json .RepoDigests}}", image], env=docker_env
        )
    except subprocess.CalledProcessError as error:
        raise SafetyError(
            f"{label} must be preloaded locally; Route A never pulls images"
        ) from error
    try:
        repo_digests = json.loads(raw)
    except json.JSONDecodeError as error:
        raise SafetyError(f"{label} did not expose a verifiable local repo digest") from error
    if not isinstance(repo_digests, list) or image not in repo_digests:
        raise SafetyError(f"{label} is not available locally under its exact digest")


def validate_local_input_images(values: Mapping[str, str], docker_env: Mapping[str, str]) -> None:
    inputs = {
        "Route A Go builder image": GO_BUILDER_IMAGE,
        "Route A base image": values["ROUTE_A_BASE_IMAGE"],
        "Route A PostgreSQL image": values["ROUTE_A_POSTGRES_IMAGE"],
        "Route A Node image": values["ROUTE_A_NODE_IMAGE"],
        "Route A Playwright image": values["ROUTE_A_PLAYWRIGHT_IMAGE"],
    }
    for label, image in inputs.items():
        _assert_local_pinned_image(image, label=label, docker_env=docker_env)


def _generated_images(values: Mapping[str, str]) -> tuple[str, str, str]:
    return (
        values["ROUTE_A_APP_IMAGE"],
        values["ROUTE_A_FRONTEND_IMAGE"],
        values["ROUTE_A_BROWSER_IMAGE"],
    )


def _image_exists(image: str, *, docker_env: Mapping[str, str]) -> bool:
    try:
        _docker_run(["image", "inspect", image], env=docker_env)
    except subprocess.CalledProcessError:
        return False
    return True


def _assert_generated_images_absent(
    values: Mapping[str, str], docker_env: Mapping[str, str]
) -> None:
    if any(_image_exists(image, docker_env=docker_env) for image in _generated_images(values)):
        raise SafetyError("generated Route A image tag already exists; refuse to overwrite it")


def _assert_project_gone(project: str, docker_env: Mapping[str, str]) -> None:
    for command in (
        ["ps", "-aq", "--filter", f"label=com.docker.compose.project={project}"],
        ["volume", "ls", "-q", "--filter", f"label=com.docker.compose.project={project}"],
        ["network", "ls", "-q", "--filter", f"label=com.docker.compose.project={project}"],
    ):
        if _docker_run(command, env=docker_env).strip():
            raise SafetyError("Route A teardown left a project-scoped Docker resource behind")


def _image_labels(image: str, *, docker_env: Mapping[str, str]) -> Mapping[str, str]:
    raw = _docker_run(
        ["image", "inspect", "--format", "{{json .Config.Labels}}", image], env=docker_env
    )
    try:
        labels = json.loads(raw)
    except json.JSONDecodeError as error:
        raise SafetyError("Route A image labels could not be verified") from error
    if not isinstance(labels, Mapping):
        raise SafetyError("Route A image is missing its ownership labels")
    return {str(key): str(value) for key, value in labels.items()}


def _remove_generated_images(values: Mapping[str, str], docker_env: Mapping[str, str]) -> None:
    project = values["COMPOSE_PROJECT_NAME"]
    target_sha = values["ROUTE_A_TARGET_SHA"]
    for image in _generated_images(values):
        if not _image_exists(image, docker_env=docker_env):
            continue
        labels = _image_labels(image, docker_env=docker_env)
        if labels.get(PROJECT_LABEL) != project or labels.get(TARGET_SHA_LABEL) != target_sha:
            raise SafetyError("refusing to remove an image not owned by this Route A run")
        _docker_run(["image", "rm", image], env=docker_env)


def _write_provenance_receipt(
    *,
    artifact_dir: Path,
    target_sha: str,
    project: str,
    values: Mapping[str, str],
    docker_env: Mapping[str, str],
    dashboard_container: str,
) -> None:
    app_image = values["ROUTE_A_APP_IMAGE"]
    app_labels = _image_labels(app_image, docker_env=docker_env)
    if app_labels.get(PROJECT_LABEL) != project or app_labels.get(TARGET_SHA_LABEL) != target_sha:
        raise SafetyError("Route A app image labels do not match the validated run")
    image_id = _docker_run(
        ["image", "inspect", "--format", "{{.Id}}", app_image], env=docker_env
    ).strip()
    runtime_env = _docker_run(
        [
            "inspect",
            "--format",
            "{{range .Config.Env}}{{println .}}{{end}}",
            dashboard_container,
        ],
        env=docker_env,
    )
    runtime_sha = next(
        (
            line.removeprefix("GIT_SHA=")
            for line in runtime_env.splitlines()
            if line.startswith("GIT_SHA=")
        ),
        "",
    )
    if runtime_sha != target_sha:
        raise SafetyError("dashboard runtime GIT_SHA does not match the Route A target")
    (artifact_dir / "provenance.json").write_text(
        json.dumps(
            {
                "target_sha": target_sha,
                "project": project,
                "app_image": app_image,
                "image_id": image_id,
                "runtime_git_sha": runtime_sha,
                "input_images": {
                    "base": values["ROUTE_A_BASE_IMAGE"],
                    "postgres": values["ROUTE_A_POSTGRES_IMAGE"],
                    "node": values["ROUTE_A_NODE_IMAGE"],
                    "playwright": values["ROUTE_A_PLAYWRIGHT_IMAGE"],
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def execute(*, worktree: Path, target_sha: str, values: Mapping[str, str]) -> None:
    """Perform a future explicit Route A run after every static refusal passes."""
    validate_worktree(worktree, target_sha)
    validate_ambient_environment(os.environ)
    compose_file = compose_file_for(worktree)
    artifact_dir = Path(values["ROUTE_A_ARTIFACT_DIR"])
    validate_compose(load_compose(compose_file), worktree=worktree, artifact_dir=artifact_dir)
    artifact_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
    artifact_dir.chmod(0o700)

    with tempfile.TemporaryDirectory(prefix="route-a-env-", dir="/tmp") as temporary:
        temporary_dir = Path(temporary)
        docker_config = temporary_dir / "docker-config"
        docker_config.mkdir(mode=0o700)
        docker_env = docker_environment(values, docker_config)
        env_file = write_env_file(temporary_dir, values)
        validate_local_docker_context(docker_env)
        validate_local_input_images(values, docker_env)
        _assert_project_gone(values["COMPOSE_PROJECT_NAME"], docker_env)
        _assert_generated_images_absent(values, docker_env)
        config = _run(
            compose_command(
                worktree=worktree,
                compose_file=compose_file,
                project=values["COMPOSE_PROJECT_NAME"],
                env_file=env_file,
                arguments=["config", "--format", "json"],
            ),
            cwd=worktree,
            env=docker_env,
        )
        try:
            rendered_config = json.loads(config)
        except json.JSONDecodeError as error:
            raise SafetyError("Route A Compose config did not render as JSON") from error
        if not isinstance(rendered_config, Mapping):
            raise SafetyError("Route A Compose config did not render as a mapping")
        validate_compose(
            rendered_config,
            worktree=worktree,
            artifact_dir=artifact_dir,
            rendered=True,
        )
        (artifact_dir / "compose-config.json").write_text(config, encoding="utf-8")

        try:
            _docker_run(
                [
                    "build",
                    "--network",
                    "none",
                    "--pull=false",
                    "--label",
                    f"{PROJECT_LABEL}={values['COMPOSE_PROJECT_NAME']}",
                    "--label",
                    f"{TARGET_SHA_LABEL}={target_sha}",
                    "--build-arg",
                    f"GIT_SHA={target_sha}",
                    "--build-arg",
                    f"BUTLERS_BASE_IMAGE={values['ROUTE_A_BASE_IMAGE']}",
                    "--build-arg",
                    "BUTLERS_OFFLINE=1",
                    "--tag",
                    values["ROUTE_A_APP_IMAGE"],
                    ".",
                ],
                cwd=worktree,
                env=docker_env,
            )
            _run(
                compose_command(
                    worktree=worktree,
                    compose_file=compose_file,
                    project=values["COMPOSE_PROJECT_NAME"],
                    env_file=env_file,
                    arguments=[
                        "up",
                        "--build",
                        "--wait",
                        "--wait-timeout",
                        "90",
                        "dashboard-api",
                        "frontend",
                    ],
                ),
                cwd=worktree,
                env=docker_env,
            )
            dashboard_container = _run(
                compose_command(
                    worktree=worktree,
                    compose_file=compose_file,
                    project=values["COMPOSE_PROJECT_NAME"],
                    env_file=env_file,
                    arguments=["ps", "-q", "dashboard-api"],
                ),
                cwd=worktree,
                env=docker_env,
            ).strip()
            if not dashboard_container:
                raise SafetyError("Route A dashboard container was not available for provenance")
            _run(
                compose_command(
                    worktree=worktree,
                    compose_file=compose_file,
                    project=values["COMPOSE_PROJECT_NAME"],
                    env_file=env_file,
                    arguments=["run", "--build", "--no-deps", "--rm", "browser"],
                ),
                cwd=worktree,
                env=docker_env,
            )
            _write_provenance_receipt(
                artifact_dir=artifact_dir,
                target_sha=target_sha,
                project=values["COMPOSE_PROJECT_NAME"],
                values=values,
                docker_env=docker_env,
                dashboard_container=dashboard_container,
            )
        finally:
            _run(
                compose_command(
                    worktree=worktree,
                    compose_file=compose_file,
                    project=values["COMPOSE_PROJECT_NAME"],
                    env_file=env_file,
                    arguments=["down", "--volumes", "--remove-orphans"],
                ),
                cwd=worktree,
                env=docker_env,
            )
            _remove_generated_images(values, docker_env)
            _assert_project_gone(values["COMPOSE_PROJECT_NAME"], docker_env)
            _assert_generated_images_absent(values, docker_env)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-sha", required=True)
    parser.add_argument("--worktree", type=Path, default=REPO_ROOT)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--base-image")
    parser.add_argument("--postgres-image")
    parser.add_argument("--node-image")
    parser.add_argument("--playwright-image")
    args = parser.parse_args(argv)
    worktree = args.worktree.resolve()
    validate_worktree(worktree, args.target_sha)
    validate_ambient_environment(os.environ)
    if not args.execute:
        print(
            json.dumps({"status": "preflight-only", "target_sha": args.target_sha}, sort_keys=True)
        )
        return 0
    if not all((args.base_image, args.postgres_image, args.node_image, args.playwright_image)):
        parser.error("--execute requires digest-pinned base, postgres, node, and Playwright images")
    artifact_dir = Path(tempfile.mkdtemp(prefix="route-a-", dir="/tmp"))
    values = route_a_environment(
        target_sha=args.target_sha,
        artifact_dir=artifact_dir,
        base_image=args.base_image,
        postgres_image=args.postgres_image,
        node_image=args.node_image,
        playwright_image=args.playwright_image,
        project=generate_project(args.target_sha),
    )
    execute(worktree=worktree, target_sha=args.target_sha, values=values)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
