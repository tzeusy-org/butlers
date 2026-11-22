"""Helm render: the migrator login split is dark until database.migratorLogin.enabled.

bu-dss7he.6 S3. Off (the default), nothing renders differently. On, only the migrations
initContainer connects as the migrator, and only the shared ``<release>-bws`` Secret gains
the two migrator keys; every runtime container keeps the shared runtime login.
"""

from __future__ import annotations

import copy
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CHART = _REPO_ROOT / "deploy" / "helm" / "butlers"
_SITE = [
    "--set", "trackerEgress.deniedCidrs={192.0.2.1/32,192.0.2.2/32}",
    "--set", "egressPolicy.allowedCidrs={198.51.100.7/32}",
]  # fmt: skip
_ON = ("--set", "database.migratorLogin.enabled=true")
_MODE = "BUTLERS_STARTUP_MIGRATIONS"
_MIGRATOR_KEYS = {
    "MIGRATOR_POSTGRES_USER": "BUTLERS_RUNTIME_MIGRATOR_DB_USER",
    "MIGRATOR_POSTGRES_PASSWORD": "BUTLERS_RUNTIME_MIGRATOR_DB_PASSWORD",
}

needs_helm = pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")


def _render(env: str, *extra: str) -> list[dict]:
    result = subprocess.run(
        ["helm", "template", "butlers", str(_CHART), "-f", str(_CHART / "values.yaml"),
         "-f", str(_CHART / f"values.{env}.yaml"), "--set", "image.tag=t",
         "--set", "frontendImage.tag=t", *_SITE, *extra],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    return [d for d in yaml.safe_load_all(result.stdout) if d]


def _pod_specs(docs: list[dict]) -> list[tuple[str, dict]]:
    specs = []
    for doc in docs:
        spec = doc.get("spec", {})
        if doc["kind"] == "CronJob":
            spec = spec["jobTemplate"]["spec"]
        pod = spec.get("template", {}).get("spec")
        if pod:
            specs.append((doc["metadata"]["name"], pod))
    return specs


def _containers(docs: list[dict]) -> list[tuple[str, dict]]:
    out = []
    for name, pod in _pod_specs(docs):
        for c in [*pod.get("initContainers", []), *pod.get("containers", [])]:
            out.append((f"{name}/{c['name']}", c))
    return out


def _login_keys(container: dict) -> dict[str, str]:
    return {
        e["name"]: e["valueFrom"]["secretKeyRef"]["key"]
        for e in container.get("env", [])
        if e["name"] in {"POSTGRES_USER", "POSTGRES_PASSWORD"} and "valueFrom" in e
    }


def _bws_data(docs: list[dict]) -> dict[str, str]:
    (store,) = [
        d
        for d in docs
        if d["kind"] == "ExternalSecret" and d["spec"]["target"]["name"] == "butlers-bws"
    ]
    return {e["secretKey"]: e["remoteRef"]["key"] for e in store["spec"]["data"]}


@needs_helm
@pytest.mark.parametrize("env", ["dev", "prod"])
def test_default_is_dark_every_container_uses_the_shared_login(env: str) -> None:
    docs = _render(env)
    assert not set(_bws_data(docs)) & set(_MIGRATOR_KEYS)
    logins = {name: _login_keys(c) for name, c in _containers(docs) if _login_keys(c)}
    assert "dashboard-api/migrations" in logins
    for name, keys in logins.items():
        assert keys == {
            "POSTGRES_USER": "POSTGRES_USER",
            "POSTGRES_PASSWORD": "POSTGRES_PASSWORD",
        }, name


@needs_helm
def test_enabled_moves_only_the_migrations_init_container_to_the_migrator() -> None:
    docs = _render("dev", *_ON)
    data = _bws_data(docs)
    for key, remote in _MIGRATOR_KEYS.items():
        assert data[key] == remote
    for name, container in _containers(docs):
        keys = _login_keys(container)
        if not keys:
            continue
        if name == "dashboard-api/migrations":
            assert keys == {
                "POSTGRES_USER": "MIGRATOR_POSTGRES_USER",
                "POSTGRES_PASSWORD": "MIGRATOR_POSTGRES_PASSWORD",
            }
        else:
            assert keys == {
                "POSTGRES_USER": "POSTGRES_USER",
                "POSTGRES_PASSWORD": "POSTGRES_PASSWORD",
            }, name


def _startup_mode(container: dict) -> str | None:
    values = [e.get("value") for e in container.get("env", []) if e["name"] == _MODE]
    assert len(values) <= 1
    return values[0] if values else None


@needs_helm
@pytest.mark.parametrize("env", ["dev", "prod"])
def test_runtime_containers_head_check_only_when_enabled(env: str) -> None:
    for name, container in _containers(_render(env)):
        assert _startup_mode(container) is None, name
    checked = set()
    for name, container in _containers(_render(env, *_ON)):
        if not _login_keys(container):
            continue
        if name == "dashboard-api/migrations":
            assert _startup_mode(container) is None
        else:
            assert _startup_mode(container) == "check", name
            checked.add(name)
    assert any(name.startswith("butlers-up/") for name in checked), checked


def _strip_split(docs: list[dict]) -> list[dict]:
    """Remove exactly what the flag may change: migrator keys and login, startup mode."""
    docs = copy.deepcopy(docs)
    for doc in docs:
        if doc["kind"] == "ExternalSecret":
            doc["spec"]["data"] = [
                e for e in doc["spec"]["data"] if e["secretKey"] not in _MIGRATOR_KEYS
            ]
    for _name, pod in _pod_specs(docs):
        for c in [*pod.get("initContainers", []), *pod.get("containers", [])]:
            c["env"] = [e for e in c.get("env", []) if e["name"] != _MODE]
        for c in pod.get("initContainers", []):
            if c["name"] == "migrations":
                c["env"] = [
                    e for e in c["env"] if e["name"] not in {"POSTGRES_USER", "POSTGRES_PASSWORD"}
                ]
    return docs


@needs_helm
@pytest.mark.parametrize("env", ["dev", "prod"])
def test_enabled_changes_nothing_else(env: str) -> None:
    off = _render(env)
    on = _render(env, *_ON)
    assert off != on
    assert _strip_split(off) == _strip_split(on)


@needs_helm
def test_remote_key_names_come_from_values() -> None:
    docs = _render(
        "dev", *_ON, "--set", "externalSecrets.migratorData.MIGRATOR_POSTGRES_USER=RENAMED_USER"
    )
    assert _bws_data(docs)["MIGRATOR_POSTGRES_USER"] == "RENAMED_USER"


def test_compose_moves_only_the_migrations_service_and_falls_back_to_the_shared_login() -> None:
    compose = yaml.safe_load((_REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    env = compose["services"]["migrations"]["environment"]
    assert env["POSTGRES_USER"] == "${BUTLERS_MIGRATOR_DB_USER:-${POSTGRES_USER:-butlers}}"
    assert env["POSTGRES_PASSWORD"].startswith(
        "${BUTLERS_MIGRATOR_DB_PASSWORD:-${POSTGRES_PASSWORD:?"
    )
    for name, service in compose["services"].items():
        if name != "migrations":
            assert "BUTLERS_MIGRATOR" not in yaml.safe_dump(service), name


def test_compose_passes_the_startup_mode_through_defaulting_to_run() -> None:
    compose = yaml.safe_load((_REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    assert compose["x-postgres-env"][_MODE] == "${BUTLERS_STARTUP_MIGRATIONS:-run}"
