"""What this propagation phase is *not* allowed to have changed.

Acceptance for this leaf includes a negative that no functional test can state:
Test, verify-all, and the scheduled verification sweep must still run exactly
the path they ran before, because cutting them over is a separate leaf that
also has to remove every dashboard-local adapter probe.  Landing the control
plane and quietly rewiring one caller would leave the system in a state neither
leaf describes.

So this file enumerates importers by parsing every module in the tree with
``ast`` and reading its import statements, rather than grepping for a name a
module might have spelled differently (``from ... import x``, an aliased
module, a nested import inside a function).  The enumeration is exhaustive over
files, so a new importer added anywhere fails this test and forces the question
back to the leaf that owns it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_PACKAGE = "butlers.core.runtime_probe_control"
_REPO_ROOT = Path(__file__).resolve().parents[2]

#: Everywhere the control plane may legitimately be reached from in this phase.
#: None of these is a model-verification caller:
#:
#: * the package itself;
#: * the daemon, which attaches Switchboard's route and nothing else;
#: * the Secrets API, which imports only the reserved signing-key *name* so it
#:   can refuse to store or return it (REQ-core-credentials-002);
#: * the test fixture builder, which is importable but never imported by a
#:   production path --- proven separately below.
_PERMITTED_IMPORTERS = {
    Path("src/butlers/core/runtime_probe_control"),
    Path("src/butlers/daemon.py"),
    Path("src/butlers/api/routers/secrets_v2.py"),
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


def test_the_enumeration_actually_finds_the_known_importers() -> None:
    """Guard the guard: an enumeration that found nothing would pass vacuously."""
    importers = _control_plane_importers()

    assert Path("src/butlers/daemon.py") in importers
    assert len(importers) > 1


def test_no_caller_outside_the_control_plane_has_been_cut_over() -> None:
    """Criterion 10: Test, verify-all, and the sweep are untouched here."""
    unexpected = {
        path
        for path in _control_plane_importers()
        if not any(
            path == permitted or permitted in path.parents for permitted in _PERMITTED_IMPORTERS
        )
    }

    assert unexpected == set(), (
        "a caller was cut over to the runtime-probe control plane in the propagation "
        f"phase: {sorted(str(path) for path in unexpected)}"
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


def test_model_settings_still_builds_its_own_verification_adapter() -> None:
    """The dashboard's local probe path is still exactly where it was.

    Removing it is the *next* leaf's job, and doing it here would strand the
    dashboard: the production signing-key mount does not exist yet, so a
    cut-over caller would have no working path at all.
    """
    from butlers.api.routers import model_settings

    assert hasattr(model_settings, "_create_verification_adapter")
    assert hasattr(model_settings, "run_verify_all_models")
    assert model_settings._VERIFY_ALL_CONCURRENCY == 8
