"""Declarative manifest of runtime-role privileges on every ``public`` relation.

One row per ``public`` relation says which table privileges each runtime role
(:func:`butlers.db_authority.roles.runtime_roles`) holds. It is the inventory the
deny-by-default model starts from (bu-dss7he.6 slice S2): today it records the
effective grants the deployed database already has, so checking it changes nothing.
Narrowing a relation is a later, reviewed edit to its row.

Most relations still carry :data:`LEGACY_FLEET_DML` -- the blanket
``SELECT, INSERT, UPDATE, DELETE`` that ``scripts/init-db.sql`` grants every runtime
role on every ``public`` table. That row is an unreviewed inheritance, not an approval:
it lists the relations still waiting for a per-table decision. The explicit rows are
boundaries a migration or the bootstrap installed on purpose (DND single-writer and
generation fencing, the runtime-attention outbox, cost-claim and consolidation write
narrowing); deny-by-default must keep them exactly, so they are spelled out here
rather than derived.

Partitions are checked against their parent's row; extension-owned relations
(PostGIS, ``pg_stat_statements``) are outside the manifest. Row-level security is
not modelled here: a grant row is necessary, not sufficient, where a FORCE RLS
policy is the fence.

``butlers.db_authority.catalog`` reads the effective privileges from a database and
diffs them against :func:`expected_privileges`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from butlers.db_authority.roles import runtime_roles

TABLE_PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")

_DML = frozenset({"SELECT", "INSERT", "UPDATE", "DELETE"})
_NONE: frozenset[str] = frozenset()


@dataclass(frozen=True)
class RelationGrant:
    """Privileges every runtime role holds on one relation, with per-role overrides."""

    fleet: frozenset[str]
    by_role: Mapping[str, frozenset[str]] = field(default_factory=dict)
    reason: str = ""
    after_init_db_replay: RelationGrant | None = None
    """What replaying ``scripts/init-db.sql`` leaves instead, when it overrides this row."""

    def privileges(self, role: str, *, after_init_db_replay: bool = False) -> frozenset[str]:
        if after_init_db_replay and self.after_init_db_replay is not None:
            return self.after_init_db_replay.privileges(role)
        return self.by_role.get(role, self.fleet)


LEGACY_FLEET_DML = RelationGrant(_DML, reason="init-db blanket public DML; not yet reviewed")
"""Inherited blanket grant. A relation leaves this row when its narrowing is decided."""

# Relations created outside Alembic by the WhatsApp bridge (whatsmeow) at runtime.
WA_BRIDGE_RELATIONS = frozenset(
    {
        "whatsmeow_app_state_mutation_macs",
        "whatsmeow_app_state_sync_keys",
        "whatsmeow_app_state_version",
        "whatsmeow_chat_settings",
        "whatsmeow_contacts",
        "whatsmeow_device",
        "whatsmeow_event_buffer",
        "whatsmeow_identity_keys",
        "whatsmeow_lid_map",
        "whatsmeow_message_secrets",
        "whatsmeow_nct_salt",
        "whatsmeow_pre_keys",
        "whatsmeow_privacy_tokens",
        "whatsmeow_retry_buffer",
        "whatsmeow_sender_keys",
        "whatsmeow_sessions",
        "whatsmeow_version",
    }
)

_LEGACY_FLEET_DML_RELATIONS = (
    "approvals_policy",
    "atmosphere_feed_status",
    "atmosphere_readings",
    "attention_daily_rollup",
    "attention_ledger",
    "audit_log",
    "breaker_resets",
    "butler_model_overrides",
    "butler_reachability_conditions",
    "butler_secrets",
    "butler_subscriptions",
    "butler_tools",
    "calendar_dedup_overrides",
    "calendar_dedup_rules",
    "capability_catalog_control",
    "capability_catalog_current",
    "capability_catalog_history",
    "capability_catalog_installations",
    "capability_catalog_members",
    "capture_operations",
    "capture_service_control",
    "captures",
    "channel_defaults",
    "contacts_preferred_channel_dropbak_core_123",
    "core_265_anchor_backup",
    "core_265_message_backup",
    "core_265_turn_backup",
    "custody_holds",
    "dashboard_conversations",
    "dashboard_job_health",
    "dashboard_job_transitions",
    "dashboard_messages",
    "delegation_ledger",
    "delegation_wake_attempts",
    "deployments",
    "dismissed_issues",
    "dispatch_failures",
    "domain_event_contracts",
    "domain_event_deliveries",
    "domain_event_reactions",
    "domain_events",
    "entities",
    "entities_canonical_name_bak_core_130",
    "entity_graph_edges",
    "entity_info",
    "entity_rebind_log",
    "expected_signals",
    "fleet_case_evidence",
    "fleet_case_links",
    "fleet_cases",
    "flight_status_feed_status",
    "google_accounts",
    "healing_attempt_sessions",
    "healing_attempts",
    "healing_dispatch_events",
    "infra_conditions",
    "ingestion_events",
    "insight_amendments",
    "insight_candidates",
    "insight_cooldowns",
    "insight_engagement",
    "insight_settings",
    "memory_catalog",
    "memory_compaction_log",
    "memory_retention_policies",
    "model_catalog",
    "model_dispatch_attempts",
    "model_round_robin_counters",
    "model_served_usage",
    "owner_conditions",
    "owner_scheduling_preferences",
    "pending_confirms",
    "permissions",
    "priority_contacts",
    "priority_contacts_butler_dropbak_core_129",
    "provider_allowance_states",
    "provider_config",
    "provider_feature_catalogue",
    "qa_allowed_repositories",
    "qa_dismissals",
    "qa_findings",
    "qa_investigation_events",
    "qa_patrols",
    "qa_repo_config",
    "secret_probe_log",
    "spend_ceiling",
    "spend_rules",
    "steam_accounts",
    "system_prompt_history",
    "task_continuity",
    "timeline_saved_views",
    "token_limits",
    "token_usage_ledger",
    "v_qa_butler_heartbeat",
    "v_qa_butler_receiver_state",
    "v_qa_connector_state",
    "v_qa_recent_failures",
    "v_qa_tool_call_failures",
    "webhooks",
    *sorted(WA_BRIDGE_RELATIONS),
)

_SWITCHBOARD = "butler_switchboard_rw"
_OWNER_ONLY = "private to its NOLOGIN owner; reached only through definer interfaces"

_CONNECTOR = "connector_writer"
_CONCIERGE = "butler_concierge_rw"
_SELECT = frozenset({"SELECT"})

# core_239 removes DELETE from the fleet, but a fresh database leaves connector_writer DML;
# an init-db replay then narrows the connector to the fleet row.
_COST_CLAIM = RelationGrant(
    frozenset({"SELECT", "INSERT", "UPDATE"}),
    {_CONNECTOR: _DML},
    "core_239: no fleet DELETE, FORCE RLS",
    after_init_db_replay=RelationGrant(frozenset({"SELECT", "INSERT", "UPDATE"})),
)


def _replay_widened(
    fleet: frozenset[str], by_role: Mapping[str, frozenset[str]], reason: str
) -> RelationGrant:
    """A row a migration narrowed and an ``init-db.sql`` replay re-widens to blanket DML."""
    return RelationGrant(fleet, by_role, reason, after_init_db_replay=LEGACY_FLEET_DML)


_NO_CONNECTOR = {_CONNECTOR: _NONE}

# Rows where migrations narrow the fleet below init-db's blanket DML. They record the
# state a freshly migrated database holds; replaying init-db.sql re-widens every one to
# LEGACY_FLEET_DML (the bu-yfd9v1 class), which is why the apply step must converge.
_REPLAY_WIDENED: dict[str, RelationGrant] = {
    "ambient_projections": _replay_widened(_SELECT, {}, "migration: read-only to runtime roles"),
    "dashboard_conversation_turn_sessions": _replay_widened(
        _NONE, {_CONCIERGE: _DML}, "migration: dashboard conversation store, concierge only"
    ),
    "dashboard_conversation_turns": _replay_widened(
        _NONE, {_CONCIERGE: _DML}, "migration: dashboard conversation store, concierge only"
    ),
    "entity_graph_withheld_counts": _replay_widened(
        _SELECT, _NO_CONNECTOR, "migration: read-only; no connector access"
    ),
    "ha_person_mapping_receipts": _replay_widened(
        _NONE,
        {_CONCIERGE: _DML, _SWITCHBOARD: frozenset({"SELECT", "INSERT"})},
        "migration: receipts written by the Switchboard, concierge full",
    ),
    "insight_feedback": _replay_widened(_NONE, {_SWITCHBOARD: _DML}, "migration: Switchboard only"),
    **{
        name: _replay_widened(_DML, _NO_CONNECTOR, "migration: owner delivery; no connector access")
        for name in (
            "owner_delivery_presentations",
            "owner_delivery_receipts",
            "owner_delivery_rollout",
            "owner_delivery_source_map",
            "owner_delivery_subjects",
            "owner_delivery_tokens",
        )
    },
    "owner_places": _replay_widened(
        _NONE,
        {_CONCIERGE: _DML, "butler_chronicler_rw": _SELECT, "butler_travel_rw": _SELECT},
        "migration: concierge full; chronicler and travel read",
    ),
    "qa_publication_exports": _replay_widened(
        _SELECT,
        {"butler_qa_rw": frozenset({"SELECT", "INSERT"})},
        "migration: QA butler appends; others read",
    ),
    "qa_publication_operations": _replay_widened(
        _SELECT,
        {"butler_qa_rw": frozenset({"SELECT", "INSERT", "UPDATE"})},
        "migration: QA butler writes; others read",
    ),
    "qa_publication_refusals": _replay_widened(
        _SELECT,
        {"butler_qa_rw": frozenset({"SELECT", "INSERT"})},
        "migration: QA butler appends; others read",
    ),
    "runtime_attention_operator_control": _replay_widened(_NONE, {}, "migration: " + _OWNER_ONLY),
    "runtime_probe_control_receipts": _replay_widened(
        _NONE, {_CONCIERGE: _DML}, "migration: concierge only"
    ),
}

_BOUNDARIES: dict[str, RelationGrant] = {
    "break_through_clauses": RelationGrant(_NONE, reason=_OWNER_ONLY),
    "consolidation_runs": RelationGrant(
        frozenset({"SELECT", "INSERT"}),
        {"connector_writer": frozenset({"SELECT"})},
        reason="core_119: append-only run ledger; connectors only read",
    ),
    "cost_claim_events": _COST_CLAIM,
    "cost_claim_resolutions": _COST_CLAIM,
    "cost_claims": _COST_CLAIM,
    "dnd_generation_guard": RelationGrant(
        frozenset({"SELECT"}), reason="DND generation fence: read-only outside its owner"
    ),
    "dnd_generation_mutations": RelationGrant(_NONE, reason=_OWNER_ONLY),
    "runtime_attention_condition_control": RelationGrant(_NONE, reason=_OWNER_ONLY),
    "runtime_attention_condition_episodes": RelationGrant(_NONE, reason=_OWNER_ONLY),
    "runtime_attention_delivery_lease": RelationGrant(
        _NONE,
        {_SWITCHBOARD: frozenset({"SELECT", "INSERT", "UPDATE"})},
        reason="runtime-attention outbox: only the Switchboard deliverer leases",
    ),
    "runtime_attention_outbox": RelationGrant(
        _NONE,
        {_SWITCHBOARD: frozenset({"SELECT"})},
        reason="runtime-attention outbox: producers write through definer interfaces",
    ),
    "runtime_attention_producer_control": RelationGrant(_NONE, reason=_OWNER_ONLY),
    "state": RelationGrant(
        frozenset({"SELECT"}), reason="core_087 public state is read-only to runtime roles"
    ),
    "user_context": RelationGrant(
        frozenset({"SELECT", "INSERT", "UPDATE"}),
        reason="DND single writer: FORCE RLS policy is the write fence; no DELETE",
    ),
}


def _build() -> Mapping[str, RelationGrant]:
    rows: dict[str, RelationGrant] = {}
    for name in _LEGACY_FLEET_DML_RELATIONS:
        if name in rows:
            raise ValueError(f"duplicate manifest relation: {name}")
        rows[name] = LEGACY_FLEET_DML
    for name, grant in _BOUNDARIES.items():
        if name in rows:
            raise ValueError(f"relation is both legacy and a boundary: {name}")
        rows[name] = grant
    for name, grant in _REPLAY_WIDENED.items():
        if name in rows:
            raise ValueError(f"relation is both legacy and replay-widened: {name}")
        rows[name] = grant
    return MappingProxyType(dict(sorted(rows.items())))


PUBLIC_RELATIONS: Mapping[str, RelationGrant] = _build()

REPLAY_WIDENED = frozenset(
    name for name, grant in PUBLIC_RELATIONS.items() if grant.after_init_db_replay is not None
)
"""Relations whose fresh-database grants an ``init-db.sql`` replay changes."""


def expected_privileges(
    relation: str, roles: tuple[str, ...] | None = None, *, after_init_db_replay: bool = False
) -> dict[str, frozenset[str]]:
    """Return each runtime role's manifest privileges on one ``public`` relation.

    The default is the migration intent a freshly migrated database holds.
    ``after_init_db_replay`` returns what a replay of ``init-db.sql`` leaves, which differs
    on :data:`REPLAY_WIDENED` rows.
    """
    grant = PUBLIC_RELATIONS[relation]
    return {
        role: grant.privileges(role, after_init_db_replay=after_init_db_replay)
        for role in (roles or runtime_roles())
    }
