"""Who may reach the runtime-probe control plane, now that it is live.

Covers REQ-core-credentials-002 and REQ-dashboard-model-settings-001.  Before
the cutover this file pinned the *empty* caller set; bu-0uqgo.11 turns it into
an allowlist of exactly the surfaces that were cut over, which is the same
question asked from the other side.  A signer is mounted into the Dashboard
image now, so "which modules can reach the signing client" stopped being
bookkeeping and became the blast radius.

So this file enumerates importers by parsing every module in the tree with
``ast`` and reading its import statements, rather than grepping for a name a
module might have spelled differently (``from ... import x``, an aliased
module, a nested import inside a function).  The enumeration is exhaustive over
files, so a new importer added anywhere fails this test and forces the question
back to review.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_PACKAGE = "butlers.core.runtime_probe_control"
_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODEL_SETTINGS_SPEC = _REPO_ROOT / "openspec" / "specs" / "dashboard-model-settings" / "spec.md"
_ACTIVE_MODEL_SETTINGS_DELTA = (
    _REPO_ROOT
    / "openspec"
    / "changes"
    / "harden-runtime-auth-and-breaker-attention"
    / "specs"
    / "dashboard-model-settings"
    / "spec.md"
)

#: Everywhere the control plane may legitimately be reached from:
#:
#: * the package itself;
#: * the daemon, which attaches Switchboard's routes and nothing else;
#: * the Secrets API, which imports only the reserved signing-key *name* so it
#:   can refuse to store or return it (REQ-core-credentials-002);
#: * the model-settings router, the one cut-over caller --- Test and verify-all
#:   live there, and the hourly sweep reaches the plane by calling
#:   ``run_verify_all_models`` rather than by holding a client of its own;
#: * the test fixture builder, which is importable but never imported by a
#:   production path --- proven separately below.
_PERMITTED_IMPORTERS = {
    Path("src/butlers/core/runtime_probe_control"),
    Path("src/butlers/daemon.py"),
    Path("src/butlers/api/routers/secrets_v2.py"),
    Path("src/butlers/api/routers/model_settings.py"),
    Path("src/butlers/testing/runtime_probe_control.py"),
}


def _imported_modules(source: str) -> set[str]:
    """Every module name a file imports, including inside functions."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def _production_modules() -> list[Path]:
    return sorted(
        path for directory in ("src", "roster") for path in (_REPO_ROOT / directory).rglob("*.py")
    )


def _control_plane_importers() -> set[Path]:
    importers: set[Path] = set()
    for path in _production_modules():
        if any(name.startswith(_PACKAGE) for name in _imported_modules(path.read_text())):
            importers.add(path.relative_to(_REPO_ROOT))
    return importers


def _requirement_block(spec: str, title: str) -> str:
    """Return one requirement body without conflating neighbouring requirements."""
    marker = f"### Requirement: {title}\n"
    _, remainder = spec.split(marker, 1)
    return remainder.split("\n### Requirement:", 1)[0]


def _scenario_names(requirement: str) -> list[str]:
    return [
        line.removeprefix("#### Scenario: ")
        for line in requirement.splitlines()
        if line.startswith("#### Scenario: ")
    ]


def test_the_enumeration_actually_finds_the_known_importers() -> None:
    """Guard the guard: an enumeration that found nothing would pass vacuously."""
    importers = _control_plane_importers()

    assert Path("src/butlers/daemon.py") in importers
    assert len(importers) > 1


def test_no_caller_outside_the_allowlist_reaches_the_control_plane() -> None:
    """AC4: exactly one Dashboard router holds the signed client, and it is named."""
    unexpected = {
        path
        for path in _control_plane_importers()
        if not any(
            path == permitted or permitted in path.parents for permitted in _PERMITTED_IMPORTERS
        )
    }

    assert unexpected == set(), (
        "an unreviewed module reached the runtime-probe control plane: "
        f"{sorted(str(path) for path in unexpected)}"
    )


def test_no_production_module_imports_the_synthetic_key_fixtures() -> None:
    """Synthetic keys are for tests; a production import would be a real key path."""
    fixtures = "butlers.testing.runtime_probe_control"
    importers = {
        path.relative_to(_REPO_ROOT)
        for path in _production_modules()
        if fixtures in _imported_modules(path.read_text())
    }

    assert importers == set()


def test_the_secrets_api_only_borrows_the_reserved_name() -> None:
    """The Secrets API must not gain a way to reach key material or the client."""
    from butlers.api.routers import secrets_v2

    imported = _imported_modules(Path(secrets_v2.__file__).read_text())
    control_imports = {name for name in imported if name.startswith(_PACKAGE)}

    assert control_imports == {
        f"{_PACKAGE}.keys",
        f"{_PACKAGE}.keys.RESERVED_SIGNING_KEY_SECRET_NAME",
    }


def test_model_settings_no_longer_holds_a_local_verification_adapter() -> None:
    """AC4: the dashboard-local probe path is gone from the module that had it.

    Attribute-level rather than source-level on purpose: an import the module
    re-exports is reachable by a caller whatever the import statement looks
    like, and this is the exact set the activation guard refuses to sign
    beside.  ``run_verify_all_models`` is asserted present so a future deletion
    of the whole surface cannot make the absences pass for the wrong reason.
    """
    from butlers.api.routers import model_settings
    from butlers.core.runtime_probe_control.activation import LOCAL_PROBE_SYMBOLS

    present = [symbol for symbol in LOCAL_PROBE_SYMBOLS if hasattr(model_settings, symbol)]

    assert present == []
    assert hasattr(model_settings, "run_verify_all_models")
    assert model_settings._VERIFY_ALL_CONCURRENCY == 8


def test_active_verify_all_delta_matches_the_signed_control_plane_cutover() -> None:
    """REQ-dashboard-model-settings-001 keeps Verify-all outside dashboard adapters."""
    baseline = _MODEL_SETTINGS_SPEC.read_text(encoding="utf-8")
    delta = _ACTIVE_MODEL_SETTINGS_DELTA.read_text(encoding="utf-8")
    source = (_REPO_ROOT / "src" / "butlers" / "api" / "routers" / "model_settings.py").read_text(
        encoding="utf-8"
    )

    for title in ("Catalog Verify-All API", "Hourly Automated Verification Sweep"):
        canonical = _requirement_block(baseline, title)
        changed = _requirement_block(delta, title)
        assert set(_scenario_names(canonical)).issubset(_scenario_names(changed))
        assert "CredentialStore" not in changed
        assert "dashboard-local" not in changed.lower()
        assert "construct a Codex adapter" not in changed
        assert "switchboard" in changed.lower()
        assert "signed" in changed.lower()
        assert "runtime-probe" in changed.lower()

    catalog_changed = _requirement_block(delta, "Catalog Verify-All API")
    assert "- **WHEN** `POST /api/settings/models/verify-all` is accepted" in catalog_changed

    verify_all_source = source.split("async def run_verify_all_models(", 1)[1].split(
        "# ---------------------------------------------------------------------------\n# POST /api/settings/models/verify-all",
        1,
    )[0]
    assert "client = _probe_client(caller)" in verify_all_source
    assert 'await client.probe(row["id"])' in verify_all_source
    assert "CredentialStore" not in verify_all_source
