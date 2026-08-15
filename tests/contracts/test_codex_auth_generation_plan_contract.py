"""Static regressions for the Codex auth generation-fencing planning packet.

The packet is deliberately planning-only.  These checks keep the future
implementation instructions executable enough that the security properties do
not collapse into prose-only intent before implementation starts.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.contract

_ROOT = Path(__file__).resolve().parents[2]
_CHANGE = _ROOT / "openspec/changes/generation-fenced-codex-auth-rotation-provenance"
_PLAN = (
    _ROOT / "docs/superpowers/plans/2026-08-13-generation-fenced-codex-auth-rotation-provenance.md"
)


def _read(path: Path) -> str:
    assert path.is_file(), f"required generation-fencing artifact is missing: {path}"
    return path.read_text(encoding="utf-8")


def _packet() -> str:
    return "\n".join(
        [
            _read(_PLAN),
            _read(_CHANGE / "design.md"),
            _read(_CHANGE / "tasks.md"),
            *(_read(path) for path in sorted((_CHANGE / "specs").glob("*/spec.md"))),
        ]
    )


def test_packet_allocates_the_next_free_core_revision() -> None:
    """The implementation cannot fork the core Alembic chain at occupied core_198."""
    packet = _packet()

    assert "core_199_codex_auth_generation_provenance.py" in packet
    assert 'revision == "core_199"' in packet
    assert 'down_revision == "core_198"' in packet
    assert "core_198_codex_auth_generation_provenance.py" not in packet


def test_packet_defines_guarded_abandonment_for_every_prelaunch_failure() -> None:
    """Prepared and launched operations need an explicit terminalization path."""
    packet = _packet()

    for term in (
        "abandon_codex_auth_operation",
        "codex_auth_abandon_operation",
        "stage_prepare_failed",
        "prelaunch_cancelled",
        "launch_mark_failed",
        "launch_failed",
        "duplicate abandonment",
    ):
        assert term in packet


def test_packet_repairs_public_secret_acl_and_row_visibility_on_every_bootstrap() -> None:
    """Broad public-table grants cannot reveal or forge the reserved binding."""
    packet = _packet()

    for term in (
        "FORCE ROW LEVEL SECURITY",
        "codex_auth_non_reserved_secrets",
        "codex_auth_reserved_owner",
        "REVOKE SELECT (codex_auth_generation_id)",
        "REVOKE UPDATE (codex_auth_generation_id)",
        "has_column_privilege",
        "after every broad-grant bootstrap rerun",
    ):
        assert term in packet


def test_packet_treats_any_present_malformed_raw_row_as_unprovable() -> None:
    """Only physical row absence is eligible for first device-auth bootstrap."""
    packet = _packet()

    for term in (
        "no reserved raw row at all",
        "present malformed raw row",
        "direct owner replacement",
        "test_present_malformed_raw_row_cannot_bootstrap",
    ):
        assert term in packet


def test_packet_requires_behavior_executing_kernel_peer_isolation() -> None:
    """Mode 0600 under one shared identity is not a peer-child boundary."""
    packet = _packet()

    for term in (
        "kernel-enforced per-invocation isolation",
        "unique leased outer UID/GID",
        "test_codex_peer_cannot_read_another_operation_stage",
        "test_codex_peer_cannot_write_another_operation_stage",
        "--dangerously-bypass-approvals-and-sandbox",
    ):
        assert term in packet


def test_packet_requires_real_postgres_race_and_effective_role_evidence() -> None:
    """Mocks do not prove serialization or the database privilege boundary."""
    packet = _packet()

    for term in (
        "test_two_conditional_successors_have_exactly_one_winner",
        "test_owner_replacement_racing_completion_has_no_stale_health_write",
        "test_revoke_racing_device_auth_has_no_stale_health_write",
        "test_duplicate_completion_commits_exactly_once",
        "test_effective_roles_execute_only_their_guarded_operations",
        "real PostgreSQL connections",
        "Mocks may supplement but SHALL NOT substitute",
    ):
        assert term in packet


def test_packet_makes_the_migration_intentionally_irreversible() -> None:
    """Downgrade must fail transactionally instead of implying an undefined cleanup."""
    packet = _packet()

    for term in (
        "intentionally irreversible",
        "test_core_199_downgrade_fails_without_catalog_or_data_change",
        "downgrade()",
        "future independently reviewed migration",
    ):
        assert term in packet


def test_packet_reviews_the_captured_base_through_head() -> None:
    """The final diff check must cover committed implementation, not only dirt."""
    plan = _read(_PLAN)

    assert 'IMPLEMENTATION_REVIEW_BASE="$(git rev-parse HEAD)"' in plan
    assert 'git diff --check "${IMPLEMENTATION_REVIEW_BASE}...HEAD"' in plan
    assert 'git diff --stat "${IMPLEMENTATION_REVIEW_BASE}...HEAD"' in plan
