"""Import-only migration loading, freshness and scoped module registration."""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pytest

from butlers.testing.migration import load_migration

pytestmark = pytest.mark.unit


def test_load_migration_preserves_names_globals_and_freshness(tmp_path):
    path = tmp_path / "030_example.py"
    path.write_text(
        "revision = 'rel_030'\nstate = []\ndef upgrade(): state.append('up')\ndef downgrade(): state.append('down')\n"
    )
    first = load_migration(path, "_migration_rel_030")
    second = load_migration(path, "_migration_rel_030")
    assert first is not second
    assert first.__name__ == second.__name__ == "_migration_rel_030"
    assert first.revision == "rel_030"
    assert first.__file__ == str(path)
    assert "_migration_rel_030" not in sys.modules
    first.upgrade()
    first.downgrade()
    assert first.state == ["up", "down"]
    assert second.state == []
    assert load_migration(path).__name__ == "_migration_030_example"


def test_load_migration_reports_missing_file_and_import_error(tmp_path):
    path = tmp_path / "missing.py"
    with pytest.raises(FileNotFoundError, match="missing.py"):
        load_migration(path)
    path.write_text("raise RuntimeError('import refusal')\n")
    with pytest.raises(RuntimeError, match="import refusal"):
        load_migration(path)


@pytest.mark.parametrize("spec", [None, SimpleNamespace(loader=None)])
def test_load_migration_refuses_unloadable_spec(tmp_path, monkeypatch, spec):
    path = tmp_path / "migration.py"
    path.write_text("")
    monkeypatch.setattr("importlib.util.spec_from_file_location", lambda *args: spec)
    with pytest.raises(AssertionError, match="migration"):
        load_migration(path)


@pytest.mark.parametrize("preexisting", [False, True])
@pytest.mark.parametrize("fails", [False, True])
def test_load_migration_scoped_registration_restores_even_after_failure(
    tmp_path, preexisting, fails
):
    name = "_migration_kit_registration"
    prior = ModuleType(name)
    path = tmp_path / "migration.py"
    path.write_text(
        "import sys\nfrom dataclasses import dataclass\n"
        "assert sys.modules[__name__].__file__ == __file__\n"
        "@dataclass\nclass Payload:\n    value: int = 7\n"
        + ("raise RuntimeError('import refusal')\n" if fails else "value = 7\n")
    )
    if preexisting:
        sys.modules[name] = prior
    try:
        if fails:
            with pytest.raises(RuntimeError, match="import refusal"):
                load_migration(path, name, register=True)
        else:
            loaded = load_migration(path, name, register=True)
            assert loaded.value == loaded.Payload().value == 7
        if preexisting:
            assert sys.modules[name] is prior
        else:
            assert name not in sys.modules
    finally:
        sys.modules.pop(name, None)
