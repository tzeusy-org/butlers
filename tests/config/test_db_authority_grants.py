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


def test_replay_widened_rows_record_intent_and_the_replay_result() -> None:
    from butlers.db_authority.grants import REPLAY_WIDENED

    assert {"ambient_projections", "runtime_attention_operator_control", "cost_claims"} <= (
        REPLAY_WIDENED
    )
    intent = expected_privileges("ambient_projections")
    replayed = expected_privileges("ambient_projections", after_init_db_replay=True)
    assert set(intent.values()) == {frozenset({"SELECT"})}
    assert set(replayed.values()) == {LEGACY_FLEET_DML.fleet}
    # Rows without a replay override read the same either way.
    assert expected_privileges("user_context") == expected_privileges(
        "user_context", after_init_db_replay=True
    )


# --- The projection publisher is a declared principal outside the runtime fleet ---


def _publisher_catalog(rows: dict[str, frozenset[str]], parents=None, *, present=True):
    from butlers.db_authority.grants import PROJECTION_PUBLISHER

    return PublicCatalog(
        {(rel, PROJECTION_PUBLISHER): privs for rel, privs in rows.items()},
        dict(parents or {}),
        frozenset({PROJECTION_PUBLISHER}) if present else frozenset(),
    )


def test_publisher_is_declared_outside_the_runtime_fleet() -> None:
    from butlers.db_authority.grants import PROJECTION_PUBLISHER, publisher_relations
    from butlers.knowledge_read.manifest import load_manifest

    assert PROJECTION_PUBLISHER == "butlers_projection_publisher"
    assert PROJECTION_PUBLISHER not in _ROLES
    published = {
        t.source.split(".", 1)[1] for t in load_manifest().tables if t.source.startswith("public.")
    }
    assert publisher_relations() == published
    assert published == {"entities", "entity_graph_edges", "entity_graph_withheld_counts"}
    assert published <= PUBLIC_RELATIONS.keys()


def test_publisher_holds_select_on_exactly_the_published_public_tables() -> None:
    from butlers.db_authority.catalog import diff_publisher_catalog
    from butlers.db_authority.grants import publisher_relations

    rows = {rel: frozenset() for rel in PUBLIC_RELATIONS}
    rows.update({rel: frozenset({"SELECT"}) for rel in publisher_relations()})
    assert diff_publisher_catalog(_publisher_catalog(rows), published=True) == []
    # Before reconcile creates the publication, it holds nothing at all.
    assert (
        diff_publisher_catalog(_publisher_catalog({"entities": frozenset()}), published=False) == []
    )


def test_publisher_widening_or_narrowing_is_named() -> None:
    from butlers.db_authority.catalog import diff_publisher_catalog
    from butlers.db_authority.grants import publisher_relations

    rows = {rel: frozenset({"SELECT"}) for rel in publisher_relations()}
    rows["audit_log"] = frozenset({"SELECT"})
    rows["entities"] = frozenset({"SELECT", "UPDATE"})
    del rows["entity_graph_edges"]
    assert diff_publisher_catalog(_publisher_catalog(rows), published=True) == [
        "audit_log butlers_projection_publisher: manifest - catalog SELECT",
        "entities butlers_projection_publisher: manifest SELECT catalog SELECT,UPDATE",
        "publisher relation absent from catalog: entity_graph_edges",
    ]


def test_publisher_grants_before_publication_exists_are_refused() -> None:
    from butlers.db_authority.catalog import diff_publisher_catalog

    assert diff_publisher_catalog(
        _publisher_catalog({"entities": frozenset({"SELECT"})}), published=False
    ) == ["entities butlers_projection_publisher: manifest - catalog SELECT"]


def test_publisher_absent_is_not_a_difference_where_cnpg_roles_do_not_exist() -> None:
    from butlers.db_authority.catalog import diff_publisher_catalog

    assert diff_publisher_catalog(_publisher_catalog({}, present=False), published=True) == []
