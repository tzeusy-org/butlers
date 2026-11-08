"""Roster-derived runtime roles and the hand-written copies that must equal them.

``butlers.db_authority.roles`` turns the roster into role names. Two copies cannot
import it -- ``scripts/init-db.sql`` is SQL, and ``butlers.bootstrap_prerequisite``
pins its admission subjects -- so these checks hold them equal to the derivation:
adding a butler to the roster without granting it in init-db fails here, not at a
runtime ``permission denied``.

Source-only: no PostgreSQL, no connection.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from butlers import bootstrap_prerequisite
from butlers.db_authority import roles
from butlers.db_authority.roles import (
    CONNECTOR_ROLE,
    RosterRoleError,
    butler_role,
    butler_roles,
    butler_schemas,
    runtime_roles,
)

pytestmark = pytest.mark.unit

_REPO = Path(__file__).resolve().parents[2]
_INIT_DB = _REPO / "scripts" / "init-db.sql"


def _init_db_array(name: str) -> tuple[str, ...]:
    match = re.search(rf"\b{name} TEXT\[\] := ARRAY\[(.*?)\];", _INIT_DB.read_text(), re.S)
    assert match, f"init-db.sql no longer declares {name}"
    return tuple(re.findall(r"'([^']+)'", match.group(1)))


def _write_butler(roster: Path, name: str, body: str) -> None:
    (roster / name).mkdir(parents=True)
    (roster / name / "butler.toml").write_text(body)


def test_roles_cover_every_roster_butler_directory() -> None:
    names = sorted(p.parent.name for p in roles.ROSTER_DIR.glob("*/butler.toml"))
    assert names, "the real roster must not be empty"
    assert butler_roles() == tuple(f"butler_{name}_rw" for name in names)
    assert "butler_concierge_rw" in butler_roles()


def test_runtime_roles_append_the_connector_writer() -> None:
    assert runtime_roles() == (*butler_roles(), CONNECTOR_ROLE)


def test_init_db_butler_roles_equal_the_roster() -> None:
    assert _init_db_array("_butler_roles") == butler_roles()


def test_init_db_all_runtime_roles_equal_the_roster() -> None:
    assert _init_db_array("_all_runtime_roles") == runtime_roles()


def test_bootstrap_prerequisite_butlers_equal_the_roster() -> None:
    assert bootstrap_prerequisite._BUTLERS == butler_schemas()


def test_schema_comes_from_the_db_section_then_the_name(tmp_path: Path) -> None:
    _write_butler(tmp_path, "alpha", '[butler]\nname = "alpha"\n')
    _write_butler(tmp_path, "beta", '[butler]\nname = "beta"\n[butler.db]\nschema = "gamma"\n')
    assert butler_roles(tmp_path) == ("butler_alpha_rw", "butler_gamma_rw")


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("[butler\n", "unreadable"),
        ('[other]\nname = "x"\n', "no \\[butler\\] name"),
        ('[butler]\nname = "x"\n[butler.db]\nschema = 3\n', "not a string"),
        ('[butler]\nname = "Bad-Name"\n', "invalid butler schema"),
    ],
)
def test_unreadable_manifest_fails_loudly(tmp_path: Path, body: str, message: str) -> None:
    _write_butler(tmp_path, "x", body)
    with pytest.raises(RosterRoleError, match=message):
        butler_roles(tmp_path)


def test_missing_or_empty_roster_fails_loudly(tmp_path: Path) -> None:
    with pytest.raises(RosterRoleError, match="not found"):
        butler_roles(tmp_path / "absent")
    with pytest.raises(RosterRoleError, match="no butler manifests"):
        butler_roles(tmp_path)


def test_duplicate_schema_fails_loudly(tmp_path: Path) -> None:
    _write_butler(tmp_path, "a", '[butler]\nname = "a"\n[butler.db]\nschema = "same"\n')
    _write_butler(tmp_path, "b", '[butler]\nname = "b"\n[butler.db]\nschema = "same"\n')
    with pytest.raises(RosterRoleError, match="twice"):
        butler_roles(tmp_path)


def test_butler_role_refuses_non_identifiers() -> None:
    with pytest.raises(RosterRoleError):
        butler_role('x"; DROP ROLE y; --')
