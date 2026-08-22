"""Production Compose carries neither runtime-probe control mount yet.

Covers REQ-core-credentials-002 (Asymmetric Runtime-Probe Control Capability).
This leaf builds the inert trust representation only: the parser, the receipt
table, and the reserved name.  Mounting the signer into Dashboard and the
keyring into the butler stack is a separate, deliberate step, so these tests
pin the current state --- no mount, no environment fallback, no other way for a
signing key to reach the process --- and will fail loudly when that step lands
so the reviewer sees the activation instead of inheriting it.

These tests read files.  They never render, start, or inspect a live stack, and
they never read a deployment secret.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
import yaml

from butlers.core.runtime_probe_control.keys import (
    RESERVED_SIGNING_KEY_SECRET_NAME,
    SIGNER_PATH,
    VERIFIER_KEYRING_PATH,
)

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE = _REPO_ROOT / "docker-compose.yml"
_SRC = _REPO_ROOT / "src" / "butlers"
_KEYS_MODULE = _SRC / "core" / "runtime_probe_control" / "keys.py"

_SECRET_NAMES = ("runtime_probe_control_signing_key", "runtime_probe_control_verifiers")
_MOUNT_PATHS = (str(SIGNER_PATH), str(VERIFIER_KEYRING_PATH))
_ACTIVATION_SURFACES = (
    "dashboard-api",
    "dashboard-api-hotreload",
    "butlers-up",
    "butlers-up-hotreload",
)


def _compose() -> dict:
    return yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))


def test_compose_declares_no_runtime_probe_control_secret() -> None:
    """Nothing in production Compose names either document."""
    raw = _COMPOSE.read_text(encoding="utf-8")

    for name in _SECRET_NAMES:
        assert name not in raw
    for path in _MOUNT_PATHS:
        assert path not in raw


@pytest.mark.parametrize("service_name", _ACTIVATION_SURFACES)
def test_activation_surfaces_mount_neither_document(service_name: str) -> None:
    """The Dashboard and all-butlers definitions are the ones that will change.

    Asserting on them by name keeps the eventual mount from arriving as a
    silent diff in a large Compose file.
    """
    service = _compose()["services"][service_name]

    assert not service.get("secrets")
    for volume in service.get("volumes", []):
        rendered = volume if isinstance(volume, str) else str(volume)
        for path in _MOUNT_PATHS:
            assert path not in rendered
    for entry in service.get("environment", []) or []:
        assert RESERVED_SIGNING_KEY_SECRET_NAME not in str(entry)


def test_no_service_carries_the_reserved_environment_variable() -> None:
    """The signing key is a file, never a value in the process environment."""
    services = _compose()["services"]

    for service in services.values():
        environment = service.get("environment") or []
        entries = environment if isinstance(environment, list) else list(environment)
        for entry in entries:
            assert RESERVED_SIGNING_KEY_SECRET_NAME not in str(entry)


def test_loader_has_no_environment_or_database_fallback() -> None:
    """The only source of a signing key is its mounted file.

    An environment or credential-store fallback would reintroduce exactly the
    shared-value path the file mount exists to remove, so its absence is a
    property worth pinning rather than a coincidence of the current code.
    Prose is excluded deliberately: the module *documents* having no fallback,
    and a docstring match would make this test pass on the wrong evidence.
    """
    tree = ast.parse(_KEYS_MODULE.read_text(encoding="utf-8"))
    forbidden = {"environ", "getenv", "CredentialStore", "DatabaseManager"}

    used = {
        node.attr if isinstance(node, ast.Attribute) else node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute | ast.Name)
    }

    assert not used & forbidden


def test_reserved_name_is_referenced_only_by_the_loader_and_the_exclusion() -> None:
    """Any third reference would be a new way to reach the key."""
    package = "src/butlers/core/runtime_probe_control"
    referencing = sorted(
        path.relative_to(_REPO_ROOT).as_posix()
        for path in _SRC.rglob("*.py")
        if "RESERVED_SIGNING_KEY_SECRET_NAME" in path.read_text(encoding="utf-8")
    )
    outside_package = [path for path in referencing if not path.startswith(package)]

    assert outside_package == ["src/butlers/api/routers/secrets_v2.py"]


def test_the_reserved_literal_is_written_down_once() -> None:
    """One spelling, in the loader; everything else imports it."""
    spelled = sorted(
        path.relative_to(_REPO_ROOT).as_posix()
        for path in _SRC.rglob("*.py")
        if RESERVED_SIGNING_KEY_SECRET_NAME in path.read_text(encoding="utf-8")
    )

    assert spelled == ["src/butlers/core/runtime_probe_control/keys.py"]


def test_no_runtime_launches_a_probe_capability_client() -> None:
    """Nothing signs, verifies, or sends a capability yet.

    The representation is inert by design; a control endpoint, signed client,
    or scheduler cutover belongs to later leaves.
    """
    caller = re.compile(r"\bmatch_signer_to_keyring\b|\bsigner_snapshot\b|\bverifier_snapshot\b")
    callers = sorted(
        path.relative_to(_REPO_ROOT).as_posix()
        for path in _SRC.rglob("*.py")
        if caller.search(path.read_text(encoding="utf-8"))
        and "runtime_probe_control" not in path.parts
    )

    assert callers == []
