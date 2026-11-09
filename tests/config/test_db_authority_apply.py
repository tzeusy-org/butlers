"""Grant apply planning: pure tests over fabricated catalogs (bu-dss7he.6 S2b)."""

from __future__ import annotations

import pytest

from butlers.db_authority.apply import GrantChange, plan_public_grants
from butlers.db_authority.catalog import PublicCatalog
from butlers.db_authority.grants import PUBLIC_RELATIONS, WA_BRIDGE_RELATIONS, expected_privileges
from butlers.db_authority.roles import runtime_roles

pytestmark = pytest.mark.unit

_ROLES = runtime_roles()


def _catalog(*, after_init_db_replay: bool) -> dict:
    return {
        (rel, role): privs
        for rel in PUBLIC_RELATIONS
        if rel not in WA_BRIDGE_RELATIONS
        for role, privs in expected_privileges(
            rel, after_init_db_replay=after_init_db_replay
        ).items()
    }


def _plan(privileges: dict) -> list[GrantChange]:
    return plan_public_grants(PublicCatalog(privileges, {}, frozenset(_ROLES)))


def test_matching_catalog_needs_no_changes() -> None:
    assert _plan(_catalog(after_init_db_replay=False)) == []


def test_replayed_catalog_is_narrowed_and_never_regrants_beyond_manifest() -> None:
    changes = _plan(_catalog(after_init_db_replay=True))
    assert changes
    touched = {c.relation for c in changes}
    assert "ambient_projections" in touched
    # Every relation not marked as replay-changed is untouched.
    from butlers.db_authority.grants import REPLAY_WIDENED

    assert touched <= REPLAY_WIDENED
    # A replayed database only ever narrows: the replay's removals are never granted back.
    assert {c.action for c in changes} == {"REVOKE"}
    # Revokes are ordered before grants.
    actions = [c.action for c in changes]
    assert actions == sorted(actions, key=lambda a: a != "REVOKE")


def test_missing_privilege_is_granted_and_only_that_privilege() -> None:
    privileges = _catalog(after_init_db_replay=False)
    privileges[("audit_log", "butler_health_rw")] = frozenset({"SELECT", "INSERT", "UPDATE"})
    assert _plan(privileges) == [GrantChange("GRANT", "audit_log", "butler_health_rw", ("DELETE",))]


def test_extra_privilege_on_a_boundary_row_is_revoked() -> None:
    privileges = _catalog(after_init_db_replay=False)
    privileges[("dnd_generation_mutations", "butler_general_rw")] = frozenset({"SELECT"})
    assert _plan(privileges) == [
        GrantChange("REVOKE", "dnd_generation_mutations", "butler_general_rw", ("SELECT",))
    ]


def test_relation_outside_the_manifest_is_never_touched() -> None:
    privileges = _catalog(after_init_db_replay=False)
    privileges[("not_in_manifest", "butler_general_rw")] = frozenset({"SELECT", "DELETE"})
    assert _plan(privileges) == []
