"""Rendered-chart password distribution for the public MCP roles (bu-y66o00.4, tasks 1.6, 1.8).

RFC 0039 D9: the address range does not separate pods, so custody does. No Butlers chart
references the publisher or the subscription owner (OD14, OD15), the ``mcp_public_ro``
password belongs to the public chart only, and the ``mcp_proposals_reader`` password is
mounted by ``butlers-up`` only. The public-pod half arrives with the public chart (2.4).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

_CHART = Path(__file__).resolve().parents[2] / "deploy" / "helm" / "butlers"
needs_helm = pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")
_SITE = [
    "--set", "trackerEgress.deniedCidrs={192.0.2.1/32,192.0.2.2/32}",
    "--set", "egressPolicy.allowedCidrs={198.51.100.7/32}",
]  # fmt: skip

# Names only; never values.
NEVER = (
    "butlers_projection_publisher",
    "mcp_subscription_owner",
    "postgres-mcp-subscription-owner-user",
    "MCP_SUBSCRIPTION_OWNER_DB_PASSWORD",
    "MCP_PUBLIC_RO_DB_PASSWORD",
    "postgres-mcp-public-ro-user",
    "mcp_public_ro",
)
IMPORTER_ONLY = (
    "MCP_PROPOSALS_READER_DB_PASSWORD",
    "postgres-mcp-proposals-reader-user",
    "mcp_proposals_reader",
)
IMPORTER_WORKLOAD = "butlers-up"


def violations(docs: list[dict]) -> list[str]:
    """Name each rendered object that references a credential it must not hold."""
    found = []
    for doc in docs:
        text = yaml.safe_dump(doc)
        label = f"{doc.get('kind')}/{doc.get('metadata', {}).get('name')}"
        found += [f"{label} references {name}" for name in NEVER if name in text]
        if doc.get("metadata", {}).get("name") != IMPORTER_WORKLOAD:
            found += [f"{label} references {name}" for name in IMPORTER_ONLY if name in text]
    return found


def _render(env: str, source: str) -> list[dict]:
    result = subprocess.run(
        ["helm", "template", "butlers", str(_CHART), "-f", str(_CHART / "values.yaml"),
         "-f", str(_CHART / f"values.{env}.yaml"), "--set", "image.tag=t",
         "--set", "frontendImage.tag=t", "--set", f"localSecrets.source={source}", *_SITE],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    return [d for d in yaml.safe_load_all(result.stdout) if d]


@needs_helm
@pytest.mark.parametrize("source", ["local", "bws"])
@pytest.mark.parametrize("env", ["dev", "prod"])
def test_no_butlers_chart_object_holds_a_public_mcp_credential(env: str, source: str) -> None:
    docs = _render(env, source)
    assert docs, "chart rendered nothing"
    assert violations(docs) == []


def test_chart_sources_do_not_name_the_forbidden_credentials() -> None:
    for path in sorted(_CHART.rglob("*")):
        if path.is_file() and path.suffix in {".yaml", ".tpl", ".example", ".md"}:
            body = path.read_text()
            for name in NEVER:
                assert name not in body, f"{path.relative_to(_CHART)} names {name}"


def _workload(name: str, secret: str) -> dict:
    return {
        "kind": "Deployment",
        "metadata": {"name": name},
        "spec": {"env": [{"valueFrom": {"secretKeyRef": {"name": secret, "key": "password"}}}]},
    }


@pytest.mark.parametrize(
    ("name", "secret", "expected"),
    [
        ("dashboard-api", "postgres-mcp-subscription-owner-user", "references"),
        ("butlers-up", "postgres-mcp-subscription-owner-user", "references"),
        ("butlers-up", "postgres-mcp-public-ro-user", "references"),
        ("dashboard-api", "postgres-mcp-proposals-reader-user", "references"),
    ],
)
def test_the_check_goes_red_on_a_mount_in_the_wrong_workload(name, secret, expected) -> None:
    found = violations([_workload(name, secret)])
    assert found and expected in found[0] and name in found[0]


def test_only_the_importer_may_mount_the_proposals_reader_password() -> None:
    assert violations([_workload("butlers-up", "postgres-mcp-proposals-reader-user")]) == []
