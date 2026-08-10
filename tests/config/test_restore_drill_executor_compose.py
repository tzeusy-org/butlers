"""Compose boundary coverage for the isolated restore-drill executor.

REQ-database-security-006 requires the privileged recovery credential to be
mounted only into the db-only executor.  These are configuration tests: they
do not render or start a Compose stack and therefore cannot read a deployment
secret or mutate a live runtime.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _compose() -> dict:
    return yaml.safe_load((_REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))


def _environment(service: dict) -> dict:
    environment = service.get("environment", {})
    assert isinstance(environment, dict)
    return environment


def _environment_keys(service: dict) -> set[str]:
    environment = service.get("environment", {})
    if isinstance(environment, dict):
        return set(environment)
    assert isinstance(environment, list)
    return {entry.split("=", 1)[0] for entry in environment}


def test_restore_drill_executor_is_db_only_and_gets_only_its_private_secret() -> None:
    """REQ-database-security-006: no shared DB environment or broad network."""
    compose = _compose()
    service = compose["services"]["restore-drill-executor"]

    assert service["networks"] == ["db"]
    assert "ports" not in service
    assert "expose" not in service
    assert "privileged" not in service
    assert "cap_add" not in service
    assert "security_opt" not in service
    assert "butlers_backups:/backups:ro" in service["volumes"]
    assert service["secrets"] == [
        {
            "source": "restore_drill_executor_password",
            "target": "restore_drill_executor_password",
            "mode": 0o400,
        }
    ]

    environment = _environment(service)
    assert "RESTORE_DRILL_EXECUTOR_PASSWORD_FILE" not in environment
    assert not any(key.startswith("POSTGRES_") for key in environment)
    assert "DATABASE_URL" not in environment
    assert service["entrypoint"][-1] == "butlers.jobs.restore_drill_executor"

    secret = compose["secrets"]["restore_drill_executor_password"]
    assert secret["file"].startswith("${RESTORE_DRILL_EXECUTOR_PASSWORD_FILE:")


def test_private_executor_secret_is_absent_from_every_normal_runtime_service() -> None:
    """The secret mount belongs only to the deterministic executor service."""
    compose = _compose()

    for name, service in compose["services"].items():
        if name == "restore-drill-executor":
            continue
        assert "restore_drill_executor_password" not in repr(service.get("secrets", []))
        assert "RESTORE_DRILL_EXECUTOR_PASSWORD_FILE" not in _environment_keys(service)
