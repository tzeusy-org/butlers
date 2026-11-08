"""Ratchet: no new literal fleet-wide role lists in migration revisions.

A revision that spells out "every butler" by hand freezes the fleet at the day it was
written; every such list written before the concierge existed omits
``butler_concierge_rw``. New revisions grant through
``butlers.db_authority.roles.butler_roles()`` / ``runtime_roles()`` instead.

A *fleet list* is any of these naming at least ``_FLEET_THRESHOLD`` distinct entries:

- a literal tuple, list or set of runtime roles (``butler_*_rw``, ``connector_writer``);
- a literal tuple, list or set of roster schema names (the
  ``[f"butler_{s}_rw" for s in ("general", ...)]`` spelling);
- one string constant (SQL) naming runtime roles.

The threshold is a fixed seven -- over half of today's thirteen butlers -- not a
fraction of the live roster, so adding or removing a butler never moves the line.
Narrow, deliberate reader lists (a few named butlers) stay literal: they are
exceptions the grant manifest will carry as explicit rows, not "everyone".

``_BASELINE`` freezes the existing sites per file and ``_FROZEN_TOTAL`` pins their sum,
so the ratchet only shrinks: a new site fails, a removed site fails until its entry is
lowered, and raising an entry needs a visible edit to the frozen total too. (A file
swapping one site for another at the same count is not detected.) Source-only: parses
Python, never imports revisions.
"""

from __future__ import annotations

import ast
import re
from collections import Counter
from pathlib import Path

import pytest

from butlers.db_authority.roles import CONNECTOR_ROLE, butler_roles, butler_schemas

pytestmark = pytest.mark.unit

_REPO = Path(__file__).resolve().parents[2]
_REVISION_GLOBS = (
    "alembic/versions/**/*.py",
    "roster/*/migrations/*.py",
    "src/butlers/modules/*/migrations/*.py",
)
_FLEET_THRESHOLD = 7
_ROLE = re.compile(r"^butler_[a-z0-9_]+_rw$")
_ROLE_IN_TEXT = re.compile(rf"\b(butler_[a-z0-9_]+_rw|{CONNECTOR_ROLE})\b")

# Existing literal fleet lists, frozen 2026-11-08 at main 5debfdd351 (bu-dss7he.6 S1).
# Lower or delete an entry when its file stops spelling the fleet out; never raise one.
_BASELINE: dict[str, int] = {
    "alembic/versions/core/core_001_foundation.py": 1,
    "alembic/versions/core/core_002_identity.py": 1,
    "alembic/versions/core/core_004_model_and_tokens.py": 1,
    "alembic/versions/core/core_005_self_healing.py": 1,
    "alembic/versions/core/core_006_dashboard.py": 1,
    "alembic/versions/core/core_007_connectors.py": 1,
    "alembic/versions/core/core_008_external_accounts.py": 1,
    "alembic/versions/core/core_009_memory_catalog.py": 1,
    "alembic/versions/core/core_010_insight_tables.py": 1,
    "alembic/versions/core/core_012_temporal_intelligence.py": 1,
    "alembic/versions/core/core_013_event_chains.py": 1,
    "alembic/versions/core/core_044_event_chains_status.py": 1,
    "alembic/versions/core/core_048_round_robin_counters.py": 1,
    "alembic/versions/core/core_050_schedule_token_budget.py": 1,
    "alembic/versions/core/core_051_qa_patrols.py": 1,
    "alembic/versions/core/core_052_qa_findings.py": 1,
    "alembic/versions/core/core_053_qa_dismissals.py": 1,
    "alembic/versions/core/core_055_v_qa_recent_failures.py": 2,
    "alembic/versions/core/core_056_tenant_id_defaults.py": 1,
    "alembic/versions/core/core_057_qa_allowed_repositories.py": 1,
    "alembic/versions/core/core_060_qa_repo_config.py": 1,
    "alembic/versions/core/core_063_v_briefing_contributions.py": 1,
    "alembic/versions/core/core_065_public_schema_write_grants.py": 1,
    "alembic/versions/core/core_066_healing_workflow_schema.py": 1,
    "alembic/versions/core/core_071_v_qa_recent_failures_trigger_source.py": 2,
    "alembic/versions/core/core_088_backfill_route_session_ingestion_event_id.py": 1,
    "alembic/versions/core/core_089_butler_logs.py": 1,
    "alembic/versions/core/core_091_qa_investigation_events.py": 1,
    "alembic/versions/core/core_092_audit_log.py": 1,
    "alembic/versions/core/core_094_spend_tables.py": 1,
    "alembic/versions/core/core_095_permissions_webhooks_approvals_policy.py": 1,
    "alembic/versions/core/core_096_memory_retention_policies.py": 1,
    "alembic/versions/core/core_098_butler_prompt_history.py": 1,
    "alembic/versions/core/core_099_dispatch_failures.py": 1,
    "alembic/versions/core/core_100_webhooks_secret_encrypted.py": 1,
    "alembic/versions/core/core_101_priority_contacts.py": 1,
    "alembic/versions/core/core_102_channel_defaults.py": 1,
    "alembic/versions/core/core_104_model_dispatch_attempts.py": 1,
    "alembic/versions/core/core_105_secrets_be1.py": 1,
    "alembic/versions/core/core_107_provider_feature_catalogue.py": 1,
    "alembic/versions/core/core_110_contact_info_write_block.py": 1,
    "alembic/versions/core/core_113_timeline_saved_views.py": 1,
    "alembic/versions/core/core_119_consolidation_runs.py": 1,
    "alembic/versions/core/core_120_dismissed_issues.py": 1,
    "alembic/versions/core/core_121_seed_permissions_vocabulary.py": 1,
    "alembic/versions/core/core_125_v_qa_tool_call_failures.py": 2,
    "alembic/versions/core/core_129_priority_contacts_drop_butler.py": 1,
    "alembic/versions/core/core_137_owner_scheduling_preferences.py": 1,
    "alembic/versions/core/core_144_calendar_dedup_overrides.py": 1,
    "alembic/versions/core/core_146_webhooks_secret_prefix.py": 1,
    "alembic/versions/core/core_149_webhooks_production_delivery.py": 1,
    "alembic/versions/core/core_160_attention_ledger.py": 1,
    "alembic/versions/core/core_162_delegation_ledger.py": 1,
    "alembic/versions/core/core_163_deployments_ledger.py": 1,
    "alembic/versions/core/core_164_breaker_resets.py": 1,
    "alembic/versions/core/core_165_attention_daily_rollup.py": 1,
    "alembic/versions/core/core_181_delegation_wake.py": 1,
    "alembic/versions/core/core_182_infra_conditions.py": 1,
    "alembic/versions/core/core_184_owner_conditions.py": 1,
    "alembic/versions/core/core_186_domain_events.py": 1,
    "alembic/versions/core/core_188_atmosphere_context.py": 1,
    "alembic/versions/core/core_191_flight_status_feed.py": 1,
    "alembic/versions/core/core_193_dashboard_turn_cancellation.py": 1,
    "alembic/versions/core/core_198_runtime_attention_outbox.py": 1,
    "alembic/versions/core/core_200_butler_reachability_conditions.py": 1,
    "alembic/versions/core/core_201_runtime_probe_control_receipts.py": 1,
    "alembic/versions/core/core_206_domain_event_contracts_and_reactions.py": 1,
    "alembic/versions/core/core_207_grant_chronicler_calendar_read_surface.py": 1,
    "alembic/versions/core/core_210_expected_signals.py": 1,
    "alembic/versions/core/core_215_entity_graph_edges.py": 1,
    "alembic/versions/core/core_217_fleet_case_file.py": 1,
    "alembic/versions/core/core_220_sessions_friction.py": 1,
    "alembic/versions/core/core_229_task_continuity_ledger.py": 1,
    "alembic/versions/core/core_239_cost_claims.py": 1,
    "alembic/versions/core/core_241_insight_feedback.py": 1,
    "alembic/versions/core/core_243_entity_rebind_log.py": 1,
    "alembic/versions/core/core_246_ha_person_mapping_receipts.py": 1,
    "alembic/versions/core/core_255_insight_premise_binding.py": 1,
    "alembic/versions/core/core_257_provider_allowance_states.py": 1,
    "alembic/versions/core/core_266_qa_publication_operations.py": 1,
    "alembic/versions/core/core_275_capability_catalog_publication.py": 1,
    "alembic/versions/core/core_293_endpoint_custody_holds.py": 1,
    "alembic/versions/core/core_294_qa_publication_exports.py": 1,
    "alembic/versions/core/core_295_custody_hold_hardening.py": 1,
    "alembic/versions/core/core_296_custody_phone_hold_transitive.py": 1,
    "alembic/versions/core/core_302_qa_publication_refusals.py": 1,
    "alembic/versions/core/core_303_owner_places.py": 1,
    "alembic/versions/core/core_315_memory_projection_invalidation.py": 1,
    "roster/chronicler/migrations/003_restrict_chronicler_grants.py": 1,
    "roster/chronicler/migrations/026_grant_calendar_read_surface.py": 1,
    "roster/concierge/migrations/001_fleet_views.py": 1,
    "roster/switchboard/migrations/024_v_qa_infra_state.py": 1,
    "roster/switchboard/migrations/035_registry_control_plane.py": 1,
    "roster/switchboard/migrations/050_admission_attempt_bindings.py": 1,
}
_FROZEN_TOTAL = 97


def _is_runtime_role(value: object) -> bool:
    return isinstance(value, str) and (value == CONNECTOR_ROLE or bool(_ROLE.match(value)))


def _constants(node: ast.Tuple | ast.List | ast.Set) -> set[object]:
    return {e.value for e in node.elts if isinstance(e, ast.Constant)}


def fleet_list_sites(
    source: str, schemas: frozenset[str], threshold: int = _FLEET_THRESHOLD
) -> list[int]:
    """Return the line of each literal fleet list in one revision's source."""
    lines = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            values = _constants(node)
            roles = {v for v in values if _is_runtime_role(v)}
            named_schemas = values & schemas
            if len(roles) >= threshold or len(named_schemas) >= threshold:
                lines.append(node.lineno)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if len(set(_ROLE_IN_TEXT.findall(node.value))) >= threshold:
                lines.append(node.lineno)
    return sorted(lines)


def _current_sites() -> Counter[str]:
    schemas = frozenset(butler_schemas())
    found: Counter[str] = Counter()
    for pattern in _REVISION_GLOBS:
        for path in sorted(_REPO.glob(pattern)):
            count = len(fleet_list_sites(path.read_text(encoding="utf-8"), schemas))
            if count:
                found[path.relative_to(_REPO).as_posix()] = count
    return found


def test_no_new_literal_fleet_role_lists() -> None:
    grown = {
        path: count for path, count in _current_sites().items() if count > _BASELINE.get(path, 0)
    }
    assert not grown, (
        "Revisions spell out the runtime-role fleet by hand; grant through "
        "butlers.db_authority.roles.butler_roles()/runtime_roles() instead: "
        f"{grown}"
    )


def test_baseline_only_shrinks() -> None:
    current = _current_sites()
    stale = {path: n for path, n in _BASELINE.items() if current.get(path, 0) < n}
    assert not stale, f"Lower or delete these _BASELINE entries to keep the ratchet tight: {stale}"
    assert sum(_BASELINE.values()) <= _FROZEN_TOTAL, "_BASELINE grew past its frozen total"


def test_detector_counts_every_fleet_spelling_but_not_narrow_lists() -> None:
    schemas = frozenset(butler_schemas())
    seven_roles = butler_roles()[:7]
    seven_schemas = tuple(sorted(schemas))[:7]
    source = "\n".join(
        [
            f"A = {seven_roles!r}",
            f"B = {('butler_travel_rw', 'butler_chronicler_rw')!r}",
            f"C = {('leak', 'smoke', 'co', 'gas', 'a', 'b', 'c', 'd')!r}",
            f"D = {list(seven_schemas)!r}",
            f"E = {seven_schemas[:6]!r}",
            f"F = {'GRANT SELECT ON t TO ' + ', '.join(seven_roles)!r}",
            f"G = {'GRANT SELECT ON t TO ' + ', '.join(seven_roles[:6])!r}",
        ]
    )
    assert fleet_list_sites(source, schemas) == [1, 4, 6]
