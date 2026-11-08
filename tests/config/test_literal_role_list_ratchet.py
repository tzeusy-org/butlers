"""Ratchet: no new literal fleet-wide role lists in migration revisions.

A revision that spells out "every butler role" by hand freezes the fleet at the day it
was written; every such list written before the concierge existed omits
``butler_concierge_rw``. New revisions grant through
``butlers.db_authority.roles.butler_roles()`` / ``runtime_roles()`` instead.

A *fleet list* is a literal tuple, list or set naming at least half as many runtime
roles (``butler_*_rw`` or ``connector_writer``) as the roster has butlers. Narrow,
deliberate reader lists (one or two named butlers) stay literal: they are exceptions
the grant manifest will carry as explicit rows, not "everyone".

``_BASELINE`` freezes the existing sites per file. It may only shrink: a new site fails,
and a removed site fails until its baseline entry is lowered or deleted, so the
ratchet cannot silently loosen. Source-only: parses Python, never imports revisions.
"""

from __future__ import annotations

import ast
import re
from collections import Counter
from pathlib import Path

import pytest

from butlers.db_authority.roles import CONNECTOR_ROLE, butler_roles

pytestmark = pytest.mark.unit

_REPO = Path(__file__).resolve().parents[2]
_REVISION_GLOBS = (
    "alembic/versions/**/*.py",
    "roster/*/migrations/*.py",
    "src/butlers/modules/*/migrations/*.py",
)
_ROLE = re.compile(r"^butler_[a-z0-9_]+_rw$")

# Existing literal fleet lists, frozen 2026-11-08 at main 5debfdd351 (bu-dss7he.6 S1).
# Lower or delete an entry when its file stops spelling the fleet out; never raise one.
_BASELINE: dict[str, int] = {
    "alembic/versions/core/core_002_identity.py": 1,
    "alembic/versions/core/core_004_model_and_tokens.py": 1,
    "alembic/versions/core/core_005_self_healing.py": 1,
    "alembic/versions/core/core_006_dashboard.py": 1,
    "alembic/versions/core/core_007_connectors.py": 1,
    "alembic/versions/core/core_008_external_accounts.py": 1,
    "alembic/versions/core/core_009_memory_catalog.py": 1,
    "alembic/versions/core/core_010_insight_tables.py": 1,
    "alembic/versions/core/core_048_round_robin_counters.py": 1,
    "alembic/versions/core/core_051_qa_patrols.py": 1,
    "alembic/versions/core/core_052_qa_findings.py": 1,
    "alembic/versions/core/core_053_qa_dismissals.py": 1,
    "alembic/versions/core/core_055_v_qa_recent_failures.py": 1,
    "alembic/versions/core/core_057_qa_allowed_repositories.py": 1,
    "alembic/versions/core/core_060_qa_repo_config.py": 1,
    "alembic/versions/core/core_066_healing_workflow_schema.py": 1,
    "alembic/versions/core/core_071_v_qa_recent_failures_trigger_source.py": 1,
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
    "alembic/versions/core/core_113_timeline_saved_views.py": 1,
    "alembic/versions/core/core_119_consolidation_runs.py": 1,
    "alembic/versions/core/core_120_dismissed_issues.py": 1,
    "alembic/versions/core/core_125_v_qa_tool_call_failures.py": 1,
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
    "alembic/versions/core/core_200_butler_reachability_conditions.py": 1,
    "alembic/versions/core/core_201_runtime_probe_control_receipts.py": 1,
    "alembic/versions/core/core_206_domain_event_contracts_and_reactions.py": 1,
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
    "alembic/versions/core/core_294_qa_publication_exports.py": 1,
    "alembic/versions/core/core_302_qa_publication_refusals.py": 1,
    "alembic/versions/core/core_303_owner_places.py": 1,
    "alembic/versions/core/core_315_memory_projection_invalidation.py": 1,
    "roster/switchboard/migrations/024_v_qa_infra_state.py": 1,
    "roster/switchboard/migrations/035_registry_control_plane.py": 1,
}


def _is_runtime_role(value: object) -> bool:
    return isinstance(value, str) and (value == CONNECTOR_ROLE or bool(_ROLE.match(value)))


def fleet_list_sites(source: str, threshold: int) -> list[int]:
    """Return the line of each literal collection naming ``threshold``+ runtime roles."""
    lines = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            named = {
                e.value
                for e in node.elts
                if isinstance(e, ast.Constant) and _is_runtime_role(e.value)
            }
            if len(named) >= threshold:
                lines.append(node.lineno)
    return lines


def _threshold() -> int:
    return (len(butler_roles()) + 1) // 2


def _current_sites() -> Counter[str]:
    found: Counter[str] = Counter()
    for pattern in _REVISION_GLOBS:
        for path in sorted(_REPO.glob(pattern)):
            count = len(fleet_list_sites(path.read_text(encoding="utf-8"), _threshold()))
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


def test_detector_counts_fleet_lists_not_narrow_reader_lists() -> None:
    fleet = repr(butler_roles()[: _threshold()])
    narrow = repr(("butler_travel_rw", "butler_chronicler_rw"))
    unrelated = repr(("leak", "smoke", "co", "gas", "a", "b", "c", "d"))
    source = f"A = {fleet}\nB = {narrow}\nC = {unrelated}\nD = [{fleet[1:-1]}]\n"
    assert fleet_list_sites(source, _threshold()) == [1, 4]
