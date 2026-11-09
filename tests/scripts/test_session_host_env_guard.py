"""RFC 0038 D3 / bu-kiuh8v AC4 source half: the session host's environment composition.

The session host never receives domain, database or connector credentials. This scans
the checked-in Compose service and the Helm template for what the service is composed
of (environment, secret references, files, volumes, ports, networks) and fails if a
forbidden credential class appears. It reads source only: no container starts, no
secret is read, and every injected name below is a synthetic marker, never a value.

The scan fails if the service definition is missing, so it cannot pass vacuously.
Names outside the allowlist also fail, so a new variable is a deliberate edit here.
"""

from __future__ import annotations

import copy
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CHART = _REPO_ROOT / "deploy" / "helm" / "butlers"
_TEMPLATE = _CHART / "templates" / "session-host.yaml"
_SERVICE = "session-host"
# Reviewed environment of the dark skeleton. Model-credential staging (bu-kiuh8v.3, .5)
# widens this deliberately; nothing else may.
_ALLOWED_ENV = {"HOME"}

_CLASSES: dict[str, re.Pattern[str]] = {
    "database": re.compile(r"(^|_)(POSTGRES|PG|PGPASSWORD|DB|DATABASE|DSN)(_|$)"),
    "connector": re.compile(
        r"(^|_)(GOOGLE|GMAIL|GDRIVE|TELEGRAM|SLACK|WHATSAPP|WA|SPOTIFY|OWNTRACKS|HA|DISCORD"
        r"|MINIO|S3|ACTIVITYWATCH|CONNECTOR|OAUTH)(_|$)"
    ),
    "domain": re.compile(
        r"(^|_)(BUTLER|BUTLERS|DASHBOARD|SWITCHBOARD|BWS|RUNTIME_PROBE|CONTROL)(_|$)"
    ),
    # Credential store, encryption, backup, beads/Dolt, GitHub and kube material. Once
    # model-credential staging widens _ALLOWED_ENV, these regexes are the backstop.
    "credential": re.compile(
        r"(^|_)(CREDENTIAL|CREDENTIALS|CREDENTIAL_STORE|ENCRYPTION|FERNET|SECRET|SECRETS"
        r"|PASSWORD|PASSPHRASE|SIGNING|SSH|RESTIC|RESTORE|OFFSITE|BACKUP|DOLT|BEADS"
        r"|GITHUB|GH|KUBE|KUBECONFIG|KUBERNETES|HELM)(_|$)"
    ),
}
# Template constructs that pull in the shared credential plumbing.
_FORBIDDEN_TEMPLATE_TOKENS = (
    "postgresEnv",
    "bwsSecret",
    "secretKeyRef",
    "configMapKeyRef",
    "envFrom",
    "secretName",
    "externalsecret",
)
# The one reviewed secret reference: the host's own PKI (bu-kiuh8v.15). Every other
# occurrence of a forbidden token still fails.
_PKI_SECRET_LINE = "secretName: {{ .Values.sessionHost.pkiSecret }}"

# The reviewed storage and listener (bu-kiuh8v.15). Each is compared exactly, so an
# added volume, mount or port is a deliberate edit here.
_PORT = 41198
_COMPOSE_VOLUMES = [
    "${SESSION_HOST_PKI_DIR:-./deploy/session-host/pki-unprovisioned}:/run/session-host/pki:ro",
    "session_host_credentials:/var/lib/session-host/credentials",
]
_COMPOSE_TMPFS = ["/var/lib/session-host/staging:mode=0700"]
_POD_PORTS = [{"name": "control", "containerPort": _PORT, "protocol": "TCP"}]
_POD_MOUNTS = [
    {"name": "pki", "mountPath": "/run/session-host/pki", "readOnly": True},
    {"name": "staging", "mountPath": "/var/lib/session-host/staging"},
    {"name": "credentials", "mountPath": "/var/lib/session-host/credentials"},
]


def _pod_volumes(release: str, pki_secret: str) -> list[dict]:
    return [
        {"name": "pki", "secret": {"secretName": pki_secret, "defaultMode": 0o400}},
        {"name": "staging", "emptyDir": {"medium": "Memory", "sizeLimit": "256Mi"}},
        {
            "name": "credentials",
            "persistentVolumeClaim": {"claimName": f"{release}-session-host-credentials"},
        },
    ]


def template_token_violations(text: str) -> list[str]:
    """Forbidden plumbing in the template source, apart from the one PKI reference."""
    found = [] if text.count(_PKI_SECRET_LINE) <= 1 else ["a second PKI secret reference"]
    scanned = text.replace(_PKI_SECRET_LINE, "")
    return found + [t for t in _FORBIDDEN_TEMPLATE_TOKENS if t.lower() in scanned.lower()]


def _classify(names: list[str]) -> list[str]:
    found = []
    for name in sorted(names):
        hits = [label for label, pattern in _CLASSES.items() if pattern.search(name.upper())]
        if hits:
            found.append(f"{hits[0]} credential class: {name}")
        elif name not in _ALLOWED_ENV:
            found.append(f"unreviewed environment name: {name}")
    return found


def _compose_env_names(environment: object) -> list[str]:
    if environment is None:
        return []
    if isinstance(environment, dict):
        return [str(key) for key in environment]
    return [str(item).split("=", 1)[0] for item in environment]


def compose_violations(service: dict | None) -> list[str]:
    """Why a Compose service definition breaks the session-host composition rules."""
    if service is None:
        return [f"{_SERVICE} service definition is missing"]
    found = _classify(_compose_env_names(service.get("environment")))
    # ``extends`` inherits another service's environment, secrets and volumes, which this
    # scan of the service's own keys would not see. The repo uses none, so refuse it.
    for key in ("extends", "env_file", "secrets", "configs", "volumes_from", "ports", "expose"):
        if service.get(key):
            found.append(f"{key} is not allowed on the session host")
    # The control plane reaches the host over the control-session network, so nothing
    # is published; storage is exactly the PKI mount, the credential volume and staging.
    for key, reviewed in (("volumes", _COMPOSE_VOLUMES), ("tmpfs", _COMPOSE_TMPFS)):
        if service.get(key) != reviewed:
            found.append(f"{key} is not allowed on the session host: {service.get(key)}")
    if service.get("networks") != ["control-session"]:
        found.append(f"networks must be exactly [control-session]: {service.get('networks')}")
    if service.get("profiles") != [_SERVICE]:
        found.append(
            f"the service must be off by default behind its profile: {service.get('profiles')}"
        )
    return found


def pod_violations(
    deployment: dict | None, *, release: str = "butlers", pki_secret: str = "session-host-pki"
) -> list[str]:
    """Why a rendered Deployment breaks the session-host composition rules."""
    if deployment is None:
        return [f"{_SERVICE} Deployment is missing from the enabled render"]
    spec = deployment["spec"]["template"]["spec"]
    found = []
    containers = spec.get("containers", [])
    if spec.get("initContainers"):
        found.append("initContainers are not allowed on the session host")
    if len(containers) != 1:
        found.append(f"the session host runs exactly one container: {len(containers)}")
    for container in [*containers, *spec.get("initContainers", [])]:
        env = container.get("env", [])
        found += _classify([item["name"] for item in env])
        found += [
            f"{item['name']} takes its value from a reference ({sorted(item['valueFrom'])})"
            for item in env
            if "valueFrom" in item
        ]
        if container.get("envFrom"):
            found.append("container envFrom is not allowed on the session host")
        for key, reviewed in (("ports", _POD_PORTS), ("volumeMounts", _POD_MOUNTS)):
            if container.get(key) != reviewed:
                found.append(f"container {key} is not allowed on the session host")
    if spec.get("volumes") != _pod_volumes(release, pki_secret):
        found.append(f"volumes are not allowed on the session host: {spec.get('volumes')}")
    # fsGroup would make the projected key group-readable (0440), which the host and the
    # control-plane client both refuse (any 0o077 bit); defaultMode is pinned above.
    if (spec.get("securityContext") or {}).get("fsGroup") is not None:
        found.append("fsGroup is not allowed: it widens the PKI key's mode")
    if spec.get("automountServiceAccountToken") is not False:
        found.append("the service account token must not be mounted")
    return found


def exposure_violations(docs: list[dict], *, release: str = "butlers") -> list[str]:
    """Why the enabled render exposes the session host beyond the control plane's port."""
    found = []
    services = [d for d in docs if d["kind"] == "Service"]
    for service in services:
        spec = service["spec"]
        ports = [(p.get("port"), p.get("targetPort")) for p in spec.get("ports", [])]
        if spec.get("type", "ClusterIP") != "ClusterIP" or ports != [(_PORT, "control")]:
            found.append(f"the Service exposes more than the control port: {spec}")
    policies = [d for d in docs if d["kind"] == "NetworkPolicy"]
    reviewed_ingress = [
        {
            "from": [
                {
                    "podSelector": {
                        "matchLabels": {
                            "app.kubernetes.io/instance": release,
                            "app.kubernetes.io/component": "butlers-control",
                        }
                    }
                }
            ],
            "ports": [{"protocol": "TCP", "port": _PORT}],
        }
    ]
    admits_only_control = [
        p
        for p in policies
        if p["spec"].get("policyTypes") == ["Ingress"]
        and p["spec"].get("ingress") == reviewed_ingress
        and p["spec"]["podSelector"]["matchLabels"].get("app.kubernetes.io/component") == _SERVICE
    ]
    if services and len(admits_only_control) != 1:
        found.append("a Service needs the ingress policy admitting butlers-control only")
    for policy in policies:
        if policy not in admits_only_control:
            found.append(f"unreviewed session-host policy: {policy['metadata']['name']}")
    return found


@pytest.fixture(scope="module")
def compose_service() -> dict | None:
    compose = yaml.safe_load((_REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    return compose["services"].get(_SERVICE)


def _render(*settings: str) -> list[dict]:
    command = ["helm", "template", "butlers", str(_CHART)]
    command += ["-f", str(_CHART / "values.yaml"), "-f", str(_CHART / "values.dev.yaml")]
    site = (
        "trackerEgress.deniedCidrs={192.0.2.1/32}",
        "egressPolicy.allowedCidrs={198.51.100.7/32}",
        "database.host=postgres.invalid",
    )
    for setting in ("image.tag=t", "frontendImage.tag=t", *site, *settings):
        command += ["--set", setting]
    result = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
    assert result.returncode == 0, result.stderr
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def _session_host_docs(docs: list[dict]) -> list[dict]:
    return [d for d in docs if "session-host" in d["metadata"]["name"]]


needs_helm = pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")
_ENABLED = ("butlersControl.enabled=true", "sessionHost.enabled=true")

# ── The real definitions ────────────────────────────────────────────────────


def test_compose_session_host_is_present_clean_and_off_by_default(compose_service) -> None:
    assert compose_service is not None, f"{_SERVICE} missing from docker-compose.yml"
    assert compose_violations(compose_service) == []


def test_helm_template_pulls_in_no_credential_plumbing() -> None:
    text = _TEMPLATE.read_text(encoding="utf-8")
    assert text.strip(), "the session-host template is empty; the scan would pass vacuously"
    assert template_token_violations(text) == []


@needs_helm
def test_helm_session_host_is_dark_by_default_and_clean_when_enabled() -> None:
    assert yaml.safe_load((_CHART / "values.yaml").read_text())["sessionHost"]["enabled"] is False
    assert _session_host_docs(_render()) == []
    docs = _session_host_docs(_render(*_ENABLED))
    deployments = [d for d in docs if d["kind"] == "Deployment"]
    assert sorted(d["kind"] for d in docs) == [
        "Deployment",
        "NetworkPolicy",
        "PersistentVolumeClaim",
        "Service",
    ], "only the host, its control Service, credential volume and ingress policy"
    assert pod_violations(deployments[0] if deployments else None) == []
    assert exposure_violations(docs) == []
    container = deployments[0]["spec"]["template"]["spec"]["containers"][0]
    assert container["command"] == [
        "/usr/local/bin/python",
        "-I",
        "/app/scripts/runtime_clis/python_entrypoint.py",
        "butlers",
    ]
    values = yaml.safe_load((_CHART / "values.yaml").read_text())["sessionHost"]
    assert container["args"] == [
        "session-host",
        "--pki-dir=/run/session-host/pki",
        "--staging-root=/var/lib/session-host/staging",
        "--credentials-root=/var/lib/session-host/credentials/codex",
        f"--broker-origin={values['brokerOrigin']}",
        "--bind-host=0.0.0.0",
        f"--port={values['port']}",
    ]


@needs_helm
def test_the_rendered_exposure_mutations_fail() -> None:
    docs = _session_host_docs(_render(*_ENABLED))
    service = next(d for d in docs if d["kind"] == "Service")
    policy = next(d for d in docs if d["kind"] == "NetworkPolicy")
    deployment = next(d for d in docs if d["kind"] == "Deployment")

    wider = copy.deepcopy(service)
    wider["spec"]["type"] = "NodePort"
    assert exposure_violations([wider, policy]) != []
    extra = copy.deepcopy(service)
    extra["spec"]["ports"].append({"port": 22, "targetPort": 22})
    assert exposure_violations([extra, policy]) != []
    assert exposure_violations([service]) != []  # no admitting policy
    for open_from in ([{}], [{"namespaceSelector": {}}], [{"podSelector": {}}]):
        loose = copy.deepcopy(policy)
        loose["spec"]["ingress"][0]["from"] = open_from
        assert exposure_violations([service, loose]) != []

    mutated = copy.deepcopy(deployment)
    pod = mutated["spec"]["template"]["spec"]
    pod["volumes"].append({"name": "codex", "hostPath": {"path": "/root/.codex"}})
    assert any("volumes are not allowed" in v for v in pod_violations(mutated))
    mutated = copy.deepcopy(deployment)
    mutated["spec"]["template"]["spec"]["volumes"][0]["secret"]["secretName"] = "bws-secret"
    assert any("volumes are not allowed" in v for v in pod_violations(mutated))
    mutated = copy.deepcopy(deployment)
    container = mutated["spec"]["template"]["spec"]["containers"][0]
    container["volumeMounts"].append({"name": "pki", "mountPath": "/root/.codex"})
    assert any("volumeMounts" in v for v in pod_violations(mutated))
    mutated = copy.deepcopy(deployment)
    container = mutated["spec"]["template"]["spec"]["containers"][0]
    container["ports"].append({"containerPort": 41100})
    assert any("ports" in v for v in pod_violations(mutated))
    mutated = copy.deepcopy(deployment)
    mutated["spec"]["template"]["spec"]["initContainers"] = [{"name": "x"}]
    assert any("initContainers" in v for v in pod_violations(mutated))
    mutated = copy.deepcopy(deployment)
    mutated["spec"]["template"]["spec"]["securityContext"] = {"fsGroup": 1000}
    assert any("fsGroup" in v for v in pod_violations(mutated))
    for mode in (0o440, 0o644):
        mutated = copy.deepcopy(deployment)
        mutated["spec"]["template"]["spec"]["volumes"][0]["secret"]["defaultMode"] = mode
        assert any("volumes are not allowed" in v for v in pod_violations(mutated))


@needs_helm
def test_the_pki_secret_can_never_be_the_bws_secret() -> None:
    command = ["helm", "template", "butlers", str(_CHART), "-f", str(_CHART / "values.yaml")]
    command += ["-f", str(_CHART / "values.dev.yaml")]
    for setting in (
        *_ENABLED,
        "sessionHost.pkiSecret=butlers-bws",
        "image.tag=t",
        "frontendImage.tag=t",
        "trackerEgress.deniedCidrs={192.0.2.1/32}",
        "egressPolicy.allowedCidrs={198.51.100.7/32}",
        "database.host=postgres.invalid",
    ):
        command += ["--set", setting]
    result = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
    assert result.returncode != 0 and "sessionHost.pkiSecret" in result.stderr


def test_a_second_or_missing_secret_reference_in_the_template_fails() -> None:
    text = _TEMPLATE.read_text(encoding="utf-8")
    assert template_token_violations(text + "\n        secretName: butlers-bws\n") == ["secretName"]
    assert template_token_violations(text + f"\n  {_PKI_SECRET_LINE}\n") != []
    assert template_token_violations(text + "\n  envFrom: []\n") == ["envFrom"]


@needs_helm
def test_helm_session_host_requires_the_control_plane_it_reaches() -> None:
    command = ["helm", "template", "butlers", str(_CHART), "-f", str(_CHART / "values.yaml")]
    command += ["-f", str(_CHART / "values.dev.yaml"), "--set", "sessionHost.enabled=true"]
    command += ["--set", "image.tag=t", "--set", "frontendImage.tag=t"]
    command += ["--set", "trackerEgress.deniedCidrs={192.0.2.1/32}"]
    command += ["--set", "egressPolicy.allowedCidrs={198.51.100.7/32}"]
    result = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
    assert result.returncode != 0 and "butlersControl.enabled" in result.stderr


# ── Mutation proofs: each forbidden class, and a missing service, must fail ─────


def test_a_missing_service_definition_fails_the_scan() -> None:
    assert compose_violations(None) == [f"{_SERVICE} service definition is missing"]
    assert pod_violations(None) == [f"{_SERVICE} Deployment is missing from the enabled render"]


@pytest.mark.parametrize(
    ("label", "name"),
    [
        ("database", "POSTGRES_PASSWORD"),
        ("database", "BUTLERS_DB_POOL_MAX_SIZE"),
        ("database", "DATABASE_URL"),
        ("connector", "GOOGLE_OAUTH_CLIENT_SECRET"),
        ("connector", "TELEGRAM_BOT_TOKEN"),
        ("connector", "HA_ACCESS_TOKEN"),
        ("domain", "DASHBOARD_API_KEY"),
        ("domain", "BWS_ACCESS_TOKEN"),
        ("domain", "SWITCHBOARD_MCP_URL"),
        ("credential", "CREDENTIAL_STORE_KEY"),
        ("credential", "ENCRYPTION_KEY"),
        ("credential", "FERNET_KEY"),
        ("credential", "RESTIC_PASSWORD_FILE"),
        ("credential", "OFFSITE_SSH_KEY_FILE"),
        ("credential", "BEADS_DOLT_SERVER_HOST"),
        ("credential", "DOLT_REMOTE_PASSWORD"),
        ("credential", "GITHUB_TOKEN"),
        ("credential", "GH_TOKEN"),
        ("credential", "KUBECONFIG"),
        ("credential", "KUBE_TOKEN"),
    ],
)
def test_each_forbidden_credential_class_fails_in_compose_and_helm(
    compose_service, label: str, name: str
) -> None:
    mutated = copy.deepcopy(compose_service)
    mutated["environment"] = {**mutated["environment"], name: "synthetic-marker"}
    assert any(f"{label} credential class: {name}" in v for v in compose_violations(mutated))

    as_list = copy.deepcopy(compose_service)
    as_list["environment"] = [f"{name}=synthetic-marker"]
    assert any(f"{label} credential class" in v for v in compose_violations(as_list))


@needs_helm
@pytest.mark.parametrize(
    ("label", "name"),
    [
        ("database", "POSTGRES_PASSWORD"),
        ("connector", "TELEGRAM_BOT_TOKEN"),
        ("domain", "BWS_ACCESS_TOKEN"),
        ("credential", "GITHUB_TOKEN"),
        ("credential", "KUBECONFIG"),
    ],
)
def test_forbidden_class_fails_in_the_rendered_pod(label: str, name: str) -> None:
    docs = _session_host_docs(_render(*_ENABLED))
    deployment = copy.deepcopy(next(d for d in docs if d["kind"] == "Deployment"))
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    container["env"].append({"name": name, "value": "synthetic-marker"})
    assert any(f"{label} credential class: {name}" in v for v in pod_violations(deployment))


def test_the_postgres_anchor_merge_is_caught(compose_service) -> None:
    compose = yaml.safe_load(
        "x-postgres-env: &postgres-env\n  POSTGRES_HOST: db.invalid\n"
        "services:\n  session-host:\n    environment:\n      <<: *postgres-env\n      HOME: /root\n"
    )
    mutated = {**copy.deepcopy(compose_service), **compose["services"][_SERVICE]}
    assert any("database credential class: POSTGRES_HOST" in v for v in compose_violations(mutated))


def test_structural_credential_routes_fail(compose_service) -> None:
    for key, value in (
        ("extends", {"service": "butlers-control"}),
        ("extends", {"file": "docker-compose.yml", "service": "butlers-up"}),
        ("env_file", [".env"]),
        ("secrets", ["runtime_probe_control_signing_key"]),
        ("configs", [{"source": "runtime_probe_control_verifiers"}]),
        ("volumes", ["runtime_codex:/root/.codex"]),
        ("ports", ["127.0.0.1:41100:41100"]),
    ):
        mutated = {**copy.deepcopy(compose_service), key: value}
        assert any(f"{key} is not allowed" in v for v in compose_violations(mutated)), key
    for networks in (["control-session", "db"], ["egress"]):
        mutated = {**copy.deepcopy(compose_service), "networks": networks}
        assert any("networks must be exactly" in v for v in compose_violations(mutated))
    mutated = {**copy.deepcopy(compose_service), "profiles": []}
    assert any("off by default" in v for v in compose_violations(mutated))


def test_unreviewed_names_and_references_fail_the_pod_scan() -> None:
    deployment = {
        "spec": {"template": {"spec": {
            "automountServiceAccountToken": True,
            "volumes": [{"name": "v"}],
            "containers": [{
                "env": [{"name": "SOMETHING_NEW", "value": "x"},
                        {"name": "HOME", "valueFrom": {"secretKeyRef": {}}}],
                "envFrom": [{"secretRef": {}}],
                "ports": [{"containerPort": 1}],
            }],
        }}}
    }  # fmt: skip
    found = pod_violations(deployment)
    for expected in (
        "unreviewed environment name: SOMETHING_NEW",
        "secretKeyRef",
        "envFrom",
        "ports",
        "volumes are not allowed",
        "service account token",
    ):
        assert any(expected in v for v in found), expected
