"""Public-relation grant manifest: shape and catalog diff (bu-dss7he.6 S2a).

Pure unit tests over fabricated catalogs; the container-backed comparison against a
really migrated database lives in ``tests/integration/test_db_authority_grants_catalog.py``.
"""

from __future__ import annotations

import pytest

from butlers.db_authority.catalog import PublicCatalog, diff_public_catalog
from butlers.db_authority.grants import (
    LEGACY_FLEET_DML,
    PUBLIC_RELATIONS,
    TABLE_PRIVILEGES,
    WA_BRIDGE_RELATIONS,
    expected_privileges,
)
from butlers.db_authority.roles import runtime_roles

pytestmark = pytest.mark.unit

_ROLES = runtime_roles()


def _catalog(rows: dict[str, dict[str, frozenset[str]]], parents=None) -> PublicCatalog:
    privileges = {
        (rel, role): privs for rel, by_role in rows.items() for role, privs in by_role.items()
    }
    return PublicCatalog(privileges, dict(parents or {}), frozenset(_ROLES))


def _manifest_catalog() -> dict[str, dict[str, frozenset[str]]]:
    return {rel: expected_privileges(rel) for rel in PUBLIC_RELATIONS}


def test_manifest_privileges_are_table_privileges() -> None:
    for name, grant in PUBLIC_RELATIONS.items():
        assert grant.fleet <= set(TABLE_PRIVILEGES), name
        for role, privs in grant.by_role.items():
            assert role in _ROLES, (name, role)
            assert privs <= set(TABLE_PRIVILEGES), (name, role)


def test_every_boundary_row_states_its_reason() -> None:
    for name, grant in PUBLIC_RELATIONS.items():
        if grant is not LEGACY_FLEET_DML:
            assert grant.reason, name


def test_dnd_and_runtime_attention_fences_are_explicit_rows() -> None:
    # A deny-by-default regeneration must not widen or drop these (critic, bu-dss7he.6).
    assert expected_privileges("dnd_generation_mutations") == dict.fromkeys(_ROLES, frozenset())
    assert set(expected_privileges("dnd_generation_guard").values()) == {frozenset({"SELECT"})}
    user_context = set(expected_privileges("user_context").values())
    assert user_context == {frozenset({"SELECT", "INSERT", "UPDATE"})}
    outbox = expected_privileges("runtime_attention_outbox")
    assert {r for r, p in outbox.items() if p} == {"butler_switchboard_rw"}


def test_wa_bridge_relations_are_in_the_manifest() -> None:
    assert WA_BRIDGE_RELATIONS <= PUBLIC_RELATIONS.keys()


def test_matching_catalog_has_no_differences() -> None:
    assert diff_public_catalog(_catalog(_manifest_catalog())) == []


def test_unlisted_relation_fails() -> None:
    rows = _manifest_catalog()
    rows["brand_new_table"] = dict.fromkeys(_ROLES, frozenset({"SELECT"}))
    assert diff_public_catalog(_catalog(rows)) == ["relation not in manifest: brand_new_table"]


def test_widened_grant_fails() -> None:
    rows = _manifest_catalog()
    rows["dnd_generation_mutations"]["butler_general_rw"] = frozenset({"SELECT"})
    assert diff_public_catalog(_catalog(rows)) == [
        "dnd_generation_mutations butler_general_rw: manifest - catalog SELECT"
    ]


def test_narrowed_grant_fails() -> None:
    rows = _manifest_catalog()
    rows["state"]["butler_qa_rw"] = frozenset()
    assert diff_public_catalog(_catalog(rows)) == ["state butler_qa_rw: manifest SELECT catalog -"]


def test_partition_is_checked_against_its_parent_row() -> None:
    rows = _manifest_catalog()
    rows["token_usage_ledger_202701"] = dict(rows["token_usage_ledger"])
    parents = {"token_usage_ledger_202701": "token_usage_ledger"}
    assert diff_public_catalog(_catalog(rows, parents)) == []
    rows["token_usage_ledger_202701"]["butler_qa_rw"] = frozenset()
    assert diff_public_catalog(_catalog(rows, parents)) == [
        "token_usage_ledger_202701 butler_qa_rw: manifest SELECT,INSERT,UPDATE,DELETE catalog -"
    ]


def test_absent_manifest_rows_are_reported_unless_allowed() -> None:
    rows = _manifest_catalog()
    for name in WA_BRIDGE_RELATIONS:
        del rows[name]
    found = diff_public_catalog(_catalog(rows))
    assert sorted(found) == sorted(
        f"manifest relation absent from catalog: {n}" for n in WA_BRIDGE_RELATIONS
    )
    assert diff_public_catalog(_catalog(rows), absent_ok=WA_BRIDGE_RELATIONS) == []


def test_missing_role_is_reported_not_skipped() -> None:
    rows = _manifest_catalog()
    catalog = _catalog(rows)
    catalog = PublicCatalog(
        catalog.privileges, catalog.parents, catalog.present_roles - {"butler_concierge_rw"}
    )
    assert diff_public_catalog(catalog) == ["role missing from catalog: butler_concierge_rw"]
