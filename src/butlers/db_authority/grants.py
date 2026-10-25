"""Source-owned database object/operation authority.

Catalog discovery checks this manifest; it never adds a grant. Roster discovery
expands identities only. New managed objects must acquire an explicit source row
before a migrator can finalize them.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .roles import AuthorityError, RuntimeProfile, runtime_profiles


class ObjectKind(StrEnum):
    TABLE = "table"
    VIEW = "view"
    SEQUENCE = "sequence"
    FUNCTION = "function"


@dataclass(frozen=True, order=True)
class Permission:
    role: str
    verbs: tuple[str, ...]
    columns: tuple[str, ...] = ()


@dataclass(frozen=True)
class ObjectAuthority:
    schema: str
    name: str
    kind: ObjectKind
    owner: str
    permissions: tuple[Permission, ...]
    producer: str
    forced_rls: bool = False
    signature: str = ""
    search_path: tuple[str, ...] = ()
    required: bool = True

    @property
    def identity(self) -> tuple[str, str, str, str]:
        return self.schema, self.name, self.kind.value, self.signature


_READ = ("SELECT",)
_WRITE = ("SELECT", "INSERT", "UPDATE")
_CRUD = (*_WRITE, "DELETE")
_APPEND = ("SELECT", "INSERT")

# Exact public producers. An empty permission list deliberately retains an
# administrative/history object without giving it to a serving identity.
_PUBLIC_ROWS = (
    ("ingestion_events", "ingestion", "Switchboard canonical ingest and connector replay"),
    ("model_catalog", "models", "runtime model resolution; authenticated model settings"),
    ("butler_model_overrides", "models", "runtime model resolution; authenticated model settings"),
    ("provider_config", "models", "runtime model resolution; authenticated provider settings"),
    ("token_limits", "models", "runtime allowance reads; authenticated token settings"),
    ("model_round_robin_counters", "runtime_write", "model dispatch round-robin reservation"),
    ("model_dispatch_attempts", "runtime_append", "same-attempt model dispatch evidence"),
    ("token_usage_ledger", "runtime_append", "model usage recorder and allowance reads"),
    ("audit_log", "runtime_append", "role-bound append-only audit producers, core279"),
    (
        "insight_candidates",
        "insight_candidates",
        "registered owning Insight broker and proposal producers",
    ),
    ("insight_cooldowns", "insight", "owning Insight broker cooldown lifecycle"),
    ("insight_engagement", "insight", "owning Insight engagement lifecycle"),
    ("insight_settings", "insight", "owning Insight settings and authenticated dashboard"),
    ("insight_amendments", "insight", "owning Insight amendment CAS and final delivery"),
    ("insight_feedback", "insight_feedback", "owner feedback bridge and Switchboard retention"),
    ("user_context", "context", "typed non-DND producers; canonical DND private writer"),
    ("entities", "identity", "owner bootstrap and identity registry producers"),
    ("entity_info", "identity", "bound entity credentials and registry producers"),
    ("google_accounts", "accounts", "Google account registry"),
    ("steam_accounts", "accounts", "Steam account registry"),
    ("entity_graph_edges", "identity", "identity graph canonical merge"),
    ("entity_rebind_log", "runtime_append", "same-transaction entity rebind receipt"),
    ("memory_catalog", "runtime_write", "memory owning producer and cross-butler discovery"),
    ("expected_signals", "signals", "producer_role-fenced expected-signal registration"),
    ("qa_patrols", "qa", "QA patrol producer and authenticated synthetic patrol"),
    ("qa_findings", "qa", "QA finding producer"),
    ("qa_dismissals", "qa", "QA dismissal producer"),
    ("qa_repo_config", "qa", "QA repository configuration producer"),
    ("qa_investigation_events", "qa_append", "QA investigation event history"),
    ("qa_allowed_repositories", "dashboard", "authenticated dashboard repository allowlist"),
    ("breaker_resets", "dashboard_append", "authenticated circuit-breaker reset history"),
    ("qa_publication_operations", "qa", "QA publication operation state machine and forced RLS"),
    ("butler_secrets", "credentials", "shared credential authority and fenced rotation CAS"),
    ("secret_probe_log", "credential_append", "credential health probe history"),
    ("spend_rules", "models", "model budget reads and authenticated spend settings"),
    ("spend_ceiling", "models", "global model allowance reads and authenticated settings"),
    ("deployments", "operator", "explicit operator deployment ledger"),
    ("state", "dashboard", "shared authenticated system settings"),
    ("approvals_policy", "policy", "quiet-hours policy reads and authenticated owner changes"),
    ("permissions", "dashboard", "authenticated standing permission administration"),
    ("channel_defaults", "dashboard", "authenticated delivery defaults"),
    ("webhooks", "dashboard", "authenticated webhook administration"),
    ("priority_contacts", "policy", "priority identity reads and authenticated settings"),
    ("provider_feature_catalogue", "runtime_write", "server-owned provider catalogue bootstrap"),
    ("system_prompt_history", "runtime_append", "server-owned system prompt history"),
    ("butler_tools", "runtime_write", "registered tool metadata publication"),
    ("domain_events", "runtime_append", "canonical domain-event ingestion"),
    ("domain_event_deliveries", "runtime_write", "owning domain-event delivery claim and ACK"),
    ("domain_event_contracts", "runtime_write", "registered domain-event contract publication"),
    ("domain_event_reactions", "runtime_write", "registered domain-event reaction publication"),
    ("butler_subscriptions", "runtime_write", "domain-event subscription publication"),
    ("dashboard_conversations", "dashboard", "authenticated dashboard conversation lifecycle"),
    ("dashboard_messages", "dashboard", "authenticated dashboard messages"),
    ("dashboard_conversation_turns", "dashboard", "authenticated dashboard turn lifecycle"),
    ("dashboard_conversation_turn_sessions", "runtime_write", "runtime turn/session binding"),
    ("timeline_saved_views", "dashboard", "authenticated saved timeline views"),
    ("task_continuity", "runtime_write", "bound runtime task continuity ledger"),
    ("dispatch_failures", "runtime_append", "runtime dispatch failure evidence"),
    ("delegation_ledger", "runtime_write", "registered delegation lifecycle"),
    ("delegation_wake_attempts", "runtime_write", "delegation wake claim and result"),
    ("attention_ledger", "runtime_write", "owning delivery attention ledger"),
    ("attention_daily_rollup", "runtime_write", "attention daily summarizer"),
    ("butler_reachability_conditions", "runtime_write", "receiver-observed reachability"),
    ("infra_conditions", "runtime_write", "infra condition state producer"),
    ("owner_conditions", "runtime_write", "verified owner condition producer"),
    ("cost_claims", "runtime_write", "role-fenced cost claim reservation"),
    ("cost_claim_events", "runtime_append", "cost claim event history"),
    ("cost_claim_resolutions", "finance", "authenticated cost-claim resolution"),
    ("capability_catalog_control", "runtime_write", "signed capability catalogue publication"),
    ("capability_catalog_current", "runtime_write", "current committed capability catalogue"),
    ("capability_catalog_history", "runtime_append", "committed capability catalogue history"),
    ("capability_catalog_installations", "runtime_write", "registered runtime installation"),
    ("capability_catalog_members", "runtime_write", "registered runtime catalogue members"),
    ("atmosphere_readings", "runtime_write", "atmosphere connector readings"),
    ("atmosphere_feed_status", "runtime_write", "atmosphere connector availability"),
    ("flight_status_feed_status", "runtime_write", "flight-status connector availability"),
    ("fleet_cases", "runtime_write", "fleet case state producer"),
    ("fleet_case_evidence", "runtime_append", "fleet case evidence producer"),
    ("fleet_case_links", "runtime_write", "fleet case link producer"),
    ("consolidation_runs", "runtime_append", "memory consolidation run history"),
    ("memory_retention_policies", "policy", "memory retention settings"),
    ("memory_compaction_log", "runtime_append", "memory compaction history"),
    ("dismissed_issues", "qa", "QA dismissed issue lineage"),
    ("healing_attempts", "history", "retained retired healing history, no execution restoration"),
    ("healing_attempt_sessions", "history", "retained retired healing history"),
    ("healing_dispatch_events", "history", "retained retired healing history"),
    ("contacts_dropbak", "history", "bounded predecessor rollback snapshot"),
    ("priority_contacts_dedup_bak_core_133", "history", "bounded predecessor rollback snapshot"),
    ("core_265_anchor_backup", "history", "conversation identity rollback snapshot"),
    ("core_265_message_backup", "history", "conversation identity rollback snapshot"),
    ("core_265_turn_backup", "history", "conversation identity rollback snapshot"),
)


def public_objects(
    database: str, migration_owner: str, *, profiles: tuple[RuntimeProfile, ...] | None = None
) -> tuple[ObjectAuthority, ...]:
    """Expand declared producer rows, never catalog-discovered names or grants."""
    profiles = runtime_profiles(database) if profiles is None else profiles
    runtimes = tuple(profile.role for profile in profiles)
    dashboard = "dashboard_api"
    connector = "connector_writer"
    credential = "credential_store_writer"
    permissions = {
        "runtime_write": tuple(Permission(role, _WRITE) for role in runtimes)
        + (Permission(dashboard, _READ),),
        "runtime_append": tuple(Permission(role, _APPEND) for role in runtimes)
        + (Permission(dashboard, _READ),),
        "models": tuple(Permission(role, _READ) for role in runtimes)
        + (Permission(dashboard, _CRUD),),
        "ingestion": (Permission("butler_switchboard_rw", _WRITE), Permission(connector, _WRITE)),
        "context": tuple(Permission(role, _WRITE) for role in runtimes)
        + (Permission(connector, _WRITE), Permission(dashboard, _READ)),
        "identity": tuple(Permission(role, _WRITE) for role in runtimes)
        + (Permission(connector, _WRITE), Permission(dashboard, _WRITE)),
        "accounts": (Permission(connector, _WRITE), Permission(dashboard, _CRUD)),
        "signals": tuple(Permission(role, _WRITE) for role in runtimes)
        + (Permission(connector, _WRITE), Permission(dashboard, _READ)),
        "qa": (Permission("butler_qa_rw", _WRITE), Permission(dashboard, _WRITE)),
        "qa_append": (Permission("butler_qa_rw", _APPEND), Permission(dashboard, _READ)),
        "dashboard": (Permission(dashboard, _WRITE),),
        "dashboard_append": (Permission(dashboard, _APPEND),),
        "finance": tuple(
            Permission(role, _READ) for role in runtimes if role != "butler_finance_rw"
        )
        + (Permission("butler_finance_rw", _WRITE), Permission(dashboard, _READ)),
        "insight": (Permission("butler_switchboard_rw", _CRUD), Permission(dashboard, _WRITE)),
        "insight_candidates": tuple(
            Permission(role, ("INSERT",)) for role in runtimes if role != "butler_switchboard_rw"
        )
        + (Permission("butler_switchboard_rw", _CRUD), Permission(dashboard, _WRITE)),
        "insight_feedback": (
            Permission("butler_switchboard_rw", _APPEND),
            Permission(dashboard, _APPEND),
        ),
        "policy": tuple(Permission(role, _READ) for role in runtimes)
        + (Permission(dashboard, _WRITE),),
        "credentials": tuple(Permission(role, _READ) for role in runtimes)
        + (
            Permission(connector, _READ),
            Permission(credential, _CRUD),
            Permission(dashboard, _CRUD),
        ),
        "credential_append": (Permission(credential, _APPEND), Permission(dashboard, _READ)),
        "operator": (),
        "history": (Permission(dashboard, _READ), Permission("butler_qa_rw", _READ)),
    }
    forced = {"user_context", "expected_signals", "qa_publication_operations", "cost_claims"}
    result = tuple(
        ObjectAuthority(
            "public",
            name,
            ObjectKind.TABLE,
            "dnd_generation_owner" if name == "user_context" else migration_owner,
            permissions[policy],
            producer,
            name in forced,
            required=policy != "history",
        )
        for name, policy, producer in _PUBLIC_ROWS
    )
    if len({row.identity for row in result}) != len(result):
        raise AuthorityError("Database object authority is ambiguous")
    return result


# Exact local owners. These names are reviewed source declarations, not catalog
# enumeration. Module rows are included only for modules in the fixed roster.
_CORE_TABLES = (
    "alembic_version",
    "state",
    "scheduled_tasks",
    "sessions",
    "route_inbox",
    "butler_logs",
    "butler_secrets",
    "session_process_logs",
    "corrections",
    "runtime_config",
    "sessions_friction",
    "delivery_preferences",
    "deferred_notifications",
    "metric_baselines",
    "metric_deviation_episodes",
    "seasonal_periods",
    "calendar_sources",
    "calendar_events",
    "calendar_event_instances",
    "calendar_event_entities",
    "calendar_sync_cursors",
    "calendar_action_log",
)
_DOMAIN_TABLES = {
    "relationship": (
        "activity_feed",
        "addresses",
        "contact_entity_map",
        "contact_labels",
        "gifts",
        "group_labels",
        "group_members",
        "groups",
        "important_dates",
        "interactions",
        "labels",
        "life_event_categories",
        "life_event_types",
        "life_events",
        "loans",
        "notes",
        "quick_facts",
        "relationship_types",
        "relationships",
        "tasks",
        "credentials",
        "entity_facts",
        "entity_predicate_registry",
        "entity_view_marks",
        "fact_approval_context",
        "fact_approval_rule_context",
        "fact_coverage",
        "fact_evidence",
        "fact_identity_decisions",
        "meeting_debriefs",
        "merge_reviews",
    ),
    "home": (
        "ha_command_log",
        "ha_entity_snapshot",
        "ha_source_health",
        "maintenance_items",
        "safety_watch",
    ),
    "general": (
        "collection_vocabulary",
        "collection_vocabulary_keys",
        "collections",
        "entities",
        "source_versions",
    ),
    "education": (
        "analytics_snapshots",
        "curriculum_requests",
        "mind_map_edges",
        "mind_map_nodes",
        "mind_maps",
        "quiz_responses",
    ),
    "travel": (
        "accommodations",
        "airport_minimum_connect",
        "booking_records",
        "connections",
        "documents",
        "leg_passengers",
        "legs",
        "reservations",
        "travellers",
        "trips",
    ),
    "lifestyle": ("taste_signals", "verdicts", "works"),
    "health": (
        "conditions",
        "meals",
        "medication_doses",
        "medications",
        "research",
        "symptoms",
        "visit_briefs",
    ),
    "switchboard": (
        "attachment_refs",
        "backfill_jobs",
        "butler_registry",
        "butler_registry_eligibility_log",
        "connector_heartbeat_log",
        "connector_registry",
        "dashboard_audit_log",
        "dead_letter_queue",
        "extraction_log",
        "extraction_queue",
        "ingestion_rules",
        "media_refs",
        "message_inbox",
        "notifications",
        "operator_audit_log",
        "routing_instructions",
        "routing_log",
        "routing_verdict_log",
        "rule_promotion_suggestions",
        "thread_affinity_settings",
        "admission_attempt_bindings",
        "admission_decisions",
        "budgets",
        "butler_boot_registrations",
        "butler_registry_control_plane",
        "effects",
        "goal_inbox",
        "goals",
        "invocations",
    ),
    "messenger": ("approval_delivery_handoffs",),
    "finance": (
        "accounts",
        "balance_snapshots",
        "bills",
        "budgets",
        "categories",
        "claim_match_bindings",
        "merchant_mappings",
        "obligation_ledger",
        "payment_destinations",
        "recurring_groups",
        "subscriptions",
        "transaction_corrections",
        "transactions",
    ),
    "chronicler": (
        "covered_local_days",
        "daily_rollup_flags",
        "daily_rollups",
        "day_close_cache_locks",
        "episode_entities",
        "episode_event_links",
        "episodes",
        "idempotency_keys",
        "overrides",
        "point_events",
        "projection_checkpoints",
        "routines",
        "source_adapter_state",
        "tier2_cache",
    ),
}
_MODULE_TABLES = {
    "whatsapp": ("whatsapp_sessions",),
    "contacts": ("contacts_source_accounts", "contacts_source_links", "contacts_sync_state"),
    "google_drive": ("google_drive_butler_folders",),
    "memory": (
        "embedding_versions",
        "episode_tombstones",
        "episodes",
        "facts",
        "knowledge_gap_origins",
        "knowledge_gaps",
        "memory_events",
        "memory_links",
        "memory_policies",
        "predicate_registry",
        "rule_applications",
        "rules",
    ),
    "approvals": (
        "approval_delivery_attempts",
        "approval_delivery_cohort_members",
        "approval_delivery_cohorts",
        "approval_delivery_intents",
        "approval_delivery_presentations",
        "approval_delivery_rollout",
        "approval_events",
        "approval_push_emissions",
        "approval_rules",
        "autonomy_approval_history",
        "autonomy_suggestions",
        "pending_actions",
    ),
}
_DOMAIN_VIEWS = {
    "chronicler": ("v_episodes_corrected", "v_latest_overrides", "v_point_events_corrected"),
    "travel": ("booking_fragmentation_inventory",),
    "concierge": ("v_fleet_sessions", "v_fleet_spend"),
    "general": ("v_briefing_contributions",),
}


def local_objects(
    database: str, migration_owner: str, *, profiles: tuple[RuntimeProfile, ...] | None = None
) -> tuple[ObjectAuthority, ...]:
    """Compile only fixed source-owning domains/modules and released read exceptions."""
    profiles = runtime_profiles(database) if profiles is None else profiles
    result = []
    for profile in profiles:
        schema, runtime = profile.primary_schema, profile.role
        tables = set(_CORE_TABLES) | set(_DOMAIN_TABLES.get(schema, ()))
        for module in profile.modules:
            if module != "memory" or not profile.private_schemas:
                tables.update(_MODULE_TABLES.get(module, ()))
        for name in sorted(tables):
            # Schema version/ACL ownership never becomes runtime write authority.
            permissions = (
                (Permission(runtime, _READ), Permission("dashboard_api", _READ))
                if name == "alembic_version"
                else (Permission(runtime, _CRUD), Permission("dashboard_api", _CRUD))
            )
            if schema == "switchboard" and name == "message_inbox":
                permissions += tuple(
                    Permission(f"butler_{reader}_rw", _READ)
                    for reader in ("relationship", "messenger", "travel")
                )
            result.append(
                ObjectAuthority(
                    schema,
                    name,
                    ObjectKind.TABLE,
                    migration_owner,
                    permissions,
                    "owning runtime selected schema; core/domain/module source producer",
                )
            )
        for private in profile.private_schemas:
            for name in ("alembic_version", *_MODULE_TABLES["memory"]):
                permissions = (
                    Permission(runtime, _READ if name == "alembic_version" else _CRUD),
                    Permission("dashboard_api", _READ if name == "alembic_version" else _CRUD),
                )
                result.append(
                    ObjectAuthority(
                        private,
                        name,
                        ObjectKind.TABLE,
                        migration_owner,
                        permissions,
                        "held primary Memory producer; separate declared private search_path",
                    )
                )
        for name in _DOMAIN_VIEWS.get(schema, ()):
            permissions = (Permission(runtime, _READ), Permission("dashboard_api", _READ))
            if schema == "general":
                permissions += (Permission("butler_switchboard_rw", _READ),)
            result.append(
                ObjectAuthority(
                    schema,
                    name,
                    ObjectKind.VIEW,
                    migration_owner,
                    permissions,
                    "RFC0010/RFC0030 or owning domain filtered view",
                )
            )
    return tuple(result)
