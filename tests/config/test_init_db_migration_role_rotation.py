"""Static shape of init-db's opt-in migration-role rotation (bu-dss7he.6 S5).

The Docker test tests/integration/test_db_authority_ownership.py runs it end to end. These
checks keep the shape reviewable: the refusal lives in the read-only preflight, the only
mutation is one UPDATE over exactly the six pins, and without the setting nothing runs.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_SOURCE = (Path(__file__).resolve().parents[2] / "scripts" / "init-db.sql").read_text(
    encoding="utf-8"
)
_PINS = {
    "restore_drill_executor_admin",
    "dnd_generation_admin",
    "runtime_attention_admin",
    "dashboard_attention",
    "dashboard_stepup",
    "perception_admin",
}


def _block(start_marker: str, end_marker: str) -> str:
    start = _SOURCE.index(start_marker)
    return _SOURCE[start : _SOURCE.index(end_marker, start) + len(end_marker)]


def _preflight() -> str:
    marker = _SOURCE.index("INITIAL_READ_ONLY_BOOTSTRAP_PREFLIGHT")
    start = _SOURCE.index("DO $$", marker)
    return _SOURCE[start : _SOURCE.index("$$;", start) + 3]


def _rotation() -> str:
    return _block("DO $migration_role_rotation$", "$migration_role_rotation$;")


def test_the_ownership_refusal_is_in_the_read_only_preflight() -> None:
    preflight = _preflight()
    assert "butlers.rotate_migration_role_from" in preflight
    assert "still owns" in preflight
    assert "pg_catalog.pg_default_acl" in preflight
    assert preflight.index("still owns") < preflight.index("_amendment_oid := to_regclass")


def test_the_rotation_block_returns_without_the_setting_and_updates_only_the_six_pins() -> None:
    rotation = _rotation()
    body = rotation[rotation.index("BEGIN") :]
    assert re.match(r"BEGIN\s+IF _old IS NULL THEN\s+RETURN;", body)
    pins = set(re.findall(r"to_regclass\('([a-z_]+)\.bootstrap_configuration'\)", rotation))
    assert pins == _PINS
    assert rotation.count("UPDATE") == 1
    assert "SET migration_role = $1 WHERE singleton" in rotation
    for forbidden in ("OWNER TO", "GRANT", "REVOKE", "DROP", "dnd_generation_owner"):
        assert forbidden not in rotation, forbidden


def test_the_rotation_runs_after_the_preflight_and_before_any_section() -> None:
    preflight_end = _SOURCE.index(_preflight()) + len(_preflight())
    rotation_start = _SOURCE.index("DO $migration_role_rotation$")
    assert preflight_end < rotation_start < _SOURCE.index("CREATE EXTENSION")


_INSTALL_FUNCTIONS = {
    "dashboard_attention.install_interface()",
    "dashboard_attention.finalize_interface()",
    "dashboard_attention.deactivate_interface()",
    "dashboard_stepup.install_interface()",
    "dashboard_stepup.finalize_interface()",
    "dashboard_stepup.deactivate_interface()",
    "perception_admin.install_interface()",
    "perception_admin.rollback_interface()",
}


def test_the_old_login_loses_install_execute_only_under_the_rotation_setting() -> None:
    block = _block("DO $rotation_revoke_install$", "$rotation_revoke_install$;")
    body = block[block.index("BEGIN") :]
    assert re.match(r"BEGIN\s+IF _old IS NULL THEN\s+RETURN;", body)
    assert "butlers.rotate_migration_role_from" in block
    assert set(re.findall(r"'([a-z_]+\.[a-z_]+\(\))'", block)) == _INSTALL_FUNCTIONS
    assert block.count("REVOKE ALL ON FUNCTION") == 1
    assert "FROM %I', _fn, _old" in block
    # Runs after every section has granted to the rotated pin, so it is the last statement.
    assert _SOURCE.rstrip().endswith("$rotation_revoke_install$;")
    assert _SOURCE.index("DO $rotation_revoke_install$") > _SOURCE.index(
        "$perception_grant_installer$;"
    )


@pytest.mark.parametrize(
    "schema",
    [
        "restore_drill_executor_admin",
        "dnd_generation_admin",
        "runtime_attention_admin",
        "perception_admin",
    ],
)
def test_a_pin_is_refused_not_silently_re_pointed_on_replay(schema: str) -> None:
    block = _block(f"DO $pin_{schema}$", f"$pin_{schema}$;")
    assert f"'{schema} bootstrap configuration differs'" in block
    assert "migration_role <> COALESCE(" in block
    insert = _SOURCE.index(f"INSERT INTO {schema}.bootstrap_configuration")
    upsert = _SOURCE[insert : _SOURCE.index(f"DO $pin_{schema}$", insert)]
    assert not re.search(r"migration_role\s*=\s*excluded", upsert, re.IGNORECASE)
