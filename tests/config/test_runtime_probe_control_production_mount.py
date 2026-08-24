"""Production Compose mounts the signer to Dashboard alone.

Covers REQ-core-credentials-002 (Asymmetric Runtime-Probe Control Capability),
acceptance criteria 2 and 9; the plane it mounts is the one
REQ-dashboard-model-settings-001 verifies models over.  Its predecessor pinned the *absence* of both
mounts so the activation could not arrive as a silent diff; this file is that
activation, asserted from the other side.

The asymmetry is the whole design and it is a deployment property, not a code
property: Dashboard holds a private signer, every verifying process holds only
public keys, and a stolen keyring buys an attacker nothing.  A future edit that
hands the keyring's mount to all-butlers costs nothing; one that hands the
*signer* to all-butlers hands a spawning stack the ability to mint its own
probe capabilities.  Only a test that names the services can tell those two
diffs apart.

These tests read files.  They never render, start, or inspect a live stack, and
they never read a deployment secret --- the tracked defaults asserted here are
placeholders that are deliberately not keys.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest
import yaml

from butlers.core.runtime_probe_control.keys import (
    RESERVED_SIGNING_KEY_SECRET_NAME,
    SIGNER_PATH,
    VERIFIER_KEYRING_PATH,
    RuntimeProbeControlKeyError,
    parse_signer_document,
    parse_verifier_keyring_document,
)

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE = _REPO_ROOT / "docker-compose.yml"
_ENV_EXAMPLE = _REPO_ROOT / ".env.example"
_SRC = _REPO_ROOT / "src" / "butlers"
_KEYS_MODULE = _SRC / "core" / "runtime_probe_control" / "keys.py"

_SIGNER_SECRET = "runtime_probe_control_signing_key"
_VERIFIER_CONFIG = "runtime_probe_control_verifiers"

#: The two Dashboard runtimes: the only signing side in the deployment.
_SIGNING_SERVICES = ("dashboard-api", "dashboard-api-hotreload")
#: The two all-butlers runtimes: Switchboard verifies here and never signs.
_VERIFYING_ONLY_SERVICES = ("butlers-up", "butlers-up-hotreload")

_PLACEHOLDERS = _REPO_ROOT / "deploy" / "runtime-probe-control"
_SIGNER_PLACEHOLDER = _PLACEHOLDERS / "signing-key-unprovisioned.json"
_VERIFIER_PLACEHOLDER = _PLACEHOLDERS / "verifiers-unprovisioned.json"


def _compose() -> dict:
    return yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))


# ── The mounts themselves ───────────────────────────────────────────────


@pytest.mark.parametrize("service_name", _SIGNING_SERVICES)
def test_dashboard_receives_the_private_signer_owner_read_only(service_name: str) -> None:
    """Mode 0400 is the property the loader checks before it will sign at all."""
    service = _compose()["services"][service_name]

    mounts = service.get("secrets") or []
    assert [mount["source"] for mount in mounts] == [_SIGNER_SECRET]
    assert mounts[0]["target"] == _SIGNER_SECRET
    # Compose renders an octal literal to an int; 0o400 is owner-read-only.
    assert mounts[0]["mode"] == 0o400


@pytest.mark.parametrize("service_name", _VERIFYING_ONLY_SERVICES)
def test_all_butlers_receives_no_private_file(service_name: str) -> None:
    """The verifying stack declares no ``secrets:`` key whatsoever.

    Asserted as absence of the whole block rather than absence of one name:
    a stack that holds no private file at all cannot leak one through a mount
    somebody adds later without touching this line.
    """
    service = _compose()["services"][service_name]

    assert "secrets" not in service


@pytest.mark.parametrize("service_name", _SIGNING_SERVICES + _VERIFYING_ONLY_SERVICES)
def test_both_sides_share_exactly_one_public_keyring_source(service_name: str) -> None:
    """One source, one path, both sides --- so they cannot disagree about trust."""
    service = _compose()["services"][service_name]

    mounts = service.get("configs") or []
    assert [mount["source"] for mount in mounts] == [_VERIFIER_CONFIG]
    assert mounts[0]["target"] == str(VERIFIER_KEYRING_PATH)


def test_no_other_service_receives_either_document() -> None:
    """Nothing outside those four names touches the control plane's material."""
    services = _compose()["services"]
    permitted = set(_SIGNING_SERVICES) | set(_VERIFYING_ONLY_SERVICES)

    for name, service in services.items():
        if name in permitted:
            continue
        sources = {mount["source"] for mount in (service.get("secrets") or [])}
        sources |= {mount["source"] for mount in (service.get("configs") or [])}
        assert not sources & {_SIGNER_SECRET, _VERIFIER_CONFIG}, name


def test_the_signer_mount_lands_on_the_path_the_loader_reads() -> None:
    """Compose's short target name resolves under ``/run/secrets``."""
    assert SIGNER_PATH.parent == Path("/run/secrets")
    assert SIGNER_PATH.name == _SIGNER_SECRET


# ── Provisioning is host configuration, never committed material ────────


def test_both_sources_are_host_paths_supplied_by_the_operator() -> None:
    """A path is configuration; the document behind it never enters git."""
    compose = _compose()

    signer = compose["secrets"][_SIGNER_SECRET]["file"]
    verifiers = compose["configs"][_VERIFIER_CONFIG]["file"]

    assert signer.startswith("${RUNTIME_PROBE_CONTROL_SIGNING_KEY_FILE:-")
    assert verifiers.startswith("${RUNTIME_PROBE_CONTROL_VERIFIERS_FILE:-")


def test_the_launcher_needs_no_manual_provisioning_step() -> None:
    """AC9: one ordinary ``up`` on an unprovisioned machine, no second stage.

    A ``${VAR:?}`` source would make the canonical launcher fail before it
    started anything, which is exactly the manual two-stage launcher this
    criterion forbids.  Defaulting instead keeps startup single-stage; the
    tests below prove the default is inert.
    """
    raw = _COMPOSE.read_text(encoding="utf-8")

    assert "RUNTIME_PROBE_CONTROL_SIGNING_KEY_FILE:?" not in raw
    assert "RUNTIME_PROBE_CONTROL_VERIFIERS_FILE:?" not in raw


@pytest.mark.parametrize(
    ("declared_default", "tracked_file"),
    (
        (f"./deploy/runtime-probe-control/{_SIGNER_PLACEHOLDER.name}", _SIGNER_PLACEHOLDER),
        (f"./deploy/runtime-probe-control/{_VERIFIER_PLACEHOLDER.name}", _VERIFIER_PLACEHOLDER),
    ),
)
def test_each_default_points_at_a_tracked_placeholder(
    declared_default: str, tracked_file: Path
) -> None:
    """The fallback resolves to a file that is really in the tree."""
    raw = _COMPOSE.read_text(encoding="utf-8")

    assert declared_default in raw
    assert tracked_file.is_file()


def test_the_signer_placeholder_is_rejected_by_the_loader() -> None:
    """AC9 rollback state: an unprovisioned deployment signs nothing.

    Parsed through the real parser rather than eyeballed, so "inert" means
    *this code path refuses it* rather than "it looks wrong to a reader".
    """
    document = _SIGNER_PLACEHOLDER.read_bytes()

    with pytest.raises(RuntimeProbeControlKeyError):
        parse_signer_document(document)


def test_the_verifier_placeholder_is_rejected_by_the_loader() -> None:
    """And an unprovisioned verifier answers 503 for every key id."""
    document = _VERIFIER_PLACEHOLDER.read_bytes()

    with pytest.raises(RuntimeProbeControlKeyError):
        parse_verifier_keyring_document(document)


@pytest.mark.parametrize("placeholder", (_SIGNER_PLACEHOLDER, _VERIFIER_PLACEHOLDER))
def test_no_placeholder_carries_a_key_shaped_field(placeholder: Path) -> None:
    """Nothing tracked here may be mistaken for --- or grown into --- a key.

    Asserted as absence of the field names a real document uses, so the test
    never has to reproduce the material it is guarding against.
    """
    document = json.loads(placeholder.read_text(encoding="utf-8"))

    assert not {"private_key_b64u", "public_key_b64u", "kid", "alg"} & set(document)
    assert "docs/operations/runtime-probe-control-keys.md" in document["how_to_provision"]


def test_env_example_documents_both_settings_as_paths() -> None:
    """An operator finds the contract where they configure everything else."""
    raw = _ENV_EXAMPLE.read_text(encoding="utf-8")

    assert "RUNTIME_PROBE_CONTROL_SIGNING_KEY_FILE=" in raw
    assert "RUNTIME_PROBE_CONTROL_VERIFIERS_FILE=" in raw
    assert "docs/operations/runtime-probe-control-keys.md" in raw


# ── The mount stays the only way in ─────────────────────────────────────


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


def test_only_the_activation_gate_reads_the_signer_snapshot() -> None:
    """AC4: no caller reaches ``signer_snapshot`` around the pre-mount guard.

    ``activated_signer_snapshot`` is the whole reason the guard is not
    advisory.  If a caller could take the raw snapshot instead, an image
    carrying a dashboard-local probe would sign anyway, and the guard would be
    a comment.  So the raw readers are enumerated and confined to the package.
    """
    raw_reader = re.compile(r"\bmatch_signer_to_keyring\b|\bsigner_snapshot\b")
    callers = sorted(
        path.relative_to(_REPO_ROOT).as_posix()
        for path in _SRC.rglob("*.py")
        if raw_reader.search(
            # ``activated_signer_snapshot`` contains ``signer_snapshot``; strip
            # the gate's own name before asking who reads the raw one.
            path.read_text(encoding="utf-8").replace("activated_signer_snapshot", "")
        )
        and "runtime_probe_control" not in path.parts
    )

    assert callers == []
