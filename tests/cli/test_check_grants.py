"""``butlers db check-grants``: exit status and content-blind output (bu-dss7he.6 S2a)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from butlers.cli import cli
from butlers.db_authority.catalog import PublicCatalog
from butlers.db_authority.grants import PUBLIC_RELATIONS, WA_BRIDGE_RELATIONS, expected_privileges
from butlers.db_authority.roles import runtime_roles

pytestmark = pytest.mark.unit


def _catalog(*, widen: bool) -> PublicCatalog:
    privileges = {
        (rel, role): privs
        for rel in PUBLIC_RELATIONS
        if rel not in WA_BRIDGE_RELATIONS
        for role, privs in expected_privileges(rel).items()
    }
    if widen:
        privileges[("dnd_generation_mutations", "butler_general_rw")] = frozenset({"SELECT"})
    return PublicCatalog(privileges, {}, frozenset(runtime_roles()))


def _replayed_catalog() -> PublicCatalog:
    privileges = {
        (rel, role): privs
        for rel in PUBLIC_RELATIONS
        if rel not in WA_BRIDGE_RELATIONS
        for role, privs in expected_privileges(rel, after_init_db_replay=True).items()
    }
    return PublicCatalog(privileges, {}, frozenset(runtime_roles()))


def _run(monkeypatch: pytest.MonkeyPatch, catalog: PublicCatalog, *args: str):
    params = {"host": "h", "port": 5432, "user": "u", "password": "p"}
    monkeypatch.setattr("butlers.db.db_params_from_env", lambda: params)
    monkeypatch.setattr("sqlalchemy.create_engine", lambda url: MagicMock())
    monkeypatch.setattr("butlers.db_authority.catalog.read_public_catalog", lambda conn: catalog)
    return CliRunner().invoke(cli, ["db", "check-grants", *args])


def test_matching_catalog_exits_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _run(monkeypatch, _catalog(widen=False))
    assert result.exit_code == 0, result.output
    assert "Public grants match the manifest" in result.output


def test_difference_exits_nonzero_and_names_only_relation_and_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _run(monkeypatch, _catalog(widen=True))
    assert result.exit_code == 1
    assert "dnd_generation_mutations butler_general_rw: manifest - catalog SELECT" in result.output
    assert "1 grant difference(s)" in result.output


def test_replayed_database_differs_from_intent_but_matches_with_the_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    default = _run(monkeypatch, _replayed_catalog())
    assert default.exit_code == 1
    assert "ambient_projections" in default.output

    flagged = _run(monkeypatch, _replayed_catalog(), "--after-init-db-replay")
    assert flagged.exit_code == 0, flagged.output
