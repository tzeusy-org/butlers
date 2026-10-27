"""Source-owned database object/operation authority.

Catalog discovery checks this manifest; it never adds a grant. Roster discovery
expands identities only. New managed objects must acquire an explicit source row
before a migrator can finalize them.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache as _owner_lookup_template_cache

from .roles import AuthorityError, RuntimeProfile, runtime_profiles


class ObjectKind(StrEnum):
    TABLE = "table"
    VIEW = "view"
    MATERIALIZED_VIEW = "materialized_view"
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
    sequence_parent: tuple[str, str, str] | None = None

    @property
    def identity(self) -> tuple[str, str, str, str]:
        return self.schema, self.name, self.kind.value, self.signature


# Current, source-owned row-security postures. ENABLE without FORCE is an
# intentional ordinary-owner maintenance boundary, not an absent RLS policy.
# These are exact relation identities, never discovered catalog permissions.
_FORCED_ROW_SECURITY = frozenset(
    ("public", name)
    for name in (
        "user_context",
        "expected_signals",
        "qa_publication_operations",
        "cost_claims",
        "cost_claim_events",
        "cost_claim_resolutions",
        "task_continuity",
        "fleet_cases",
        "fleet_case_links",
        "runtime_probe_control_receipts",
        "runtime_attention_outbox",
        "runtime_attention_delivery_lease",
        "runtime_attention_condition_episodes",
    )
)
_ENABLED_ROW_SECURITY = (
    _FORCED_ROW_SECURITY
    | frozenset(
        ("public", name)
        for name in (
            "entity_rebind_log",
            "insight_feedback",
            "insight_amendments",
            "ha_person_mapping_receipts",
            "capability_catalog_installations",
            "capability_catalog_control",
            "capability_catalog_current",
            "capability_catalog_history",
            "capability_catalog_members",
        )
    )
    | frozenset({("connectors", "home_assistant_persons")})
    | frozenset(
        ("switchboard", name)
        for name in (
            "butler_registry_control_plane",
            "butler_boot_registrations",
            "goals",
            "invocations",
            "effects",
            "budgets",
            "admission_decisions",
            "goal_inbox",
            "admission_attempt_bindings",
        )
    )
)


def relation_security_flags(row: ObjectAuthority) -> tuple[bool, bool]:
    """The actual source contract binds ENABLE and FORCE independently."""
    forced = row.forced_rls or (row.schema, row.name) in _FORCED_ROW_SECURITY
    return forced or (row.schema, row.name) in _ENABLED_ROW_SECURITY, forced


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
    ("owner_scheduling_preferences", "policy", "owner scheduling policy and dashboard editor"),
    ("calendar_dedup_rules", "dashboard", "calendar workspace explicit deduplication rules"),
    (
        "calendar_dedup_overrides",
        "dashboard",
        "calendar workspace explicit deduplication overrides",
    ),
    (
        "runtime_probe_control_receipts",
        "switchboard_retention",
        "signed runtime control replay fence",
    ),
    ("ha_person_mapping_receipts", "switchboard_append", "signed Home person mapping replay fence"),
    ("provider_allowance_states", "runtime_write", "provider account allowance state producer"),
    ("contact_info_dropbak_core_115", "history", "retained core115 rollback snapshot"),
    (
        "contacts_preferred_channel_dropbak_core_123",
        "history",
        "retained core123 rollback snapshot",
    ),
    ("priority_contacts_butler_dropbak_core_129", "history", "retained core129 rollback snapshot"),
    ("entities_canonical_name_bak_core_130", "history", "retained core130 rollback snapshot"),
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
        "switchboard_append": (Permission("butler_switchboard_rw", _APPEND),),
        "switchboard_retention": (Permission("butler_switchboard_rw", (*_APPEND, "DELETE")),),
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
    forced = {
        "user_context",
        "expected_signals",
        "qa_publication_operations",
        "cost_claims",
        "cost_claim_events",
        "cost_claim_resolutions",
    }
    result = tuple(
        ObjectAuthority(
            "public",
            name,
            ObjectKind.TABLE,
            "dnd_generation_owner" if name == "user_context" else migration_owner,
            permissions[policy]
            + {
                "audit_log": (
                    Permission(
                        "restore_drill_executor_audit_writer",
                        ("INSERT",),
                        ("actor", "action", "target", "result", "error", "metadata"),
                    ),
                ),
                "model_catalog": (Permission("runtime_attention_outbox_owner", _READ),),
                "model_dispatch_attempts": (Permission("runtime_attention_outbox_owner", _READ),),
                "infra_conditions": (Permission("runtime_attention_outbox_owner", _READ),),
                "insight_candidates": (Permission("dashboard_attention_owner", _READ),),
            }.get(name, ()),
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
    "event_chains",
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
    "calendar_event_proposals",
    "calendar_sources",
    "calendar_events",
    "calendar_event_instances",
    "calendar_event_entities",
    "calendar_sync_cursors",
    "calendar_action_log",
)

# core_089 creates logs only in its eleven fixed schemas; core_090 adds
# Chronicler. Neither producer creates a public or arbitrary target-schema log.
_BUTLER_LOG_SCHEMAS = frozenset(
    {
        "education",
        "finance",
        "general",
        "health",
        "home",
        "lifestyle",
        "messenger",
        "qa",
        "relationship",
        "switchboard",
        "travel",
        "chronicler",
    }
)


def _core_tables(schema: str) -> tuple[str, ...]:
    return _CORE_TABLES + (("butler_logs",) if schema in _BUTLER_LOG_SCHEMAS else ())


# These exact old producer windows permit closed administrative inspection.
# Their dropped tables never enter a current-head serving declaration.
_RETIRED_CHAIN_TABLES = {
    "switchboard": (("sw_001", "sw_026", "dashboard_audit_log"),),
    "contacts": (("contacts_001", "contacts_002", "contacts_source_accounts"),),
    "general": (("gen_001", "gen_002", "entities"),),
}

_DOMAIN_TABLES = {
    "relationship": (
        "addresses",
        "contact_entity_map",
        "contact_labels",
        "group_labels",
        "group_members",
        "groups",
        "important_dates",
        "labels",
        "life_event_categories",
        "life_event_types",
        "life_events",
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
        "collection_items",
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
    "contacts": ("contacts_source_links", "contacts_sync_state"),
    "google_drive": ("google_drive_butler_folders",),
    "memory": (
        "episode_tombstones",
        "episodes",
        "facts",
        "knowledge_gap_origins",
        "knowledge_gaps",
        "memory_events",
        "memory_links",
        "memory_policies",
        "predicate_registry",
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
# The actual registry names Home's roster module home_assistant; its existing
# migrations still live in roster/home/migrations. No caller can supply aliases.
_MODULE_MIGRATION_CHAINS = {"home_assistant": "home"}
_MODULE_TABLES["home_assistant"] = _DOMAIN_TABLES["home"]


_DOMAIN_MATERIALIZED_VIEWS = {"finance": ("spending_summaries",)}
_SWITCHBOARD_INFRA_VIEWS = ("v_qa_connector_state", "v_qa_butler_heartbeat")
_SWITCHBOARD_VIEW_EDGES = {
    **dict.fromkeys(_SWITCHBOARD_INFRA_VIEWS, "sw_024"),
    "v_qa_butler_receiver_state": "sw_037",
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
        tables = set(_core_tables(schema)) | set(_DOMAIN_TABLES.get(schema, ()))
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
            if schema == "switchboard" and name == "connector_registry":
                permissions += (Permission("connector_writer", _WRITE),)
            if (schema, name) in {
                ("education", "sessions"),
                ("general", "calendar_event_instances"),
                ("general", "calendar_sources"),
                ("general", "calendar_events"),
                ("general", "calendar_event_entities"),
            }:
                # RFC0014 evidence projections; never specialist private state
                # or a schema-wide/default privilege. Namespace USAGE follows
                # these exact relation permissions in the same compiler.
                permissions += (Permission("butler_chronicler_rw", _READ),)
            if name == "scheduled_tasks":
                permissions += (Permission("dashboard_attention_owner", _READ),)
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
        for name in _DOMAIN_MATERIALIZED_VIEWS.get(schema, ()):
            result.append(
                ObjectAuthority(
                    schema,
                    name,
                    ObjectKind.MATERIALIZED_VIEW,
                    migration_owner,
                    (Permission(runtime, _READ), Permission("dashboard_api", _READ)),
                    "finance_006 fixed materialized projection; refresh remains owner-only",
                )
            )
    return tuple(result)


def protected_objects(profiles: tuple[RuntimeProfile, ...]) -> tuple[ObjectAuthority, ...]:
    """Fixed purpose-state permissions; runtime writers never own their fences."""
    runtimes = tuple(profile.role for profile in profiles)
    switchboard = "butler_switchboard_rw"
    outbox_columns = (
        "lifecycle_state",
        "claim_token",
        "claim_epoch",
        "delivery_lease_epoch",
        "claimed_by_instance",
        "claimed_at",
        "claim_expires_at",
        "next_attempt_at",
        "delivered_at",
        "delivery_error_class",
        "delivery_error_detail",
        "notification_ref",
    )
    rows = [
        ObjectAuthority(
            "public",
            "dnd_generation_guard",
            ObjectKind.TABLE,
            "dnd_generation_owner",
            tuple(Permission(r, _READ) for r in runtimes),
            "canonical generation read; only private DND writer mutates",
        ),
        ObjectAuthority(
            "public",
            "dnd_generation_mutations",
            ObjectKind.TABLE,
            "dnd_generation_owner",
            (),
            "canonical immutable DND replay receipts",
        ),
        ObjectAuthority(
            "restore_drill_executor",
            "restore_drill_results",
            ObjectKind.TABLE,
            "restore_drill_executor_owner",
            (),
            "isolated executor evidence ledger",
        ),
        ObjectAuthority(
            "restore_drill_executor",
            "restore_drill_results_id_seq",
            ObjectKind.SEQUENCE,
            "restore_drill_executor_owner",
            (),
            "isolated executor ledger serial sequence",
            sequence_parent=("restore_drill_executor", "restore_drill_results", "id"),
        ),
        ObjectAuthority(
            "public",
            "runtime_attention_outbox",
            ObjectKind.TABLE,
            "runtime_attention_outbox_owner",
            (Permission(switchboard, _READ), Permission(switchboard, ("UPDATE",), outbox_columns)),
            "fenced attention worker immutable payload/lease CAS",
            True,
        ),
        ObjectAuthority(
            "public",
            "runtime_attention_delivery_lease",
            ObjectKind.TABLE,
            "runtime_attention_outbox_owner",
            (Permission(switchboard, _WRITE),),
            "single fenced delivery lease",
            True,
        ),
    ]
    rows.extend(
        ObjectAuthority(
            "public",
            name,
            ObjectKind.TABLE,
            "runtime_attention_outbox_owner",
            (),
            "owner-only attention activation/episode state",
            forced_rls=name == "runtime_attention_condition_episodes",
        )
        for name in (
            "runtime_attention_operator_control",
            "runtime_attention_producer_control",
            "runtime_attention_condition_control",
            "runtime_attention_condition_episodes",
        )
    )
    return tuple(rows)


_PUBLIC_SEQUENCES = {
    "audit_log_id_seq": "audit_log",
    "dispatch_failures_id_seq": "dispatch_failures",
    "system_prompt_history_id_seq": "system_prompt_history",
    "memory_compaction_log_id_seq": "memory_compaction_log",
    "delegation_wake_attempts_id_seq": "delegation_wake_attempts",
    "insight_feedback_id_seq": "insight_feedback",
    "provider_feature_catalogue_id_seq": "provider_feature_catalogue",
    "consolidation_runs_id_seq": "consolidation_runs",
    "secret_probe_log_id_seq": "secret_probe_log",
    "model_dispatch_attempts_id_seq": "model_dispatch_attempts",
    "butler_reachability_conditions_id_seq": "butler_reachability_conditions",
    "cost_claim_events_id_seq": "cost_claim_events",
}


def declared_sequences(relations: tuple[ObjectAuthority, ...]) -> tuple[ObjectAuthority, ...]:
    """Only named production sequence/parent pairs confer sequence USAGE.

    Catalog discovery cannot add sequences or copy grants to unknown children.
    Parent linkage is independently checked during convergence.
    """
    result = []
    for row in relations:
        names = [
            name
            for name, parent in _PUBLIC_SEQUENCES.items()
            if row.schema == "public" and parent == row.name
        ]
        if row.name == "butler_logs" and row.schema != "public":
            names.append("butler_logs_id_seq")
        if row.name == "ha_command_log" or (row.schema, row.name) == (
            "switchboard",
            "connector_heartbeat_log",
        ):
            names.append(row.name + "_id_seq")
        for name in names:
            permissions = tuple(
                Permission(permission.role, ("USAGE",))
                for permission in row.permissions
                if "INSERT" in permission.verbs
                and not permission.columns
                and permission.role != "cost_claim_restore_owner"
            )
            if (row.schema, name) == ("public", "cost_claim_events_id_seq"):
                permissions += (Permission("cost_claim_restore_owner", ("UPDATE",)),)
            result.append(
                ObjectAuthority(
                    row.schema,
                    name,
                    ObjectKind.SEQUENCE,
                    row.owner,
                    permissions,
                    f"production serial/identity parent {row.schema}.{row.name}",
                    required=False,
                    sequence_parent=(row.schema, row.name, "id"),
                )
            )
    return tuple(result)


def _authentication_relation_permissions(schema: str, name: str, migration_owner: str):
    """Exact underlying rights of the separately preserved owner-auth interfaces."""
    attention, stepup = "dashboard_attention_owner", "dashboard_stepup_owner"
    if schema != "dashboard_auth":
        return (Permission(migration_owner, _READ),)
    permissions = []
    if name in {"instance", "sessions"}:
        permissions.append(Permission(attention, _READ))
    if name == "csrf":
        permissions.append(Permission(attention, _READ, ("session_digest", "digest", "expires_at")))
    if name in {"instance", "sessions", "credentials", "ceremonies"}:
        permissions.append(Permission(stepup, _READ))
    if name == "instance":
        permissions.extend(
            Permission(role, ("UPDATE",), ("singleton",)) for role in (attention, stepup)
        )
    if name == "credentials":
        permissions.append(Permission(stepup, ("UPDATE",), ("counter", "backup_state")))
    if name == "ceremonies":
        permissions.append(Permission(stepup, ("INSERT", "UPDATE")))
    return tuple(permissions)


def auxiliary_objects(migration_owner: str, maintenance_owner: str) -> tuple[ObjectAuthority, ...]:
    """Independent authentication/confirmation state keeps its separate boundary."""
    groups = {
        "dashboard_auth": (
            migration_owner,
            (
                "instance",
                "contexts",
                "intents",
                "ceremonies",
                "credentials",
                "sessions",
                "csrf",
                "rate_buckets",
                "audit",
            ),
        ),
        "dashboard_attention": (
            "dashboard_attention_owner",
            (
                "tickets",
                "stamps",
                "final_claims",
                "candidate_causes",
            ),
        ),
        "dashboard_stepup": ("dashboard_stepup_owner", ("bindings",)),
    }
    rows = [
        ObjectAuthority(
            schema,
            name,
            ObjectKind.TABLE,
            owner,
            _authentication_relation_permissions(schema, name, migration_owner),
            "independent dedicated authentication/confirmation interface",
        )
        for schema, (owner, names) in groups.items()
        for name in names
    ]
    rows.extend(
        ObjectAuthority(
            schema,
            "bootstrap_configuration",
            ObjectKind.TABLE,
            maintenance_owner,
            (Permission(migration_owner, _READ), Permission(f"{schema}_owner", _READ)),
            "retained independent dashboard interface configuration",
        )
        for schema in ("dashboard_attention", "dashboard_stepup")
    )
    rows.append(
        ObjectAuthority(
            "public",
            "break_through_clauses",
            ObjectKind.TABLE,
            "dashboard_attention_owner",
            (Permission(migration_owner, _READ),),
            "independent D13 standing-permission clauses",
        )
    )
    return tuple(rows)


def shared_reader_objects(
    migration_owner: str, *, switchboard: bool = False
) -> tuple[ObjectAuthority, ...]:
    """Released filtered projections and connector-owned producers only."""
    connector_rows = {
        "filtered_events": (Permission("connector_writer", _CRUD),),
        "activitywatch_events": (
            Permission("connector_writer", _WRITE),
            Permission("butler_chronicler_rw", _READ),
        ),
        "home_assistant_history": (
            Permission("connector_writer", _WRITE),
            Permission("butler_chronicler_rw", _READ),
        ),
        "home_assistant_persons": (
            Permission("butler_switchboard_rw", _APPEND),
            Permission("butler_chronicler_rw", _READ),
        ),
        "owner_outbound_events": (
            Permission("connector_writer", _WRITE),
            Permission("butler_chronicler_rw", _READ),
        ),
        "owntracks_points": (
            Permission("connector_writer", _WRITE),
            Permission("butler_chronicler_rw", _READ),
        ),
        "spotify_listening_sessions": (
            Permission("connector_writer", _WRITE),
            Permission("butler_chronicler_rw", _READ),
        ),
        "spotify_spoken_sessions": (
            Permission("connector_writer", _WRITE),
            Permission("butler_chronicler_rw", _READ),
        ),
        "spotify_track_plays": (
            Permission("connector_writer", _WRITE),
            Permission("butler_chronicler_rw", _READ),
        ),
        "steam_cursors": (Permission("connector_writer", _WRITE),),
        "steam_play_history": (
            Permission("connector_writer", _WRITE),
            Permission("butler_chronicler_rw", _READ),
        ),
        "home_assistant_persons_dedup_bak_core_133": (),
    }
    rows = [
        ObjectAuthority(
            "connectors",
            name,
            ObjectKind.TABLE,
            migration_owner,
            permissions,
            "fixed connector producer / owning Chronicler source adapter",
            required=name != "home_assistant_persons_dedup_bak_core_133",
        )
        for name, permissions in connector_rows.items()
    ]
    for name in ("v_qa_recent_failures", "v_qa_tool_call_failures"):
        rows.append(
            ObjectAuthority(
                "public",
                name,
                ObjectKind.VIEW,
                migration_owner,
                (Permission("butler_qa_rw", _READ), Permission("dashboard_api", _READ)),
                "fixed filtered QA failure projection, no underlying specialist grants",
            )
        )
    if switchboard:
        # sw_024 creates this pair in public even when its own chain is scoped.
        # Only its exact QA/observability readers acquire SELECT, never base-table rights.
        readers = (
            "qa",
            "chronicler",
            "education",
            "finance",
            "general",
            "health",
            "home",
            "lifestyle",
            "messenger",
            "relationship",
            "switchboard",
            "travel",
        )
        rows.extend(
            ObjectAuthority(
                "public",
                name,
                ObjectKind.VIEW,
                migration_owner,
                tuple(Permission(f"butler_{reader}_rw", _READ) for reader in readers),
                "sw_024 fixed infra-state QA projection",
            )
            for name in _SWITCHBOARD_INFRA_VIEWS
        )
    if switchboard:
        rows.append(
            ObjectAuthority(
                "public",
                "v_qa_butler_receiver_state",
                ObjectKind.VIEW,
                migration_owner,
                (Permission("butler_qa_rw", _READ),),
                "sw_037 receiver-observed state; QA SELECT only",
            )
        )
    for name in ("v_overlay_contributions", "v_prep_contributions"):
        rows.append(
            ObjectAuthority(
                "calendar",
                name,
                ObjectKind.VIEW,
                migration_owner,
                (Permission("dashboard_api", _READ),),
                "RFC0020 key-filtered Calendar projection; no specialist-state grant",
            )
        )
    return tuple(rows)


@dataclass(frozen=True)
class RelationManifest:
    """One source compilation; catalog rows can only satisfy or contradict it."""

    database: str
    migration_owner: str
    maintenance_owner: str
    profiles: tuple[RuntimeProfile, ...]
    relations: tuple[ObjectAuthority, ...]
    source_database: str
    selected_plans: tuple | None = None

    @property
    def schemas(self) -> tuple[str, ...]:
        return tuple(sorted({row.schema for row in self.relations}))


def compile_relation_manifest(
    database: str, migration_owner: str, maintenance_owner: str, *, source_database: str
) -> RelationManifest:
    """Expand the full deployment from its real roster and explicit purpose rows.

    The administrative compiler can target a disposable database, but no caller
    can supply a capability, schema population, permission list or owner alias.
    Actual database/maintenance identities are independently proved by the held
    migration session before this compilation can authorize any DCL.
    """
    from .roles import PURPOSE_OWNERS, _identifier, compile_deployment_profiles

    _identifier(migration_owner)
    _identifier(maintenance_owner)
    profiles = compile_deployment_profiles(database, source_database=source_database)
    capabilities = (
        {profile.role for profile in profiles}
        | set(PURPOSE_OWNERS)
        | {
            "connector_writer",
            "dashboard_api",
            "dashboard_auth_api",
            "credential_store_writer",
            "restore_drill_executor",
        }
    )
    if migration_owner == maintenance_owner or {migration_owner, maintenance_owner} & capabilities:
        raise AuthorityError("Migration and maintenance identities are not distinct")
    relations = (
        *public_objects(database, migration_owner, profiles=profiles),
        *local_objects(database, migration_owner, profiles=profiles),
        *protected_objects(profiles),
        *auxiliary_objects(migration_owner, maintenance_owner),
        *shared_reader_objects(
            migration_owner, switchboard=any(p.primary_schema == "switchboard" for p in profiles)
        ),
    )
    if any(profile.primary_schema == "relationship" for profile in profiles):
        from butlers.migrations import get_chain_revision_path

        relations += relationship_retained_objects(
            migration_owner,
            relationship_revisions=get_chain_revision_path("relationship"),
            core_revisions=get_chain_revision_path("core"),
        )
    relations = ordinary_purpose_relations(relations)
    relations += declared_sequences(relations)
    # A fixed cross-runtime exception cannot manufacture an absent daemon
    # principal in a selected deployment. Protected/purpose owners remain fixed.
    from dataclasses import replace

    selected_roles = {profile.role for profile in profiles}
    relations = tuple(
        replace(
            row,
            permissions=tuple(
                permission
                for permission in row.permissions
                if not (permission.role.startswith("butler_") and permission.role.endswith("_rw"))
                or permission.role in selected_roles
            ),
        )
        for row in relations
    )
    if len({row.identity for row in relations}) != len(relations):
        raise AuthorityError("Compiled object authority is ambiguous")
    return RelationManifest(
        database, migration_owner, maintenance_owner, profiles, relations, source_database
    )


# DDL functions need ownership of these fixed parents and their independently
# validated partition children. No other observed relation acquires an owner.
ORDINARY_PARTITION_OWNERS = {
    ("connectors", "filtered_events"): "connector_partition_owner",
    ("switchboard", "connector_heartbeat_log"): "switchboard_partition_owner",
    ("switchboard", "message_inbox"): "switchboard_partition_owner",
}


def ordinary_schema_creators(relations):
    """CREATE is required only for fixed partition DDL purpose owners."""
    result = {}
    for row in relations:
        if ORDINARY_PARTITION_OWNERS.get((row.schema, row.name)) == row.owner:
            result.setdefault(row.schema, set()).add(row.owner)
    return {schema: frozenset(owners) for schema, owners in result.items()}


def ordinary_purpose_relations(relations):
    """Minimum underlying capabilities of the fixed ordinary definer bodies."""
    from dataclasses import replace

    rights = {}

    def grant(schema, name, owner, verbs, columns=()):
        rights.setdefault((schema, name), []).append(Permission(owner, verbs, columns))

    for name in ("dashboard_conversation_turns", "dashboard_conversation_turn_sessions"):
        grant("public", name, "dashboard_turn_owner", _WRITE)
    grant("public", "dashboard_messages", "dashboard_turn_owner", _READ)
    grant(
        "relationship",
        "entity_facts",
        "identity_lookup_owner",
        _READ,
        ("subject", "primary", "validity", "object_kind", "predicate", "object"),
    )
    grant("public", "entities", "identity_lookup_owner", _READ, ("id", "roles", "metadata"))
    grant("public", "model_catalog", "model_probe_owner", _READ, ("id",))
    grant(
        "public",
        "model_catalog",
        "model_probe_owner",
        ("UPDATE",),
        (
            "last_verified_at",
            "last_verified_latency_ms",
            "last_verified_ok",
            "last_verified_error",
            "updated_at",
        ),
    )
    grant(
        "relationship",
        "pending_actions",
        "relationship_status_owner",
        _READ,
        ("id", "status", "origin"),
    )
    for name in ("cost_claims", "cost_claim_resolutions", "cost_claim_events"):
        grant("public", name, "cost_claim_restore_owner", _APPEND)
    for name, verbs in {
        "instance": ("SELECT", "UPDATE"),
        "contexts": _CRUD,
        "intents": _CRUD,
        "ceremonies": _CRUD,
        "credentials": _WRITE,
        "sessions": _CRUD,
        "csrf": ("SELECT", "INSERT", "DELETE"),
        "rate_buckets": _WRITE,
        "audit": ("SELECT", "INSERT", "DELETE"),
    }.items():
        grant("dashboard_auth", name, "dashboard_auth_owner", verbs)
    grant("finance", "bills", "finance_bill_status_owner", _READ, ("id", "status"))
    grant(
        "public",
        "insight_candidates",
        "premise_amendment_owner",
        _READ,
        ("id", "status", "delivered_at", "premise"),
    )
    grant(
        "public",
        "insight_amendments",
        "premise_amendment_owner",
        ("INSERT",),
        ("candidate_id", "episode_key", "reason", "summary"),
    )
    grant(
        "health",
        "facts",
        "health_refill_status_owner",
        _READ,
        ("id", "validity", "metadata", "predicate", "scope", "subject", "created_at", "valid_at"),
    )
    grant("switchboard", "butler_registry", "registry_control_owner", _READ, ("name",))
    grant(
        "switchboard",
        "butler_registry",
        "registry_control_owner",
        ("UPDATE",),
        ("eligibility_state", "eligibility_updated_at", "quarantined_at", "quarantine_reason"),
    )
    grant("switchboard", "butler_registry_control_plane", "registry_control_owner", _READ)
    grant(
        "switchboard",
        "butler_registry_control_plane",
        "registry_control_owner",
        ("INSERT",),
        ("name",),
    )
    grant(
        "switchboard",
        "butler_registry_control_plane",
        "registry_control_owner",
        ("UPDATE",),
        (
            "boot_epoch",
            "boot_instance_id",
            "observed_state",
            "observed_boot_epoch",
            "route_compatible",
            "accepting_routes",
            "probe_failure_class",
            "updated_at",
            "probe_sequence",
            "recorded_probe_sequence",
            "last_probe_at",
            "healthy_observed_at",
            "policy_state",
            "policy_actor",
            "policy_reason",
            "policy_changed_at",
            "policy_provenance",
        ),
    )
    grant(
        "switchboard",
        "butler_boot_registrations",
        "registry_control_owner",
        _READ,
        ("name", "boot_instance_id", "boot_epoch"),
    )
    grant(
        "switchboard",
        "butler_boot_registrations",
        "registry_control_owner",
        ("INSERT",),
        ("name", "boot_instance_id", "boot_epoch"),
    )
    return tuple(
        replace(
            row,
            owner=ORDINARY_PARTITION_OWNERS.get((row.schema, row.name), row.owner),
            permissions=(*row.permissions, *rights.get((row.schema, row.name), ())),
        )
        for row in relations
    )


# These retained historical relations have no serving consumer. Their exact
# names permit bounded revision tests/recovery inspection, never runtime grants.
_RETIRED_MEMORY_TABLES = ("embedding_versions", "rule_applications")


def select_migration_manifest(manifest: RelationManifest, plans: tuple | None) -> RelationManifest:
    """Bind required populations to the selected source chains, not catalog discovery.

    Full CLI plans and core-only/private-module administrative plans use the
    same declarations. An object absent because its producer was not requested
    is optional, not inferred authority. Unknown schemas/chains still refuse.
    Historical targets remain administrative: they cannot certify a latest
    serving catalog, and their retained tables receive no runtime privileges.
    """
    from dataclasses import replace

    from .roles import PURPOSE_OWNERS, _identifier

    if plans is None:
        return manifest
    if not isinstance(plans, tuple) or not plans:
        raise AuthorityError("Selected migration plan is unavailable")
    primary = {profile.primary_schema: profile for profile in manifest.profiles}
    private = {
        schema: profile for profile in manifest.profiles for schema in profile.private_schemas
    }
    selected: dict[str, set[str]] = {}
    core_revisions: set[str] = set()
    selected_revisions: dict[tuple[str, str], set[str]] = {}
    historical = False
    for plan in plans:
        if not isinstance(plan, tuple) or len(plan) != 3:
            raise AuthorityError("Selected migration plan is invalid")
        chain, schema, revision = plan
        target = "public" if schema is None else schema
        _identifier(target)
        profile = primary.get(target) or private.get(target)
        if (
            profile is None
            and target != "public"
            and (
                manifest.database == manifest.source_database
                or target
                in {
                    "connectors",
                    "calendar",
                    "dashboard_auth",
                    "dashboard_attention",
                    "dashboard_stepup",
                    "restore_drill_executor",
                    "dnd_generation_private",
                }
                or target in PURPOSE_OWNERS
            )
        ):
            raise AuthorityError("Selected migration schema is not declared")
        if chain == "core":
            if target in private:
                raise AuthorityError("Private module schema cannot own the core chain")
        elif chain in _MODULE_TABLES:
            if profile is not None and (
                chain not in profile.modules or (target in private and chain != "memory")
            ):
                raise AuthorityError("Selected module is not owned by its source profile")
        elif chain not in primary or (
            profile is not None
            and chain != target
            and chain
            not in {_MODULE_MIGRATION_CHAINS.get(module, module) for module in profile.modules}
        ):
            raise AuthorityError("Selected domain migration is not declared")
        if revision is not None and not isinstance(revision, str):
            raise AuthorityError("Selected migration revision is invalid")
        from butlers.migrations import get_chain_revision_path

        try:
            source_revisions = get_chain_revision_path(chain, revision)
        except RuntimeError as exc:
            raise AuthorityError("Selected migration revision is not source-owned") from exc
        selected_revisions.setdefault((target, chain), set()).update(source_revisions)
        if chain == "core":
            core_revisions.update(source_revisions)
        historical |= revision is not None
        selected.setdefault(target, set()).add(chain)

    schemas = set(selected)
    if core_revisions:
        schemas.update({"public", "connectors", "calendar"})
        # These namespaces are created on their actual source revision edges,
        # not by the ordinary role/schema bootstrap. A bounded old migration
        # must not require a future interface's namespace to exist.
        for edge, namespace in (
            ("core_196", "restore_drill_executor"),
            ("core_240", "dashboard_auth"),
            ("core_277", "dashboard_stepup"),
            ("core_278", "dashboard_attention"),
        ):
            if edge in core_revisions:
                schemas.add(namespace)
    infra_views = {
        name
        for name, edge in _SWITCHBOARD_VIEW_EDGES.items()
        if any(
            chain == "switchboard" and edge in revisions
            for (_, chain), revisions in selected_revisions.items()
        )
    }
    if infra_views:
        schemas.add("public")
    rows = {
        row.identity: row
        for row in manifest.relations
        if row.schema in schemas
        and (row.name not in _SWITCHBOARD_VIEW_EDGES or row.name in infra_views)
        and (
            row.kind != ObjectKind.MATERIALIZED_VIEW
            or "finance_006" in selected_revisions.get((row.schema, "finance"), set())
        )
    }
    # Retained outputs follow actual installed source edges, independently of
    # requested serving rights. A core-only/future-domain target cannot admit
    # a Relationship backup just because the full deployment knows its name.
    retained_keys = {("relationship", name) for name in _RELATIONSHIP_RETAINED_NAMES} | {
        ("public", "contacts_preferred_channel_dropbak_rel_003"),
        ("public", "entities_contact_profile_bak_rel_031"),
    }
    rows = {key: row for key, row in rows.items() if (row.schema, row.name) not in retained_keys}
    relationship_revisions = frozenset().union(
        *(
            revisions
            for (_, chain), revisions in selected_revisions.items()
            if chain == "relationship"
        )
    )
    for row in relationship_retained_objects(
        manifest.migration_owner,
        relationship_revisions=relationship_revisions,
        core_revisions=core_revisions,
    ):
        rows[row.identity] = row
    # core_002 creates this database-global table; core_115 drops it. A later
    # selected core replay supersedes an older prefix, independent of target schema.
    if "core_002" in core_revisions and "core_115" not in core_revisions:
        row = ObjectAuthority(
            "public",
            "contact_info",
            ObjectKind.TABLE,
            manifest.migration_owner,
            (),
            "core_002..pre-core_115 retained administrative identity",
            required=False,
        )
        rows[row.identity] = row
    if "core_002" in core_revisions and "core_134" not in core_revisions:
        row = ObjectAuthority(
            "public",
            "contacts",
            ObjectKind.TABLE,
            manifest.migration_owner,
            (),
            "core_002..pre-core_134 retained administrative identity",
            required=False,
        )
        rows[row.identity] = row
    # Supported administrative public/isolated-schema chains have no fabricated
    # runtime profile. Their declared objects remain normal-owner, closed ACLs.
    for target, chains in selected.items():
        names = {"alembic_version"}
        for chain in chains:
            names.update(
                _core_tables(target)
                if chain == "core"
                else _MODULE_TABLES.get(chain, _DOMAIN_TABLES.get(chain, ()))
            )
        for name in names:
            row = ObjectAuthority(
                target,
                name,
                ObjectKind.TABLE,
                manifest.migration_owner,
                (),
                "selected administrative source chain",
            )
            rows.setdefault(row.identity, row)
        for chain in chains:
            revisions = selected_revisions[(target, chain)]
            for introduced, removed, name in _RETIRED_CHAIN_TABLES.get(chain, ()):
                if introduced in revisions and removed not in revisions:
                    row = ObjectAuthority(
                        target,
                        name,
                        ObjectKind.TABLE,
                        manifest.migration_owner,
                        (),
                        "retained historical source revision",
                        required=False,
                    )
                    rows.setdefault(row.identity, row)
        if "finance_006" in selected_revisions.get((target, "finance"), set()):
            for name in _DOMAIN_MATERIALIZED_VIEWS["finance"]:
                row = ObjectAuthority(
                    target,
                    name,
                    ObjectKind.MATERIALIZED_VIEW,
                    manifest.migration_owner,
                    (),
                    "finance_006 selected administrative materialized projection",
                )
                rows.setdefault(row.identity, row)
        if "memory" in chains:
            for name in _RETIRED_MEMORY_TABLES:
                row = ObjectAuthority(
                    target,
                    name,
                    ObjectKind.TABLE,
                    manifest.migration_owner,
                    (),
                    "retained historical Memory revision",
                    required=False,
                )
                rows[row.identity] = row
            sequence = ObjectAuthority(
                target,
                "embedding_versions_id_seq",
                ObjectKind.SEQUENCE,
                manifest.migration_owner,
                (),
                "retained mem_001 serial identity",
                required=False,
                sequence_parent=(target, "embedding_versions", "id"),
            )
            rows[sequence.identity] = sequence

    result = []
    for row in rows.values():
        chains = selected.get(row.schema, set())
        if (
            row.name == "collection_items"
            and "general" in chains
            and "gen_002" not in selected_revisions.get((row.schema, "general"), set())
        ):
            continue
        if row.schema in {
            "public",
            "connectors",
            "restore_drill_executor",
            "dashboard_auth",
            "dashboard_attention",
            "dashboard_stepup",
        }:
            required = row.required and any("core" in values for values in selected.values())
            if row.schema == "public" and not required:
                required = row.required and row.name in {
                    "alembic_version",
                    *(
                        name
                        for chain in chains
                        for name in _MODULE_TABLES.get(chain, _DOMAIN_TABLES.get(chain, ()))
                    ),
                }
        elif row.schema == "calendar":
            required = False  # Released projections are conditional on their source relations.
        else:
            required_names = {"alembic_version"}
            for chain in chains:
                required_names.update(
                    _core_tables(row.schema)
                    if chain == "core"
                    else _MODULE_TABLES.get(chain, _DOMAIN_TABLES.get(chain, ()))
                )
                required_names.update(_DOMAIN_VIEWS.get(chain, ()))
            required = row.required and row.name in required_names
        if row.name in _SWITCHBOARD_VIEW_EDGES:
            required = row.required and row.name in infra_views
        elif (row.schema, row.name) == ("general", "v_briefing_contributions"):
            # core_001 creates General's namespace before core_063's conditional view.
            # A General-domain-only plan never requests that core producer.
            required = row.required and "core_063" in core_revisions
        elif row.kind == ObjectKind.MATERIALIZED_VIEW:
            required = row.required and "finance_006" in selected_revisions.get(
                (row.schema, "finance"), set()
            )
        if row.name in _RETIRED_MEMORY_TABLES:
            required = False
        result.append(
            replace(
                row,
                required=False if historical else required,
                permissions=() if historical else row.permissions,
                owner=manifest.migration_owner
                if historical and (row.schema, row.name) in ORDINARY_PARTITION_OWNERS
                else row.owner,
            )
        )
    # Serial/identity dependencies follow only actual selected parent rows.
    sequences = declared_sequences(tuple(result))
    for row in sequences:
        rows_by_identity = {value.identity for value in result}
        if row.identity not in rows_by_identity:
            result.append(replace(row, permissions=() if historical else row.permissions))
    return replace(manifest, relations=tuple(result), selected_plans=plans)


@dataclass(frozen=True)
class FunctionAuthority:
    """A fixed overload and body, with explicit operation-derived EXECUTE authority."""

    schema: str
    name: str
    signature: str
    owner: str
    executors: tuple[str, ...]
    security_definer: bool
    header: str
    body: str
    producer: str
    settings: tuple[str, ...]

    @property
    def identity(self):
        return self.schema, self.name, self.signature

    @property
    def sql(self):
        return self.header + "AS $db_authority_body$" + self.body + "$db_authority_body$;"


# Current definitions, not predecessor variants. These literal rows are compiled
# source data; neither serving admission nor the migrator parses migration files
# to discover a function, body, role or grant. The producer path is provenance.
_ORDINARY_FUNCTION_SOURCES = (
    (
        "core",
        "connectors",
        "connectors_filtered_events_ensure_partition",
        "timestamp with time zone",
        True,
        (
            "CREATE OR REPLACE FUNCTION connectors.connectors_filtered_events_ensure_part"
            "ition(\n            reference_ts TIMESTAMPTZ DEFAULT now()\n        ) RETURN"
            "S TEXT\n        LANGUAGE plpgsql\n        SECURITY DEFINER\nSET search_path "
            "= pg_catalog, pg_temp\n"
        ),
        (
            "\nDECLARE\n    month_start    TIMESTAMPTZ;\n    month_end      TIMESTAMPTZ;"
            "\n    partition_name TEXT;\n    next_start     TIMESTAMPTZ;\n    next_end   "
            "    TIMESTAMPTZ;\n    next_name      TEXT;\nBEGIN\n    -- Current month part"
            "ition\n    month_start    := date_trunc('month', reference_ts);\n    month_e"
            "nd      := month_start + INTERVAL '1 month';\n    partition_name := format('"
            "filtered_events_%s', to_char(month_start, 'YYYYMM'));\n\n    EXECUTE format("
            "\n        'CREATE TABLE IF NOT EXISTS connectors.%I '\n        'PARTITION OF"
            " connectors.filtered_events '\n        'FOR VALUES FROM (%L) TO (%L)',\n    "
            "    partition_name,\n        month_start,\n        month_end\n    );\n\n    "
            "-- Next month partition (proactive)\n    next_start := month_end;\n    next_"
            "end   := next_start + INTERVAL '1 month';\n    next_name  := format('filtere"
            "d_events_%s', to_char(next_start, 'YYYYMM'));\n\n    EXECUTE format(\n      "
            "  'CREATE TABLE IF NOT EXISTS connectors.%I '\n        'PARTITION OF connect"
            "ors.filtered_events '\n        'FOR VALUES FROM (%L) TO (%L)',\n        next"
            "_name,\n        next_start,\n        next_end\n    );\n\n    RETURN partitio"
            "n_name;\nEND;\n"
        ),
        "alembic/versions/core/core_135_filtered_events_ensure_partition_security_definer.py",
    ),
    (
        "core",
        "__SELECTED_SCHEMA__",
        "connectors_filtered_events_ensure_partition",
        "timestamp with time zone",
        True,
        (
            "CREATE OR REPLACE FUNCTION __SELECTED_SCHEMA__.con"
            "nectors_filtered_events_ensure_partition(\n        "
            "    reference_ts TIMESTAMPTZ DEFAULT now()\n       "
            " ) RETURNS TEXT\n        LANGUAGE plpgsql\n        S"
            "ECURITY DEFINER\nSET search_path = pg_catalog, pg_t"
            "emp\n"
        ),
        (
            "\nDECLARE\n    month_start    TIMESTAMPTZ;\n    month"
            "_end      TIMESTAMPTZ;\n    partition_name TEXT;\n  "
            "  next_start     TIMESTAMPTZ;\n    next_end       T"
            "IMESTAMPTZ;\n    next_name      TEXT;\nBEGIN\n    -- "
            "Current month partition\n    month_start    := date"
            "_trunc('month', reference_ts);\n    month_end      "
            ":= month_start + INTERVAL '1 month';\n    partition"
            "_name := format('filtered_events_%s', to_char(mont"
            "h_start, 'YYYYMM'));\n\n    EXECUTE format(\n        "
            "'CREATE TABLE IF NOT EXISTS connectors.%I '\n      "
            "  'PARTITION OF connectors.filtered_events '\n     "
            "   'FOR VALUES FROM (%L) TO (%L)',\n        partiti"
            "on_name,\n        month_start,\n        month_end\n  "
            "  );\n\n    -- Next month partition (proactive)\n    "
            "next_start := month_end;\n    next_end   := next_st"
            "art + INTERVAL '1 month';\n    next_name  := format"
            "('filtered_events_%s', to_char(next_start, 'YYYYMM"
            "'));\n\n    EXECUTE format(\n        'CREATE TABLE IF"
            " NOT EXISTS connectors.%I '\n        'PARTITION OF "
            "connectors.filtered_events '\n        'FOR VALUES F"
            "ROM (%L) TO (%L)',\n        next_name,\n        next"
            "_start,\n        next_end\n    );\n\n    RETURN partit"
            "ion_name;\nEND;\n"
        ),
        ("alembic/versions/core/core_135_filtered_events_ensure_partition_security_definer.py"),
    ),
    (
        "core",
        "public",
        "resolve_owner_triple",
        "text,text[]",
        True,
        (
            "CREATE OR REPLACE FUNCTION public.resolve_owner_triple(\n    p_predicate tex"
            "t,\n    p_candidates text[]\n)\nRETURNS TABLE(entity_id uuid, is_primary boo"
            "lean)\nLANGUAGE plpgsql\nSTABLE\nSECURITY DEFINER\nSET search_path = pg_cata"
            "log, pg_temp\n"
        ),
        (
            "\nBEGIN\n    RETURN QUERY\n    WITH live_matches AS MATERIALIZED (\n        "
            'SELECT\n            ef.subject,\n            bool_or(ef."primary" IS TRUE)'
            " AS is_primary,\n            bool_or('owner' = ANY(COALESCE(e.roles, ARRAY[]"
            "::text[]))) AS is_owner\n        FROM relationship.entity_facts ef\n        "
            "JOIN public.entities e ON e.id = ef.subject\n        WHERE ef.validity = 'ac"
            "tive'\n          AND ef.object_kind = 'literal'\n          AND (\n          "
            "      (\n                    p_predicate = 'owner-channel'\n                "
            "    AND (\n                        (\n                            ef.predica"
            "te = 'has-email'\n                            AND 'has-email:' || lower(btri"
            "m(ef.object)) = ANY(p_candidates)\n                        )\n              "
            "          OR (\n                            ef.predicate = 'has-handle'\n   "
            "                         AND 'has-handle:' || lower(btrim(ef.object)) = ANY("
            "p_candidates)\n                        )\n                        OR (\n    "
            "                        ef.predicate = 'has-phone'\n                        "
            "    AND EXISTS (\n                                SELECT 1\n                "
            "                FROM unnest(p_candidates) AS candidate(value)\n             "
            "                   CROSS JOIN LATERAL (\n                                   "
            " SELECT split_part(candidate.value, ':', 2) AS digits\n                     "
            "           ) AS normalized\n                                WHERE candidate."
            "value LIKE 'phone-digits:%'\n                                  AND normalize"
            "d.digits ~ '^[0-9]+$'\n                                  AND length(normaliz"
            "ed.digits) >= 8\n                                  AND length(regexp_replace"
            "(ef.object, '\\D', '', 'g')) >= 8\n                                  AND abs"
            "(\n                                      length(regexp_replace(ef.object, '"
            "\\D', '', 'g'))\n                                      - length(normalized.d"
            "igits)\n                                  ) <= 2\n                          "
            "        AND (\n                                      regexp_replace(ef.objec"
            "t, '\\D', '', 'g')\n                                          LIKE '%' || no"
            "rmalized.digits\n                                      OR normalized.digits "
            "LIKE '%'\n                                          || regexp_replace(ef.obj"
            "ect, '\\D', '', 'g')\n                                  )\n                 "
            "           )\n                        )\n                    )\n            "
            "    )\n                OR (\n                    -- Preserve the core_145 pu"
            "blic ABI for exact legacy\n                    -- predicate callers; owner-c"
            "hannel is the new closed mode.\n                    p_predicate <> 'owner-ch"
            "annel'\n                    AND ef.predicate = p_predicate\n                "
            "    AND ef.object = ANY(p_candidates)\n                )\n          )\n     "
            "     AND e.metadata ->> 'merged_into' IS NULL\n          AND e.metadata ->> "
            "'deleted_at' IS NULL\n        GROUP BY ef.subject\n    )\n    SELECT matches"
            ".subject, matches.is_primary\n    FROM live_matches matches\n    WHERE (SELE"
            "CT count(*) FROM live_matches) = 1\n      AND matches.is_owner\n    LIMIT 1;"
            "\nEND;\n"
        ),
        "alembic/versions/core/core_233_owner_channel_ambiguity_guard.py",
    ),
    (
        "core",
        "public",
        "uuid_generate_v7",
        "",
        False,
        (
            "CREATE OR REPLACE FUNCTION public.uuid_generate_v7()\nRETURNS uuid\nLANGUAGE"
            " plpgsql\nVOLATILE\nSET search_path = pg_catalog, pg_temp\n"
        ),
        (
            "\nDECLARE\n    unix_ts_ms bytea;\n    uuid_bytes bytea;\nBEGIN\n    -- 48-bi"
            "t big-endian Unix timestamp in milliseconds (drop the 2 high zero\n    -- by"
            "tes of the 8-byte int8send output).\n    unix_ts_ms := substring(\n        i"
            "nt8send(floor(extract(epoch FROM clock_timestamp()) * 1000)::bigint)\n      "
            "  FROM 3\n    );\n\n    -- Start from a random v4 UUID; its byte 8 already c"
            "arries the RFC-4122\n    -- variant bits (10xx) that UUIDv7 also requires.\n"
            "    uuid_bytes := uuid_send(gen_random_uuid());\n\n    -- Overlay the 6 time"
            "stamp bytes into the leading positions.\n    uuid_bytes := overlay(uuid_byte"
            "s PLACING unix_ts_ms FROM 1 FOR 6);\n\n    -- Force the version nibble (high"
            " 4 bits of byte 6) to 0111 = 7, keep the\n    -- low nibble random.\n    uui"
            "d_bytes := set_byte(uuid_bytes, 6, (get_byte(uuid_bytes, 6) & 15) | 112);\n"
            "\n    RETURN encode(uuid_bytes, 'hex')::uuid;\nEND;\n"
        ),
        "alembic/versions/core/core_148_qa_healing_uuidv7_defaults.py",
    ),
    (
        "core",
        "public",
        "dashboard_turn_require_role",
        "text",
        True,
        (
            "CREATE OR REPLACE FUNCTION public.dashboard_turn_require_role(\n            "
            "p_expected_role text\n        )\n        RETURNS void\n        LANGUAGE plpg"
            "sql\n        SECURITY DEFINER\nSET search_path = pg_catalog, pg_temp\n"
        ),
        (
            "\n        BEGIN\n            IF current_setting('role', true) IS DISTINCT FR"
            "OM p_expected_role THEN\n                RAISE EXCEPTION 'dashboard turn con"
            "trol is not authorized for this runtime role'\n                    USING ERR"
            "CODE = '42501';\n            END IF;\n        END;\n        "
        ),
        "alembic/versions/core/core_193_dashboard_turn_cancellation.py",
    ),
    (
        "core",
        "public",
        "dashboard_turn_open",
        "uuid,uuid",
        True,
        (
            "CREATE OR REPLACE FUNCTION public.dashboard_turn_open(\n            p_messag"
            "e_id uuid,\n            p_conversation_id uuid\n        )\n        \nRETURNS"
            " TABLE(\n    outcome text,\n    message_id uuid,\n    conversation_id uuid,"
            "\n    request_id uuid,\n    target_butler text,\n    target_kind text,\n    "
            "route_inbox_id uuid,\n    cancel_requested_at timestamptz,\n    cancel_confi"
            "rmed_at timestamptz,\n    terminal_state text,\n    terminal_at timestamptz"
            "\n)\n\n        LANGUAGE plpgsql\n        SECURITY DEFINER\nSET search_path ="
            " pg_catalog, pg_temp\n"
        ),
        (
            "\n        #variable_conflict use_column\n        DECLARE\n            v_turn"
            " public.dashboard_conversation_turns%ROWTYPE;\n            p_outcome text;\n"
            "        BEGIN\n            INSERT INTO public.dashboard_conversation_turns ("
            "\n                message_id,\n                conversation_id\n            "
            ")\n            SELECT p_message_id, p_conversation_id\n            WHERE EXI"
            "STS (\n                SELECT 1\n                FROM public.dashboard_messa"
            "ges\n                WHERE id = p_message_id\n                  AND conversa"
            "tion_id = p_conversation_id\n                  AND role = 'user'\n          "
            "  )\n            ON CONFLICT (message_id) DO NOTHING\n            RETURNING "
            "* INTO v_turn;\n\n            IF FOUND THEN\n                -- Opening the "
            "durable row is deliberately not the external\n                -- ingress bou"
            "ndary. The SSE generator claims that boundary\n                -- immediatel"
            "y before calling Switchboard, so a Stop received\n                -- before "
            "generator execution can terminally block dispatch.\n                p_outcom"
            "e := 'ready';\n                \nRETURN QUERY SELECT\n    p_outcome,\n    v_"
            "turn.message_id,\n    v_turn.conversation_id,\n    v_turn.request_id,\n    v"
            "_turn.target_butler,\n    v_turn.target_kind,\n    v_turn.route_inbox_id,\n "
            "   v_turn.cancel_requested_at,\n    v_turn.cancel_confirmed_at,\n    v_turn."
            "terminal_state,\n    v_turn.terminal_at;\nRETURN;\n\n            END IF;\n\n"
            "            SELECT * INTO v_turn\n            FROM public.dashboard_conversa"
            "tion_turns\n            WHERE message_id = p_message_id\n            FOR UPD"
            "ATE;\n            IF NOT FOUND THEN\n                \nRETURN QUERY SELECT\n"
            "    'missing'::text,\n    NULL::uuid,\n    NULL::uuid,\n    NULL::uuid,\n   "
            " NULL::text,\n    NULL::text,\n    NULL::uuid,\n    NULL::timestamptz,\n    "
            "NULL::timestamptz,\n    NULL::text,\n    NULL::timestamptz;\nRETURN;\n\n    "
            "        END IF;\n            IF v_turn.conversation_id <> p_conversation_id "
            "THEN\n                p_outcome := 'conflict';\n                \nRETURN QUE"
            "RY SELECT\n    p_outcome,\n    v_turn.message_id,\n    v_turn.conversation_i"
            "d,\n    v_turn.request_id,\n    v_turn.target_butler,\n    v_turn.target_kin"
            "d,\n    v_turn.route_inbox_id,\n    v_turn.cancel_requested_at,\n    v_turn."
            "cancel_confirmed_at,\n    v_turn.terminal_state,\n    v_turn.terminal_at;\nR"
            "ETURN;\n\n            END IF;\n            IF v_turn.terminal_at IS NOT NULL"
            " THEN\n                p_outcome := CASE\n                    WHEN v_turn.te"
            "rminal_state = 'cancelled' THEN 'cancelled'\n                    WHEN v_turn"
            ".terminal_state = 'ambiguous' THEN 'ambiguous'\n                    ELSE 'fi"
            "nished'\n                END;\n                \nRETURN QUERY SELECT\n    p_"
            "outcome,\n    v_turn.message_id,\n    v_turn.conversation_id,\n    v_turn.re"
            "quest_id,\n    v_turn.target_butler,\n    v_turn.target_kind,\n    v_turn.ro"
            "ute_inbox_id,\n    v_turn.cancel_requested_at,\n    v_turn.cancel_confirmed_"
            "at,\n    v_turn.terminal_state,\n    v_turn.terminal_at;\nRETURN;\n\n       "
            "     END IF;\n            IF v_turn.cancel_requested_at IS NOT NULL THEN\n  "
            "              -- A cancellation request may still be settling across an\n   "
            "             -- already-crossed ingress or runtime boundary. Only the\n     "
            "           -- terminal state above may produce SESSION_CANCELLED.\n         "
            "       p_outcome := 'cancelling';\n                \nRETURN QUERY SELECT\n  "
            "  p_outcome,\n    v_turn.message_id,\n    v_turn.conversation_id,\n    v_tur"
            "n.request_id,\n    v_turn.target_butler,\n    v_turn.target_kind,\n    v_tur"
            "n.route_inbox_id,\n    v_turn.cancel_requested_at,\n    v_turn.cancel_confir"
            "med_at,\n    v_turn.terminal_state,\n    v_turn.terminal_at;\nRETURN;\n\n   "
            "         END IF;\n            IF v_turn.ingress_state = 'accepted' THEN\n   "
            "             p_outcome := 'accepted';\n                \nRETURN QUERY SELECT"
            "\n    p_outcome,\n    v_turn.message_id,\n    v_turn.conversation_id,\n    v"
            "_turn.request_id,\n    v_turn.target_butler,\n    v_turn.target_kind,\n    v"
            "_turn.route_inbox_id,\n    v_turn.cancel_requested_at,\n    v_turn.cancel_co"
            "nfirmed_at,\n    v_turn.terminal_state,\n    v_turn.terminal_at;\nRETURN;\n"
            "\n            END IF;\n            IF v_turn.ingress_state = 'submitting' TH"
            "EN\n                p_outcome := 'pending';\n                \nRETURN QUERY "
            "SELECT\n    p_outcome,\n    v_turn.message_id,\n    v_turn.conversation_id,"
            "\n    v_turn.request_id,\n    v_turn.target_butler,\n    v_turn.target_kind,"
            "\n    v_turn.route_inbox_id,\n    v_turn.cancel_requested_at,\n    v_turn.ca"
            "ncel_confirmed_at,\n    v_turn.terminal_state,\n    v_turn.terminal_at;\nRET"
            "URN;\n\n            END IF;\n            p_outcome := 'ready';\n            "
            "\nRETURN QUERY SELECT\n    p_outcome,\n    v_turn.message_id,\n    v_turn.co"
            "nversation_id,\n    v_turn.request_id,\n    v_turn.target_butler,\n    v_tur"
            "n.target_kind,\n    v_turn.route_inbox_id,\n    v_turn.cancel_requested_at,"
            "\n    v_turn.cancel_confirmed_at,\n    v_turn.terminal_state,\n    v_turn.te"
            "rminal_at;\nRETURN;\n\n        END;\n        "
        ),
        "alembic/versions/core/core_193_dashboard_turn_cancellation.py",
    ),
    (
        "core",
        "public",
        "dashboard_turn_claim_ingress",
        "uuid",
        True,
        (
            "CREATE OR REPLACE FUNCTION public.dashboard_turn_claim_ingress(\n           "
            " p_message_id uuid\n        )\n        \nRETURNS TABLE(\n    outcome text,\n"
            "    message_id uuid,\n    conversation_id uuid,\n    request_id uuid,\n    t"
            "arget_butler text,\n    target_kind text,\n    route_inbox_id uuid,\n    can"
            "cel_requested_at timestamptz,\n    cancel_confirmed_at timestamptz,\n    ter"
            "minal_state text,\n    terminal_at timestamptz\n)\n\n        LANGUAGE plpgsq"
            "l\n        SECURITY DEFINER\nSET search_path = pg_catalog, pg_temp\n"
        ),
        (
            "\n        #variable_conflict use_column\n        DECLARE\n            v_turn"
            " public.dashboard_conversation_turns%ROWTYPE;\n            p_outcome text;\n"
            "        BEGIN\n            SELECT * INTO v_turn\n            FROM public.das"
            "hboard_conversation_turns\n            WHERE message_id = p_message_id\n    "
            "        FOR UPDATE;\n            IF NOT FOUND THEN\n                \nRETURN"
            " QUERY SELECT\n    'missing'::text,\n    NULL::uuid,\n    NULL::uuid,\n    N"
            "ULL::uuid,\n    NULL::text,\n    NULL::text,\n    NULL::uuid,\n    NULL::tim"
            "estamptz,\n    NULL::timestamptz,\n    NULL::text,\n    NULL::timestamptz;\n"
            "RETURN;\n\n            END IF;\n            IF v_turn.terminal_at IS NOT NUL"
            "L THEN\n                p_outcome := CASE\n                    WHEN v_turn.t"
            "erminal_state = 'cancelled' THEN 'cancelled'\n                    WHEN v_tur"
            "n.terminal_state = 'ambiguous' THEN 'ambiguous'\n                    ELSE 'f"
            "inished'\n                END;\n                \nRETURN QUERY SELECT\n    p"
            "_outcome,\n    v_turn.message_id,\n    v_turn.conversation_id,\n    v_turn.r"
            "equest_id,\n    v_turn.target_butler,\n    v_turn.target_kind,\n    v_turn.r"
            "oute_inbox_id,\n    v_turn.cancel_requested_at,\n    v_turn.cancel_confirmed"
            "_at,\n    v_turn.terminal_state,\n    v_turn.terminal_at;\nRETURN;\n\n      "
            "      END IF;\n            IF v_turn.cancel_requested_at IS NOT NULL THEN\n "
            "               p_outcome := 'cancelling';\n                \nRETURN QUERY SE"
            "LECT\n    p_outcome,\n    v_turn.message_id,\n    v_turn.conversation_id,\n "
            "   v_turn.request_id,\n    v_turn.target_butler,\n    v_turn.target_kind,\n "
            "   v_turn.route_inbox_id,\n    v_turn.cancel_requested_at,\n    v_turn.cance"
            "l_confirmed_at,\n    v_turn.terminal_state,\n    v_turn.terminal_at;\nRETURN"
            ";\n\n            END IF;\n            IF v_turn.ingress_state = 'accepted' T"
            "HEN\n                p_outcome := 'accepted';\n                \nRETURN QUER"
            "Y SELECT\n    p_outcome,\n    v_turn.message_id,\n    v_turn.conversation_id"
            ",\n    v_turn.request_id,\n    v_turn.target_butler,\n    v_turn.target_kind"
            ",\n    v_turn.route_inbox_id,\n    v_turn.cancel_requested_at,\n    v_turn.c"
            "ancel_confirmed_at,\n    v_turn.terminal_state,\n    v_turn.terminal_at;\nRE"
            "TURN;\n\n            END IF;\n            IF v_turn.ingress_state = 'submitt"
            "ing'\n               AND v_turn.ingress_claimed_at > now() - interval '60 se"
            "conds' THEN\n                p_outcome := 'pending';\n                \nRETU"
            "RN QUERY SELECT\n    p_outcome,\n    v_turn.message_id,\n    v_turn.conversa"
            "tion_id,\n    v_turn.request_id,\n    v_turn.target_butler,\n    v_turn.targ"
            "et_kind,\n    v_turn.route_inbox_id,\n    v_turn.cancel_requested_at,\n    v"
            "_turn.cancel_confirmed_at,\n    v_turn.terminal_state,\n    v_turn.terminal_"
            "at;\nRETURN;\n\n            END IF;\n            UPDATE public.dashboard_con"
            "versation_turns\n            SET ingress_state = 'submitting',\n            "
            "    ingress_attempts = ingress_attempts + 1,\n                ingress_claime"
            "d_at = now(),\n                ingress_error = NULL,\n                update"
            "d_at = now()\n            WHERE message_id = p_message_id\n            RETUR"
            "NING * INTO v_turn;\n            p_outcome := 'dispatch';\n            \nRET"
            "URN QUERY SELECT\n    p_outcome,\n    v_turn.message_id,\n    v_turn.convers"
            "ation_id,\n    v_turn.request_id,\n    v_turn.target_butler,\n    v_turn.tar"
            "get_kind,\n    v_turn.route_inbox_id,\n    v_turn.cancel_requested_at,\n    "
            "v_turn.cancel_confirmed_at,\n    v_turn.terminal_state,\n    v_turn.terminal"
            "_at;\nRETURN;\n\n        END;\n        "
        ),
        "alembic/versions/core/core_193_dashboard_turn_cancellation.py",
    ),
    (
        "core",
        "public",
        "dashboard_turn_bind_ingress",
        "uuid,uuid",
        True,
        (
            "CREATE OR REPLACE FUNCTION public.dashboard_turn_bind_ingress(\n            "
            "p_message_id uuid,\n            p_request_id uuid\n        )\n        \nRETU"
            "RNS TABLE(\n    outcome text,\n    message_id uuid,\n    conversation_id uui"
            "d,\n    request_id uuid,\n    target_butler text,\n    target_kind text,\n  "
            "  route_inbox_id uuid,\n    cancel_requested_at timestamptz,\n    cancel_con"
            "firmed_at timestamptz,\n    terminal_state text,\n    terminal_at timestampt"
            "z\n)\n\n        LANGUAGE plpgsql\n        SECURITY DEFINER\nSET search_path "
            "= pg_catalog, pg_temp\n"
        ),
        (
            "\n        #variable_conflict use_column\n        DECLARE\n            v_turn"
            " public.dashboard_conversation_turns%ROWTYPE;\n            v_has_prior_invoc"
            "ation boolean;\n            p_outcome text;\n        BEGIN\n            SELE"
            "CT * INTO v_turn\n            FROM public.dashboard_conversation_turns\n    "
            "        WHERE message_id = p_message_id\n            FOR UPDATE;\n          "
            "  IF NOT FOUND THEN\n                \nRETURN QUERY SELECT\n    'missing'::t"
            "ext,\n    NULL::uuid,\n    NULL::uuid,\n    NULL::uuid,\n    NULL::text,\n  "
            "  NULL::text,\n    NULL::uuid,\n    NULL::timestamptz,\n    NULL::timestampt"
            "z,\n    NULL::text,\n    NULL::timestamptz;\nRETURN;\n\n            END IF;"
            "\n            IF v_turn.terminal_at IS NOT NULL THEN\n                p_outc"
            "ome := CASE\n                    WHEN v_turn.terminal_state = 'cancelled' TH"
            "EN 'cancelled'\n                    WHEN v_turn.terminal_state = 'ambiguous'"
            " THEN 'ambiguous'\n                    ELSE 'finished'\n                END;"
            "\n                \nRETURN QUERY SELECT\n    p_outcome,\n    v_turn.message_"
            "id,\n    v_turn.conversation_id,\n    v_turn.request_id,\n    v_turn.target_"
            "butler,\n    v_turn.target_kind,\n    v_turn.route_inbox_id,\n    v_turn.can"
            "cel_requested_at,\n    v_turn.cancel_confirmed_at,\n    v_turn.terminal_stat"
            "e,\n    v_turn.terminal_at;\nRETURN;\n\n            END IF;\n            IF "
            "v_turn.request_id IS NOT NULL AND v_turn.request_id <> p_request_id THEN\n  "
            "              p_outcome := 'conflict';\n                \nRETURN QUERY SELEC"
            "T\n    p_outcome,\n    v_turn.message_id,\n    v_turn.conversation_id,\n    "
            "v_turn.request_id,\n    v_turn.target_butler,\n    v_turn.target_kind,\n    "
            "v_turn.route_inbox_id,\n    v_turn.cancel_requested_at,\n    v_turn.cancel_c"
            "onfirmed_at,\n    v_turn.terminal_state,\n    v_turn.terminal_at;\nRETURN;\n"
            "\n            END IF;\n            SELECT EXISTS (\n                SELECT 1"
            "\n                FROM public.dashboard_conversation_turn_sessions\n        "
            "        WHERE message_id = p_message_id\n                  AND invoke_claime"
            "d_at IS NOT NULL\n            ) INTO v_has_prior_invocation;\n            UP"
            "DATE public.dashboard_conversation_turns\n            SET request_id = p_req"
            "uest_id,\n                ingress_state = 'accepted',\n                ingre"
            "ss_error = NULL,\n                cancel_confirmed_at = CASE\n              "
            "      WHEN cancel_requested_at IS NOT NULL AND NOT v_has_prior_invocation\n "
            "                       THEN coalesce(cancel_confirmed_at, now())\n          "
            "          ELSE cancel_confirmed_at\n                END,\n                te"
            "rminal_state = CASE\n                    WHEN cancel_requested_at IS NOT NUL"
            "L AND NOT v_has_prior_invocation\n                        THEN 'cancelled'\n"
            "                    ELSE terminal_state\n                END,\n             "
            "   terminal_at = CASE\n                    WHEN cancel_requested_at IS NOT N"
            "ULL AND NOT v_has_prior_invocation\n                        THEN now()\n    "
            "                ELSE terminal_at\n                END,\n                upda"
            "ted_at = now()\n            WHERE message_id = p_message_id\n            RET"
            "URNING * INTO v_turn;\n            p_outcome := CASE\n                WHEN v"
            "_turn.terminal_state = 'cancelled' THEN 'cancelled'\n                WHEN v_"
            "turn.terminal_state = 'ambiguous' THEN 'ambiguous'\n                WHEN v_t"
            "urn.terminal_at IS NOT NULL THEN 'finished'\n                WHEN v_turn.can"
            "cel_requested_at IS NOT NULL THEN 'cancelling'\n                ELSE 'accept"
            "ed'\n            END;\n            \nRETURN QUERY SELECT\n    p_outcome,\n  "
            "  v_turn.message_id,\n    v_turn.conversation_id,\n    v_turn.request_id,\n "
            "   v_turn.target_butler,\n    v_turn.target_kind,\n    v_turn.route_inbox_id"
            ",\n    v_turn.cancel_requested_at,\n    v_turn.cancel_confirmed_at,\n    v_t"
            "urn.terminal_state,\n    v_turn.terminal_at;\nRETURN;\n\n        END;\n     "
            "   "
        ),
        "alembic/versions/core/core_193_dashboard_turn_cancellation.py",
    ),
    (
        "core",
        "public",
        "dashboard_turn_record_ingress_failure",
        "uuid,text,text",
        True,
        (
            "CREATE OR REPLACE FUNCTION public.dashboard_turn_record_ingress_failure(\n  "
            "          p_message_id uuid,\n            p_state text,\n            p_detai"
            "l text\n        )\n        \nRETURNS TABLE(\n    outcome text,\n    message_"
            "id uuid,\n    conversation_id uuid,\n    request_id uuid,\n    target_butler"
            " text,\n    target_kind text,\n    route_inbox_id uuid,\n    cancel_requeste"
            "d_at timestamptz,\n    cancel_confirmed_at timestamptz,\n    terminal_state "
            "text,\n    terminal_at timestamptz\n)\n\n        LANGUAGE plpgsql\n        S"
            "ECURITY DEFINER\nSET search_path = pg_catalog, pg_temp\n"
        ),
        (
            "\n        #variable_conflict use_column\n        DECLARE\n            v_turn"
            " public.dashboard_conversation_turns%ROWTYPE;\n            v_has_prior_invoc"
            "ation boolean;\n            p_outcome text;\n        BEGIN\n            IF p"
            "_state NOT IN ('retryable_error', 'rejected') THEN\n                RAISE EX"
            "CEPTION 'invalid dashboard turn ingress state: %', p_state\n                "
            "    USING ERRCODE = '22023';\n            END IF;\n            SELECT * INTO"
            " v_turn\n            FROM public.dashboard_conversation_turns\n            W"
            "HERE message_id = p_message_id\n            FOR UPDATE;\n            IF NOT "
            "FOUND THEN\n                \nRETURN QUERY SELECT\n    'missing'::text,\n   "
            " NULL::uuid,\n    NULL::uuid,\n    NULL::uuid,\n    NULL::text,\n    NULL::t"
            "ext,\n    NULL::uuid,\n    NULL::timestamptz,\n    NULL::timestamptz,\n    N"
            "ULL::text,\n    NULL::timestamptz;\nRETURN;\n\n            END IF;\n        "
            "    IF v_turn.terminal_at IS NOT NULL THEN\n                p_outcome := CAS"
            "E\n                    WHEN v_turn.terminal_state = 'cancelled' THEN 'cancel"
            "led'\n                    WHEN v_turn.terminal_state = 'ambiguous' THEN 'amb"
            "iguous'\n                    ELSE 'finished'\n                END;\n        "
            "        \nRETURN QUERY SELECT\n    p_outcome,\n    v_turn.message_id,\n    v"
            "_turn.conversation_id,\n    v_turn.request_id,\n    v_turn.target_butler,\n "
            "   v_turn.target_kind,\n    v_turn.route_inbox_id,\n    v_turn.cancel_reques"
            "ted_at,\n    v_turn.cancel_confirmed_at,\n    v_turn.terminal_state,\n    v_"
            "turn.terminal_at;\nRETURN;\n\n            END IF;\n            SELECT EXISTS"
            " (\n                SELECT 1\n                FROM public.dashboard_conversa"
            "tion_turn_sessions\n                WHERE message_id = p_message_id\n       "
            "           AND invoke_claimed_at IS NOT NULL\n            ) INTO v_has_prior"
            "_invocation;\n            IF v_turn.cancel_requested_at IS NOT NULL AND NOT "
            "v_has_prior_invocation THEN\n                UPDATE public.dashboard_convers"
            "ation_turns\n                SET cancel_confirmed_at = coalesce(cancel_confi"
            "rmed_at, now()),\n                    terminal_state = 'cancelled',\n       "
            "             terminal_at = now(),\n                    updated_at = now()\n "
            "               WHERE message_id = p_message_id\n                RETURNING * "
            "INTO v_turn;\n                p_outcome := 'cancelled';\n                \nR"
            "ETURN QUERY SELECT\n    p_outcome,\n    v_turn.message_id,\n    v_turn.conve"
            "rsation_id,\n    v_turn.request_id,\n    v_turn.target_butler,\n    v_turn.t"
            "arget_kind,\n    v_turn.route_inbox_id,\n    v_turn.cancel_requested_at,\n  "
            "  v_turn.cancel_confirmed_at,\n    v_turn.terminal_state,\n    v_turn.termin"
            "al_at;\nRETURN;\n\n            END IF;\n            UPDATE public.dashboard_"
            "conversation_turns\n            SET ingress_state = p_state,\n              "
            "  ingress_error = left(coalesce(p_detail, ''), 4096),\n                termi"
            "nal_state = CASE\n                    WHEN p_state = 'rejected' THEN 'failed"
            "'\n                    ELSE terminal_state\n                END,\n          "
            "      terminal_at = CASE\n                    WHEN p_state = 'rejected' THEN"
            " now()\n                    ELSE terminal_at\n                END,\n        "
            "        updated_at = now()\n            WHERE message_id = p_message_id\n   "
            "         RETURNING * INTO v_turn;\n            p_outcome := CASE\n          "
            "      WHEN v_turn.terminal_state = 'cancelled' THEN 'cancelled'\n           "
            "     WHEN v_turn.terminal_state = 'ambiguous' THEN 'ambiguous'\n            "
            "    WHEN v_turn.terminal_at IS NOT NULL THEN 'finished'\n                WHE"
            "N v_turn.cancel_requested_at IS NOT NULL THEN 'cancelling'\n                "
            "ELSE 'active'\n            END;\n            \nRETURN QUERY SELECT\n    p_ou"
            "tcome,\n    v_turn.message_id,\n    v_turn.conversation_id,\n    v_turn.requ"
            "est_id,\n    v_turn.target_butler,\n    v_turn.target_kind,\n    v_turn.rout"
            "e_inbox_id,\n    v_turn.cancel_requested_at,\n    v_turn.cancel_confirmed_at"
            ",\n    v_turn.terminal_state,\n    v_turn.terminal_at;\nRETURN;\n\n        E"
            "ND;\n        "
        ),
        "alembic/versions/core/core_193_dashboard_turn_cancellation.py",
    ),
    (
        "core",
        "public",
        "dashboard_turn_claim_target",
        "uuid,uuid,text",
        True,
        (
            "CREATE OR REPLACE FUNCTION public.dashboard_turn_claim_target(\n            "
            "p_message_id uuid,\n            p_request_id uuid,\n            p_target_but"
            "ler text\n        )\n        \nRETURNS TABLE(\n    outcome text,\n    messag"
            "e_id uuid,\n    conversation_id uuid,\n    request_id uuid,\n    target_butl"
            "er text,\n    target_kind text,\n    route_inbox_id uuid,\n    cancel_reques"
            "ted_at timestamptz,\n    cancel_confirmed_at timestamptz,\n    terminal_stat"
            "e text,\n    terminal_at timestamptz\n)\n\n        LANGUAGE plpgsql\n       "
            " SECURITY DEFINER\nSET search_path = pg_catalog, pg_temp\n"
        ),
        (
            "\n        #variable_conflict use_column\n        DECLARE\n            v_turn"
            " public.dashboard_conversation_turns%ROWTYPE;\n            p_outcome text;\n"
            "        BEGIN\n            PERFORM public.dashboard_turn_require_role('butle"
            "r_' || p_target_butler || '_rw');\n            SELECT * INTO v_turn\n       "
            "     FROM public.dashboard_conversation_turns\n            WHERE message_id "
            "= p_message_id\n            FOR UPDATE;\n            IF NOT FOUND THEN\n    "
            "            \nRETURN QUERY SELECT\n    'missing'::text,\n    NULL::uuid,\n  "
            "  NULL::uuid,\n    NULL::uuid,\n    NULL::text,\n    NULL::text,\n    NULL::"
            "uuid,\n    NULL::timestamptz,\n    NULL::timestamptz,\n    NULL::text,\n    "
            "NULL::timestamptz;\nRETURN;\n\n            END IF;\n            IF v_turn.ca"
            "ncel_requested_at IS NOT NULL THEN\n                p_outcome := 'cancelled'"
            ";\n                \nRETURN QUERY SELECT\n    p_outcome,\n    v_turn.message"
            "_id,\n    v_turn.conversation_id,\n    v_turn.request_id,\n    v_turn.target"
            "_butler,\n    v_turn.target_kind,\n    v_turn.route_inbox_id,\n    v_turn.ca"
            "ncel_requested_at,\n    v_turn.cancel_confirmed_at,\n    v_turn.terminal_sta"
            "te,\n    v_turn.terminal_at;\nRETURN;\n\n            END IF;\n            IF"
            " v_turn.terminal_at IS NOT NULL THEN\n                p_outcome := 'finished"
            "';\n                \nRETURN QUERY SELECT\n    p_outcome,\n    v_turn.messag"
            "e_id,\n    v_turn.conversation_id,\n    v_turn.request_id,\n    v_turn.targe"
            "t_butler,\n    v_turn.target_kind,\n    v_turn.route_inbox_id,\n    v_turn.c"
            "ancel_requested_at,\n    v_turn.cancel_confirmed_at,\n    v_turn.terminal_st"
            "ate,\n    v_turn.terminal_at;\nRETURN;\n\n            END IF;\n            I"
            "F p_target_butler IS NULL OR btrim(p_target_butler) = '' THEN\n             "
            "   p_outcome := 'conflict';\n                \nRETURN QUERY SELECT\n    p_ou"
            "tcome,\n    v_turn.message_id,\n    v_turn.conversation_id,\n    v_turn.requ"
            "est_id,\n    v_turn.target_butler,\n    v_turn.target_kind,\n    v_turn.rout"
            "e_inbox_id,\n    v_turn.cancel_requested_at,\n    v_turn.cancel_confirmed_at"
            ",\n    v_turn.terminal_state,\n    v_turn.terminal_at;\nRETURN;\n\n         "
            "   END IF;\n            IF v_turn.request_id IS NOT NULL AND v_turn.request_"
            "id <> p_request_id THEN\n                p_outcome := 'conflict';\n         "
            "       \nRETURN QUERY SELECT\n    p_outcome,\n    v_turn.message_id,\n    v_"
            "turn.conversation_id,\n    v_turn.request_id,\n    v_turn.target_butler,\n  "
            "  v_turn.target_kind,\n    v_turn.route_inbox_id,\n    v_turn.cancel_request"
            "ed_at,\n    v_turn.cancel_confirmed_at,\n    v_turn.terminal_state,\n    v_t"
            "urn.terminal_at;\nRETURN;\n\n            END IF;\n            IF v_turn.targ"
            "et_kind = 'route' THEN\n                IF v_turn.target_butler IS DISTINCT "
            "FROM p_target_butler THEN\n                    p_outcome := 'conflict';\n   "
            "                 \nRETURN QUERY SELECT\n    p_outcome,\n    v_turn.message_i"
            "d,\n    v_turn.conversation_id,\n    v_turn.request_id,\n    v_turn.target_b"
            "utler,\n    v_turn.target_kind,\n    v_turn.route_inbox_id,\n    v_turn.canc"
            "el_requested_at,\n    v_turn.cancel_confirmed_at,\n    v_turn.terminal_state"
            ",\n    v_turn.terminal_at;\nRETURN;\n\n                END IF;\n            "
            "    IF v_turn.route_inbox_id IS NOT NULL THEN\n                    p_outcome"
            " := 'enqueued';\n                    \nRETURN QUERY SELECT\n    p_outcome,\n"
            "    v_turn.message_id,\n    v_turn.conversation_id,\n    v_turn.request_id,"
            "\n    v_turn.target_butler,\n    v_turn.target_kind,\n    v_turn.route_inbox"
            "_id,\n    v_turn.cancel_requested_at,\n    v_turn.cancel_confirmed_at,\n    "
            "v_turn.terminal_state,\n    v_turn.terminal_at;\nRETURN;\n\n                "
            "END IF;\n                p_outcome := 'active';\n                \nRETURN QU"
            "ERY SELECT\n    p_outcome,\n    v_turn.message_id,\n    v_turn.conversation_"
            "id,\n    v_turn.request_id,\n    v_turn.target_butler,\n    v_turn.target_ki"
            "nd,\n    v_turn.route_inbox_id,\n    v_turn.cancel_requested_at,\n    v_turn"
            ".cancel_confirmed_at,\n    v_turn.terminal_state,\n    v_turn.terminal_at;\n"
            "RETURN;\n\n            END IF;\n            IF v_turn.target_kind IS NOT NUL"
            "L THEN\n                p_outcome := 'conflict';\n                \nRETURN Q"
            "UERY SELECT\n    p_outcome,\n    v_turn.message_id,\n    v_turn.conversation"
            "_id,\n    v_turn.request_id,\n    v_turn.target_butler,\n    v_turn.target_k"
            "ind,\n    v_turn.route_inbox_id,\n    v_turn.cancel_requested_at,\n    v_tur"
            "n.cancel_confirmed_at,\n    v_turn.terminal_state,\n    v_turn.terminal_at;"
            "\nRETURN;\n\n            END IF;\n            UPDATE public.dashboard_conver"
            "sation_turns\n            SET request_id = coalesce(request_id, p_request_id"
            "),\n                ingress_state = 'accepted',\n                target_butl"
            "er = p_target_butler,\n                target_kind = 'route',\n             "
            "   target_claimed_at = now(),\n                updated_at = now()\n         "
            "   WHERE message_id = p_message_id\n            RETURNING * INTO v_turn;\n  "
            "          p_outcome := 'active';\n            \nRETURN QUERY SELECT\n    p_o"
            "utcome,\n    v_turn.message_id,\n    v_turn.conversation_id,\n    v_turn.req"
            "uest_id,\n    v_turn.target_butler,\n    v_turn.target_kind,\n    v_turn.rou"
            "te_inbox_id,\n    v_turn.cancel_requested_at,\n    v_turn.cancel_confirmed_a"
            "t,\n    v_turn.terminal_state,\n    v_turn.terminal_at;\nRETURN;\n\n        "
            "END;\n        "
        ),
        "alembic/versions/core/core_193_dashboard_turn_cancellation.py",
    ),
    (
        "core",
        "public",
        "dashboard_turn_mark_route_enqueued",
        "uuid,uuid",
        True,
        (
            "CREATE OR REPLACE FUNCTION public.dashboard_turn_mark_route_enqueued(\n     "
            "       p_message_id uuid,\n            p_route_inbox_id uuid\n        )\n   "
            "     \nRETURNS TABLE(\n    outcome text,\n    message_id uuid,\n    conversa"
            "tion_id uuid,\n    request_id uuid,\n    target_butler text,\n    target_kin"
            "d text,\n    route_inbox_id uuid,\n    cancel_requested_at timestamptz,\n   "
            " cancel_confirmed_at timestamptz,\n    terminal_state text,\n    terminal_at"
            " timestamptz\n)\n\n        LANGUAGE plpgsql\n        SECURITY DEFINER\nSET s"
            "earch_path = pg_catalog, pg_temp\n"
        ),
        (
            "\n        #variable_conflict use_column\n        DECLARE\n            v_turn"
            " public.dashboard_conversation_turns%ROWTYPE;\n            p_outcome text;\n"
            "        BEGIN\n            SELECT * INTO v_turn\n            FROM public.das"
            "hboard_conversation_turns\n            WHERE message_id = p_message_id\n    "
            "        FOR UPDATE;\n            IF NOT FOUND THEN\n                \nRETURN"
            " QUERY SELECT\n    'missing'::text,\n    NULL::uuid,\n    NULL::uuid,\n    N"
            "ULL::uuid,\n    NULL::text,\n    NULL::text,\n    NULL::uuid,\n    NULL::tim"
            "estamptz,\n    NULL::timestamptz,\n    NULL::text,\n    NULL::timestamptz;\n"
            "RETURN;\n\n            END IF;\n            IF v_turn.cancel_requested_at IS"
            " NOT NULL THEN\n                p_outcome := 'cancelled';\n                "
            "\nRETURN QUERY SELECT\n    p_outcome,\n    v_turn.message_id,\n    v_turn.co"
            "nversation_id,\n    v_turn.request_id,\n    v_turn.target_butler,\n    v_tur"
            "n.target_kind,\n    v_turn.route_inbox_id,\n    v_turn.cancel_requested_at,"
            "\n    v_turn.cancel_confirmed_at,\n    v_turn.terminal_state,\n    v_turn.te"
            "rminal_at;\nRETURN;\n\n            END IF;\n            IF v_turn.terminal_a"
            "t IS NOT NULL THEN\n                p_outcome := 'finished';\n              "
            "  \nRETURN QUERY SELECT\n    p_outcome,\n    v_turn.message_id,\n    v_turn."
            "conversation_id,\n    v_turn.request_id,\n    v_turn.target_butler,\n    v_t"
            "urn.target_kind,\n    v_turn.route_inbox_id,\n    v_turn.cancel_requested_at"
            ",\n    v_turn.cancel_confirmed_at,\n    v_turn.terminal_state,\n    v_turn.t"
            "erminal_at;\nRETURN;\n\n            END IF;\n            IF v_turn.target_ki"
            "nd <> 'route' OR v_turn.target_butler IS NULL THEN\n                p_outcom"
            "e := 'conflict';\n                \nRETURN QUERY SELECT\n    p_outcome,\n   "
            " v_turn.message_id,\n    v_turn.conversation_id,\n    v_turn.request_id,\n  "
            "  v_turn.target_butler,\n    v_turn.target_kind,\n    v_turn.route_inbox_id,"
            "\n    v_turn.cancel_requested_at,\n    v_turn.cancel_confirmed_at,\n    v_tu"
            "rn.terminal_state,\n    v_turn.terminal_at;\nRETURN;\n\n            END IF;"
            "\n            PERFORM public.dashboard_turn_require_role(\n                '"
            "butler_' || v_turn.target_butler || '_rw'\n            );\n            IF v_"
            "turn.route_inbox_id IS NOT NULL THEN\n                IF v_turn.route_inbox_"
            "id = p_route_inbox_id THEN\n                    p_outcome := 'enqueued';\n  "
            "              ELSE\n                    p_outcome := 'conflict';\n          "
            "      END IF;\n                \nRETURN QUERY SELECT\n    p_outcome,\n    v_"
            "turn.message_id,\n    v_turn.conversation_id,\n    v_turn.request_id,\n    v"
            "_turn.target_butler,\n    v_turn.target_kind,\n    v_turn.route_inbox_id,\n "
            "   v_turn.cancel_requested_at,\n    v_turn.cancel_confirmed_at,\n    v_turn."
            "terminal_state,\n    v_turn.terminal_at;\nRETURN;\n\n            END IF;\n  "
            "          UPDATE public.dashboard_conversation_turns\n            SET route_"
            "inbox_id = p_route_inbox_id,\n                route_enqueued_at = now(),\n  "
            "              updated_at = now()\n            WHERE message_id = p_message_i"
            "d\n            RETURNING * INTO v_turn;\n            p_outcome := 'active';"
            "\n            \nRETURN QUERY SELECT\n    p_outcome,\n    v_turn.message_id,"
            "\n    v_turn.conversation_id,\n    v_turn.request_id,\n    v_turn.target_but"
            "ler,\n    v_turn.target_kind,\n    v_turn.route_inbox_id,\n    v_turn.cancel"
            "_requested_at,\n    v_turn.cancel_confirmed_at,\n    v_turn.terminal_state,"
            "\n    v_turn.terminal_at;\nRETURN;\n\n        END;\n        "
        ),
        "alembic/versions/core/core_193_dashboard_turn_cancellation.py",
    ),
    (
        "core",
        "public",
        "dashboard_turn_register_session",
        "uuid,uuid,uuid,text,text",
        True,
        (
            "CREATE OR REPLACE FUNCTION public.dashboard_turn_register_session(\n        "
            "    p_message_id uuid,\n            p_session_id uuid,\n            p_reques"
            "t_id uuid,\n            p_butler_name text,\n            p_phase text\n     "
            "   )\n        \nRETURNS TABLE(\n    outcome text,\n    message_id uuid,\n   "
            " conversation_id uuid,\n    request_id uuid,\n    target_butler text,\n    t"
            "arget_kind text,\n    route_inbox_id uuid,\n    cancel_requested_at timestam"
            "ptz,\n    cancel_confirmed_at timestamptz,\n    terminal_state text,\n    te"
            "rminal_at timestamptz\n)\n\n        LANGUAGE plpgsql\n        SECURITY DEFIN"
            "ER\nSET search_path = pg_catalog, pg_temp\n"
        ),
        (
            "\n        #variable_conflict use_column\n        DECLARE\n            v_turn"
            " public.dashboard_conversation_turns%ROWTYPE;\n            v_existing_messag"
            "e_id uuid;\n            p_outcome text;\n        BEGIN\n            IF p_pha"
            "se = 'classification' THEN\n                PERFORM public.dashboard_turn_re"
            "quire_role('butler_switchboard_rw');\n            ELSE\n                PERF"
            "ORM public.dashboard_turn_require_role(\n                    'butler_' || p_"
            "butler_name || '_rw'\n                );\n            END IF;\n            S"
            "ELECT * INTO v_turn\n            FROM public.dashboard_conversation_turns\n "
            "           WHERE message_id = p_message_id\n            FOR UPDATE;\n       "
            "     IF NOT FOUND THEN\n                \nRETURN QUERY SELECT\n    'missing'"
            "::text,\n    NULL::uuid,\n    NULL::uuid,\n    NULL::uuid,\n    NULL::text,"
            "\n    NULL::text,\n    NULL::uuid,\n    NULL::timestamptz,\n    NULL::timest"
            "amptz,\n    NULL::text,\n    NULL::timestamptz;\nRETURN;\n\n            END "
            "IF;\n            IF p_phase NOT IN ('classification', 'route') THEN\n       "
            "         p_outcome := 'conflict';\n                \nRETURN QUERY SELECT\n  "
            "  p_outcome,\n    v_turn.message_id,\n    v_turn.conversation_id,\n    v_tur"
            "n.request_id,\n    v_turn.target_butler,\n    v_turn.target_kind,\n    v_tur"
            "n.route_inbox_id,\n    v_turn.cancel_requested_at,\n    v_turn.cancel_confir"
            "med_at,\n    v_turn.terminal_state,\n    v_turn.terminal_at;\nRETURN;\n\n   "
            "         END IF;\n            IF v_turn.request_id IS NOT NULL AND v_turn.re"
            "quest_id <> p_request_id THEN\n                p_outcome := 'conflict';\n   "
            "             \nRETURN QUERY SELECT\n    p_outcome,\n    v_turn.message_id,\n"
            "    v_turn.conversation_id,\n    v_turn.request_id,\n    v_turn.target_butle"
            "r,\n    v_turn.target_kind,\n    v_turn.route_inbox_id,\n    v_turn.cancel_r"
            "equested_at,\n    v_turn.cancel_confirmed_at,\n    v_turn.terminal_state,\n "
            "   v_turn.terminal_at;\nRETURN;\n\n            END IF;\n            IF p_pha"
            "se = 'classification' AND p_butler_name <> 'switchboard' THEN\n             "
            "   p_outcome := 'conflict';\n                \nRETURN QUERY SELECT\n    p_ou"
            "tcome,\n    v_turn.message_id,\n    v_turn.conversation_id,\n    v_turn.requ"
            "est_id,\n    v_turn.target_butler,\n    v_turn.target_kind,\n    v_turn.rout"
            "e_inbox_id,\n    v_turn.cancel_requested_at,\n    v_turn.cancel_confirmed_at"
            ",\n    v_turn.terminal_state,\n    v_turn.terminal_at;\nRETURN;\n\n         "
            "   END IF;\n            IF p_phase = 'route' AND (\n                v_turn.t"
            "arget_kind <> 'route'\n                OR v_turn.target_butler IS DISTINCT F"
            "ROM p_butler_name\n                OR v_turn.route_inbox_id IS NULL\n       "
            "     ) THEN\n                p_outcome := 'conflict';\n                \nRET"
            "URN QUERY SELECT\n    p_outcome,\n    v_turn.message_id,\n    v_turn.convers"
            "ation_id,\n    v_turn.request_id,\n    v_turn.target_butler,\n    v_turn.tar"
            "get_kind,\n    v_turn.route_inbox_id,\n    v_turn.cancel_requested_at,\n    "
            "v_turn.cancel_confirmed_at,\n    v_turn.terminal_state,\n    v_turn.terminal"
            "_at;\nRETURN;\n\n            END IF;\n            SELECT message_id INTO v_e"
            "xisting_message_id\n            FROM public.dashboard_conversation_turn_sess"
            "ions\n            WHERE session_id = p_session_id;\n            IF FOUND AND"
            " v_existing_message_id <> p_message_id THEN\n                p_outcome := 'c"
            "onflict';\n                \nRETURN QUERY SELECT\n    p_outcome,\n    v_turn"
            ".message_id,\n    v_turn.conversation_id,\n    v_turn.request_id,\n    v_tur"
            "n.target_butler,\n    v_turn.target_kind,\n    v_turn.route_inbox_id,\n    v"
            "_turn.cancel_requested_at,\n    v_turn.cancel_confirmed_at,\n    v_turn.term"
            "inal_state,\n    v_turn.terminal_at;\nRETURN;\n\n            END IF;\n      "
            "      IF v_turn.terminal_state = 'ambiguous' THEN\n                -- Recove"
            "ry has already established that a predecessor may\n                -- still "
            "have acted. Do not let a delayed handler register a\n                -- repl"
            "acement session under that immutable message id.\n                p_outcome "
            ":= 'ambiguous';\n                \nRETURN QUERY SELECT\n    p_outcome,\n    "
            "v_turn.message_id,\n    v_turn.conversation_id,\n    v_turn.request_id,\n   "
            " v_turn.target_butler,\n    v_turn.target_kind,\n    v_turn.route_inbox_id,"
            "\n    v_turn.cancel_requested_at,\n    v_turn.cancel_confirmed_at,\n    v_tu"
            "rn.terminal_state,\n    v_turn.terminal_at;\nRETURN;\n\n            END IF;"
            "\n            IF v_turn.cancel_requested_at IS NULL AND v_turn.terminal_at I"
            "S NOT NULL THEN\n                p_outcome := 'finished';\n                "
            "\nRETURN QUERY SELECT\n    p_outcome,\n    v_turn.message_id,\n    v_turn.co"
            "nversation_id,\n    v_turn.request_id,\n    v_turn.target_butler,\n    v_tur"
            "n.target_kind,\n    v_turn.route_inbox_id,\n    v_turn.cancel_requested_at,"
            "\n    v_turn.cancel_confirmed_at,\n    v_turn.terminal_state,\n    v_turn.te"
            "rminal_at;\nRETURN;\n\n            END IF;\n            UPDATE public.dashbo"
            "ard_conversation_turns\n            SET request_id = coalesce(request_id, p_"
            "request_id),\n                ingress_state = 'accepted',\n                u"
            "pdated_at = now()\n            WHERE message_id = p_message_id\n            "
            "RETURNING * INTO v_turn;\n            INSERT INTO public.dashboard_conversat"
            "ion_turn_sessions (\n                message_id,\n                session_id"
            ",\n                request_id,\n                butler_name,\n              "
            "  phase\n            ) VALUES (\n                p_message_id,\n            "
            "    p_session_id,\n                p_request_id,\n                p_butler_n"
            "ame,\n                p_phase\n            )\n            ON CONFLICT (messa"
            "ge_id, session_id) DO NOTHING;\n            p_outcome := CASE\n             "
            "   WHEN v_turn.cancel_requested_at IS NOT NULL THEN 'cancelled'\n           "
            "     ELSE 'active'\n            END;\n            \nRETURN QUERY SELECT\n   "
            " p_outcome,\n    v_turn.message_id,\n    v_turn.conversation_id,\n    v_turn"
            ".request_id,\n    v_turn.target_butler,\n    v_turn.target_kind,\n    v_turn"
            ".route_inbox_id,\n    v_turn.cancel_requested_at,\n    v_turn.cancel_confirm"
            "ed_at,\n    v_turn.terminal_state,\n    v_turn.terminal_at;\nRETURN;\n\n    "
            "    END;\n        "
        ),
        "alembic/versions/core/core_193_dashboard_turn_cancellation.py",
    ),
    (
        "core",
        "public",
        "dashboard_turn_reconcile_route_recovery",
        "uuid,uuid,uuid",
        True,
        (
            "CREATE OR REPLACE FUNCTION public.dashboard_turn_reconcile_route_recovery(\n"
            "            p_message_id uuid,\n            p_request_id uuid,\n            "
            "p_route_inbox_id uuid\n        )\n        \nRETURNS TABLE(\n    outcome text"
            ",\n    message_id uuid,\n    conversation_id uuid,\n    request_id uuid,\n  "
            "  target_butler text,\n    target_kind text,\n    route_inbox_id uuid,\n    "
            "cancel_requested_at timestamptz,\n    cancel_confirmed_at timestamptz,\n    "
            "terminal_state text,\n    terminal_at timestamptz\n)\n\n        LANGUAGE plp"
            "gsql\n        SECURITY DEFINER\nSET search_path = pg_catalog, pg_temp\n"
        ),
        (
            "\n        #variable_conflict use_column\n        DECLARE\n            v_turn"
            " public.dashboard_conversation_turns%ROWTYPE;\n            p_outcome text;\n"
            "        BEGIN\n            SELECT * INTO v_turn\n            FROM public.das"
            "hboard_conversation_turns\n            WHERE message_id = p_message_id\n    "
            "        FOR UPDATE;\n            IF NOT FOUND THEN\n                \nRETURN"
            " QUERY SELECT\n    'missing'::text,\n    NULL::uuid,\n    NULL::uuid,\n    N"
            "ULL::uuid,\n    NULL::text,\n    NULL::text,\n    NULL::uuid,\n    NULL::tim"
            "estamptz,\n    NULL::timestamptz,\n    NULL::text,\n    NULL::timestamptz;\n"
            "RETURN;\n\n            END IF;\n            IF v_turn.target_kind <> 'route'"
            "\n               OR v_turn.target_butler IS NULL\n               OR v_turn.r"
            "equest_id IS DISTINCT FROM p_request_id\n               OR v_turn.route_inbo"
            "x_id IS DISTINCT FROM p_route_inbox_id THEN\n                p_outcome := 'c"
            "onflict';\n                \nRETURN QUERY SELECT\n    p_outcome,\n    v_turn"
            ".message_id,\n    v_turn.conversation_id,\n    v_turn.request_id,\n    v_tur"
            "n.target_butler,\n    v_turn.target_kind,\n    v_turn.route_inbox_id,\n    v"
            "_turn.cancel_requested_at,\n    v_turn.cancel_confirmed_at,\n    v_turn.term"
            "inal_state,\n    v_turn.terminal_at;\nRETURN;\n\n            END IF;\n      "
            "      PERFORM public.dashboard_turn_require_role(\n                'butler_'"
            " || v_turn.target_butler || '_rw'\n            );\n            IF v_turn.ter"
            "minal_at IS NOT NULL THEN\n                p_outcome := CASE\n              "
            "      WHEN v_turn.terminal_state = 'cancelled' THEN 'cancelled'\n           "
            "         WHEN v_turn.terminal_state = 'ambiguous' THEN 'ambiguous'\n        "
            "            ELSE 'finished'\n                END;\n                \nRETURN "
            "QUERY SELECT\n    p_outcome,\n    v_turn.message_id,\n    v_turn.conversatio"
            "n_id,\n    v_turn.request_id,\n    v_turn.target_butler,\n    v_turn.target_"
            "kind,\n    v_turn.route_inbox_id,\n    v_turn.cancel_requested_at,\n    v_tu"
            "rn.cancel_confirmed_at,\n    v_turn.terminal_state,\n    v_turn.terminal_at;"
            "\nRETURN;\n\n            END IF;\n\n            -- A stale route-inbox lease"
            " is evidence that the predecessor\n            -- cannot be contacted throug"
            "h the queue, not proof that its\n            -- runtime died.  A database/ne"
            "twork partition can leave that\n            -- runtime live after a recovery"
            " worker takes the lease. Preserve\n            -- every predecessor session "
            "for Stop enumeration and surface the\n            -- turn as ambiguous inste"
            "ad of starting a duplicate runtime.\n            UPDATE public.dashboard_con"
            "versation_turns\n            SET terminal_state = 'ambiguous',\n            "
            "    terminal_at = now(),\n                updated_at = now()\n            WH"
            "ERE message_id = p_message_id\n            RETURNING * INTO v_turn;\n\n     "
            "       p_outcome := 'ambiguous';\n            \nRETURN QUERY SELECT\n    p_o"
            "utcome,\n    v_turn.message_id,\n    v_turn.conversation_id,\n    v_turn.req"
            "uest_id,\n    v_turn.target_butler,\n    v_turn.target_kind,\n    v_turn.rou"
            "te_inbox_id,\n    v_turn.cancel_requested_at,\n    v_turn.cancel_confirmed_a"
            "t,\n    v_turn.terminal_state,\n    v_turn.terminal_at;\nRETURN;\n\n        "
            "END;\n        "
        ),
        "alembic/versions/core/core_193_dashboard_turn_cancellation.py",
    ),
    (
        "core",
        "public",
        "dashboard_turn_claim_invoke",
        "uuid,uuid",
        True,
        (
            "CREATE OR REPLACE FUNCTION public.dashboard_turn_claim_invoke(\n            "
            "p_message_id uuid,\n            p_session_id uuid\n        )\n        \nRETU"
            "RNS TABLE(\n    outcome text,\n    message_id uuid,\n    conversation_id uui"
            "d,\n    request_id uuid,\n    target_butler text,\n    target_kind text,\n  "
            "  route_inbox_id uuid,\n    cancel_requested_at timestamptz,\n    cancel_con"
            "firmed_at timestamptz,\n    terminal_state text,\n    terminal_at timestampt"
            "z\n)\n\n        LANGUAGE plpgsql\n        SECURITY DEFINER\nSET search_path "
            "= pg_catalog, pg_temp\n"
        ),
        (
            "\n        #variable_conflict use_column\n        DECLARE\n            v_turn"
            " public.dashboard_conversation_turns%ROWTYPE;\n            v_butler_name tex"
            "t;\n            p_outcome text;\n        BEGIN\n            SELECT * INTO v_"
            "turn\n            FROM public.dashboard_conversation_turns\n            WHER"
            "E message_id = p_message_id\n            FOR UPDATE;\n            IF NOT FOU"
            "ND THEN\n                \nRETURN QUERY SELECT\n    'missing'::text,\n    NU"
            "LL::uuid,\n    NULL::uuid,\n    NULL::uuid,\n    NULL::text,\n    NULL::text"
            ",\n    NULL::uuid,\n    NULL::timestamptz,\n    NULL::timestamptz,\n    NULL"
            "::text,\n    NULL::timestamptz;\nRETURN;\n\n            END IF;\n           "
            " SELECT butler_name INTO v_butler_name\n            FROM public.dashboard_co"
            "nversation_turn_sessions\n            WHERE message_id = p_message_id\n     "
            "         AND session_id = p_session_id\n              AND completed_at IS NU"
            "LL;\n            IF NOT FOUND THEN\n                p_outcome := 'conflict';"
            "\n                \nRETURN QUERY SELECT\n    p_outcome,\n    v_turn.message_"
            "id,\n    v_turn.conversation_id,\n    v_turn.request_id,\n    v_turn.target_"
            "butler,\n    v_turn.target_kind,\n    v_turn.route_inbox_id,\n    v_turn.can"
            "cel_requested_at,\n    v_turn.cancel_confirmed_at,\n    v_turn.terminal_stat"
            "e,\n    v_turn.terminal_at;\nRETURN;\n\n            END IF;\n            PER"
            "FORM public.dashboard_turn_require_role('butler_' || v_butler_name || '_rw')"
            ";\n            IF v_turn.cancel_requested_at IS NOT NULL THEN\n             "
            "   p_outcome := 'cancelled';\n                \nRETURN QUERY SELECT\n    p_o"
            "utcome,\n    v_turn.message_id,\n    v_turn.conversation_id,\n    v_turn.req"
            "uest_id,\n    v_turn.target_butler,\n    v_turn.target_kind,\n    v_turn.rou"
            "te_inbox_id,\n    v_turn.cancel_requested_at,\n    v_turn.cancel_confirmed_a"
            "t,\n    v_turn.terminal_state,\n    v_turn.terminal_at;\nRETURN;\n\n        "
            "    END IF;\n            IF v_turn.terminal_at IS NOT NULL THEN\n           "
            "     p_outcome := CASE\n                    WHEN v_turn.terminal_state = 'am"
            "biguous' THEN 'ambiguous'\n                    ELSE 'finished'\n            "
            "    END;\n                \nRETURN QUERY SELECT\n    p_outcome,\n    v_turn."
            "message_id,\n    v_turn.conversation_id,\n    v_turn.request_id,\n    v_turn"
            ".target_butler,\n    v_turn.target_kind,\n    v_turn.route_inbox_id,\n    v_"
            "turn.cancel_requested_at,\n    v_turn.cancel_confirmed_at,\n    v_turn.termi"
            "nal_state,\n    v_turn.terminal_at;\nRETURN;\n\n            END IF;\n       "
            "     UPDATE public.dashboard_conversation_turn_sessions\n            SET inv"
            "oke_claimed_at = coalesce(invoke_claimed_at, now()),\n                invoke"
            "_active = TRUE\n            WHERE message_id = p_message_id\n              A"
            "ND session_id = p_session_id;\n            p_outcome := 'active';\n         "
            "   \nRETURN QUERY SELECT\n    p_outcome,\n    v_turn.message_id,\n    v_turn"
            ".conversation_id,\n    v_turn.request_id,\n    v_turn.target_butler,\n    v_"
            "turn.target_kind,\n    v_turn.route_inbox_id,\n    v_turn.cancel_requested_a"
            "t,\n    v_turn.cancel_confirmed_at,\n    v_turn.terminal_state,\n    v_turn."
            "terminal_at;\nRETURN;\n\n        END;\n        "
        ),
        "alembic/versions/core/core_193_dashboard_turn_cancellation.py",
    ),
    (
        "core",
        "public",
        "dashboard_turn_release_invoke",
        "uuid,uuid",
        True,
        (
            "CREATE OR REPLACE FUNCTION public.dashboard_turn_release_invoke(\n          "
            "  p_message_id uuid,\n            p_session_id uuid\n        )\n        \nRE"
            "TURNS TABLE(\n    outcome text,\n    message_id uuid,\n    conversation_id u"
            "uid,\n    request_id uuid,\n    target_butler text,\n    target_kind text,\n"
            "    route_inbox_id uuid,\n    cancel_requested_at timestamptz,\n    cancel_c"
            "onfirmed_at timestamptz,\n    terminal_state text,\n    terminal_at timestam"
            "ptz\n)\n\n        LANGUAGE plpgsql\n        SECURITY DEFINER\nSET search_pat"
            "h = pg_catalog, pg_temp\n"
        ),
        (
            "\n        #variable_conflict use_column\n        DECLARE\n            v_turn"
            " public.dashboard_conversation_turns%ROWTYPE;\n            v_butler_name tex"
            "t;\n            p_outcome text;\n        BEGIN\n            SELECT * INTO v_"
            "turn\n            FROM public.dashboard_conversation_turns\n            WHER"
            "E message_id = p_message_id\n            FOR UPDATE;\n            IF NOT FOU"
            "ND THEN\n                \nRETURN QUERY SELECT\n    'missing'::text,\n    NU"
            "LL::uuid,\n    NULL::uuid,\n    NULL::uuid,\n    NULL::text,\n    NULL::text"
            ",\n    NULL::uuid,\n    NULL::timestamptz,\n    NULL::timestamptz,\n    NULL"
            "::text,\n    NULL::timestamptz;\nRETURN;\n\n            END IF;\n           "
            " SELECT butler_name INTO v_butler_name\n            FROM public.dashboard_co"
            "nversation_turn_sessions\n            WHERE message_id = p_message_id\n     "
            "         AND session_id = p_session_id\n              AND completed_at IS NU"
            "LL;\n            IF NOT FOUND THEN\n                p_outcome := 'conflict';"
            "\n                \nRETURN QUERY SELECT\n    p_outcome,\n    v_turn.message_"
            "id,\n    v_turn.conversation_id,\n    v_turn.request_id,\n    v_turn.target_"
            "butler,\n    v_turn.target_kind,\n    v_turn.route_inbox_id,\n    v_turn.can"
            "cel_requested_at,\n    v_turn.cancel_confirmed_at,\n    v_turn.terminal_stat"
            "e,\n    v_turn.terminal_at;\nRETURN;\n\n            END IF;\n            PER"
            "FORM public.dashboard_turn_require_role('butler_' || v_butler_name || '_rw')"
            ";\n            UPDATE public.dashboard_conversation_turn_sessions\n         "
            "   SET invoke_active = FALSE\n            WHERE message_id = p_message_id\n "
            "             AND session_id = p_session_id;\n            p_outcome := CASE\n"
            "                WHEN v_turn.terminal_state = 'cancelled' THEN 'cancelled'\n "
            "               WHEN v_turn.terminal_state = 'ambiguous' THEN 'ambiguous'\n  "
            "              WHEN v_turn.terminal_at IS NOT NULL THEN 'finished'\n         "
            "       WHEN v_turn.cancel_requested_at IS NOT NULL THEN 'cancelling'\n      "
            "          ELSE 'active'\n            END;\n            \nRETURN QUERY SELECT"
            "\n    p_outcome,\n    v_turn.message_id,\n    v_turn.conversation_id,\n    v"
            "_turn.request_id,\n    v_turn.target_butler,\n    v_turn.target_kind,\n    v"
            "_turn.route_inbox_id,\n    v_turn.cancel_requested_at,\n    v_turn.cancel_co"
            "nfirmed_at,\n    v_turn.terminal_state,\n    v_turn.terminal_at;\nRETURN;\n"
            "\n        END;\n        "
        ),
        "alembic/versions/core/core_193_dashboard_turn_cancellation.py",
    ),
    (
        "core",
        "public",
        "dashboard_turn_complete_session",
        "uuid,uuid,boolean",
        True,
        (
            "CREATE OR REPLACE FUNCTION public.dashboard_turn_complete_session(\n        "
            "    p_message_id uuid,\n            p_session_id uuid,\n            p_succes"
            "s boolean\n        )\n        \nRETURNS TABLE(\n    outcome text,\n    messa"
            "ge_id uuid,\n    conversation_id uuid,\n    request_id uuid,\n    target_but"
            "ler text,\n    target_kind text,\n    route_inbox_id uuid,\n    cancel_reque"
            "sted_at timestamptz,\n    cancel_confirmed_at timestamptz,\n    terminal_sta"
            "te text,\n    terminal_at timestamptz\n)\n\n        LANGUAGE plpgsql\n      "
            "  SECURITY DEFINER\nSET search_path = pg_catalog, pg_temp\n"
        ),
        (
            "\n        #variable_conflict use_column\n        DECLARE\n            v_turn"
            " public.dashboard_conversation_turns%ROWTYPE;\n            v_phase text;\n  "
            "          v_butler_name text;\n            v_invoke_claimed_at timestamptz;"
            "\n            v_cancel_acknowledged_at timestamptz;\n            v_has_other"
            "_open_session boolean;\n            v_pending_terminal_state text;\n        "
            "    p_outcome text;\n        BEGIN\n            SELECT * INTO v_turn\n      "
            "      FROM public.dashboard_conversation_turns\n            WHERE message_id"
            " = p_message_id\n            FOR UPDATE;\n            IF NOT FOUND THEN\n   "
            "             \nRETURN QUERY SELECT\n    'missing'::text,\n    NULL::uuid,\n "
            "   NULL::uuid,\n    NULL::uuid,\n    NULL::text,\n    NULL::text,\n    NULL:"
            ":uuid,\n    NULL::timestamptz,\n    NULL::timestamptz,\n    NULL::text,\n   "
            " NULL::timestamptz;\nRETURN;\n\n            END IF;\n            SELECT phas"
            "e, butler_name, invoke_claimed_at, cancel_acknowledged_at\n            INTO "
            "v_phase, v_butler_name, v_invoke_claimed_at, v_cancel_acknowledged_at\n     "
            "       FROM public.dashboard_conversation_turn_sessions\n            WHERE m"
            "essage_id = p_message_id\n              AND session_id = p_session_id;\n    "
            "        IF NOT FOUND THEN\n                p_outcome := 'conflict';\n       "
            "         \nRETURN QUERY SELECT\n    p_outcome,\n    v_turn.message_id,\n    "
            "v_turn.conversation_id,\n    v_turn.request_id,\n    v_turn.target_butler,\n"
            "    v_turn.target_kind,\n    v_turn.route_inbox_id,\n    v_turn.cancel_reque"
            "sted_at,\n    v_turn.cancel_confirmed_at,\n    v_turn.terminal_state,\n    v"
            "_turn.terminal_at;\nRETURN;\n\n            END IF;\n            PERFORM publ"
            "ic.dashboard_turn_require_role('butler_' || v_butler_name || '_rw');\n      "
            "      UPDATE public.dashboard_conversation_turn_sessions\n            SET co"
            "mpleted_at = coalesce(completed_at, now()),\n                invoke_active ="
            " FALSE,\n                success = coalesce(success, p_success)\n           "
            " WHERE message_id = p_message_id\n              AND session_id = p_session_i"
            "d;\n            SELECT EXISTS (\n                SELECT 1\n                F"
            "ROM public.dashboard_conversation_turn_sessions\n                WHERE messa"
            "ge_id = p_message_id\n                  AND completed_at IS NULL\n          "
            "  ) INTO v_has_other_open_session;\n            SELECT pending_terminal_stat"
            "e INTO v_pending_terminal_state\n            FROM public.dashboard_conversat"
            "ion_turns\n            WHERE message_id = p_message_id;\n\n            -- Ca"
            "ncellation intent is not cancellation confirmation.  A runtime\n            "
            "-- that was already invoking can still finish if its MCP cancel call\n      "
            "      -- was unreachable, so retain its actual success/failure here.\n      "
            "      IF v_turn.terminal_at IS NULL\n                AND v_turn.cancel_reque"
            "sted_at IS NOT NULL\n                AND NOT v_has_other_open_session\n     "
            "           AND (\n                    v_invoke_claimed_at IS NULL\n         "
            "           OR v_cancel_acknowledged_at IS NOT NULL\n                ) THEN\n"
            "                -- Either the Stop arrived before this session crossed invok"
            "e,\n                -- or this exact invoking session durably acknowledged o"
            "wner\n                -- cancellation before its final result was written.\n"
            "                UPDATE public.dashboard_conversation_turns\n                "
            "SET cancel_confirmed_at = coalesce(cancel_confirmed_at, now()),\n           "
            "         terminal_state = 'cancelled',\n                    terminal_at = no"
            "w(),\n                    updated_at = now()\n                WHERE message_"
            "id = p_message_id\n                RETURNING * INTO v_turn;\n            ELS"
            "IF v_turn.terminal_at IS NULL\n                AND v_phase = 'route'\n      "
            "          AND v_has_other_open_session THEN\n                -- The target c"
            "ompleted before an overlapping classifier or\n                -- recovery at"
            "tempt. Hold its concrete outcome until the final\n                -- open se"
            "ssion closes; no peer may be hidden by a terminal row.\n                UPDA"
            "TE public.dashboard_conversation_turns\n                SET pending_terminal"
            "_state = CASE\n                        WHEN p_success THEN 'completed'\n    "
            "                    ELSE 'failed'\n                    END,\n               "
            "     pending_terminal_at = now(),\n                    updated_at = now()\n "
            "               WHERE message_id = p_message_id\n                RETURNING * "
            "INTO v_turn;\n            ELSIF v_turn.terminal_at IS NULL\n                "
            "AND NOT v_has_other_open_session\n                AND v_pending_terminal_sta"
            "te IS NOT NULL THEN\n                UPDATE public.dashboard_conversation_tu"
            "rns\n                SET terminal_state = v_pending_terminal_state,\n       "
            "             terminal_at = now(),\n                    pending_terminal_stat"
            "e = NULL,\n                    pending_terminal_at = NULL,\n                "
            "    updated_at = now()\n                WHERE message_id = p_message_id\n   "
            "             RETURNING * INTO v_turn;\n            ELSIF v_turn.terminal_at "
            "IS NULL\n                AND NOT v_has_other_open_session\n                A"
            "ND v_phase = 'route' THEN\n                UPDATE public.dashboard_conversat"
            "ion_turns\n                SET terminal_state = CASE WHEN p_success THEN 'co"
            "mpleted' ELSE 'failed' END,\n                    terminal_at = now(),\n     "
            "               updated_at = now()\n                WHERE message_id = p_mess"
            "age_id\n                RETURNING * INTO v_turn;\n            ELSIF v_turn.t"
            "erminal_at IS NULL\n                AND NOT v_has_other_open_session\n      "
            "          AND v_phase = 'classification'\n                AND v_turn.cancel_"
            "requested_at IS NOT NULL\n                AND v_turn.target_kind IS NULL THE"
            "N\n                -- Stop landed after a classifier invocation ended but be"
            "fore\n                -- it could create any target action. That invocation "
            "was not\n                -- killed (request_cancel reports ``settling`` in t"
            "his race),\n                -- so record its actual non-delivery as finished"
            "/failed rather\n                -- than fabricating a confirmed cancellation"
            " or leaving an\n                -- unresolvable open turn forever.\n        "
            "        UPDATE public.dashboard_conversation_turns\n                SET term"
            "inal_state = 'failed',\n                    terminal_at = now(),\n          "
            "          updated_at = now()\n                WHERE message_id = p_message_i"
            "d\n                RETURNING * INTO v_turn;\n            END IF;\n          "
            "  p_outcome := CASE\n                WHEN v_turn.terminal_state = 'cancelled"
            "' THEN 'cancelled'\n                WHEN v_turn.terminal_state = 'ambiguous'"
            " THEN 'ambiguous'\n                WHEN v_turn.terminal_at IS NOT NULL THEN "
            "'finished'\n                WHEN v_turn.cancel_requested_at IS NOT NULL THEN"
            " 'cancelling'\n                ELSE 'active'\n            END;\n            "
            "\nRETURN QUERY SELECT\n    p_outcome,\n    v_turn.message_id,\n    v_turn.co"
            "nversation_id,\n    v_turn.request_id,\n    v_turn.target_butler,\n    v_tur"
            "n.target_kind,\n    v_turn.route_inbox_id,\n    v_turn.cancel_requested_at,"
            "\n    v_turn.cancel_confirmed_at,\n    v_turn.terminal_state,\n    v_turn.te"
            "rminal_at;\nRETURN;\n\n        END;\n        "
        ),
        "alembic/versions/core/core_193_dashboard_turn_cancellation.py",
    ),
    (
        "core",
        "public",
        "dashboard_turn_claim_external_action",
        "uuid,uuid,text",
        True,
        (
            "CREATE OR REPLACE FUNCTION public.dashboard_turn_claim_external_action(\n   "
            "         p_message_id uuid,\n            p_request_id uuid,\n            p_k"
            "ind text\n        )\n        \nRETURNS TABLE(\n    outcome text,\n    messag"
            "e_id uuid,\n    conversation_id uuid,\n    request_id uuid,\n    target_butl"
            "er text,\n    target_kind text,\n    route_inbox_id uuid,\n    cancel_reques"
            "ted_at timestamptz,\n    cancel_confirmed_at timestamptz,\n    terminal_stat"
            "e text,\n    terminal_at timestamptz\n)\n\n        LANGUAGE plpgsql\n       "
            " SECURITY DEFINER\nSET search_path = pg_catalog, pg_temp\n"
        ),
        (
            "\n        #variable_conflict use_column\n        DECLARE\n            v_turn"
            " public.dashboard_conversation_turns%ROWTYPE;\n            v_target_butler t"
            "ext;\n            p_outcome text;\n        BEGIN\n            PERFORM public"
            ".dashboard_turn_require_role('butler_switchboard_rw');\n            IF p_kin"
            "d NOT IN ('bug_report', 'dead_letter') THEN\n                RAISE EXCEPTION"
            " 'invalid dashboard terminal action: %', p_kind\n                    USING E"
            "RRCODE = '22023';\n            END IF;\n            SELECT * INTO v_turn\n  "
            "          FROM public.dashboard_conversation_turns\n            WHERE messag"
            "e_id = p_message_id\n            FOR UPDATE;\n            IF NOT FOUND THEN"
            "\n                \nRETURN QUERY SELECT\n    'missing'::text,\n    NULL::uui"
            "d,\n    NULL::uuid,\n    NULL::uuid,\n    NULL::text,\n    NULL::text,\n    "
            "NULL::uuid,\n    NULL::timestamptz,\n    NULL::timestamptz,\n    NULL::text,"
            "\n    NULL::timestamptz;\nRETURN;\n\n            END IF;\n            IF v_t"
            "urn.cancel_requested_at IS NOT NULL THEN\n                p_outcome := 'canc"
            "elled';\n                \nRETURN QUERY SELECT\n    p_outcome,\n    v_turn.m"
            "essage_id,\n    v_turn.conversation_id,\n    v_turn.request_id,\n    v_turn."
            "target_butler,\n    v_turn.target_kind,\n    v_turn.route_inbox_id,\n    v_t"
            "urn.cancel_requested_at,\n    v_turn.cancel_confirmed_at,\n    v_turn.termin"
            "al_state,\n    v_turn.terminal_at;\nRETURN;\n\n            END IF;\n        "
            "    IF v_turn.terminal_at IS NOT NULL THEN\n                p_outcome := 'fi"
            "nished';\n                \nRETURN QUERY SELECT\n    p_outcome,\n    v_turn."
            "message_id,\n    v_turn.conversation_id,\n    v_turn.request_id,\n    v_turn"
            ".target_butler,\n    v_turn.target_kind,\n    v_turn.route_inbox_id,\n    v_"
            "turn.cancel_requested_at,\n    v_turn.cancel_confirmed_at,\n    v_turn.termi"
            "nal_state,\n    v_turn.terminal_at;\nRETURN;\n\n            END IF;\n       "
            "     IF v_turn.request_id IS NOT NULL AND v_turn.request_id <> p_request_id "
            "THEN\n                p_outcome := 'conflict';\n                \nRETURN QUE"
            "RY SELECT\n    p_outcome,\n    v_turn.message_id,\n    v_turn.conversation_i"
            "d,\n    v_turn.request_id,\n    v_turn.target_butler,\n    v_turn.target_kin"
            "d,\n    v_turn.route_inbox_id,\n    v_turn.cancel_requested_at,\n    v_turn."
            "cancel_confirmed_at,\n    v_turn.terminal_state,\n    v_turn.terminal_at;\nR"
            "ETURN;\n\n            END IF;\n            IF v_turn.target_kind IS NOT NULL"
            " THEN\n                IF v_turn.target_kind = p_kind\n                   AN"
            "D v_turn.external_action_claimed_at IS NOT NULL THEN\n                    --"
            " The side effect was reserved but not durably completed.\n                  "
            "  -- A crashed worker must never make a later caller report it\n            "
            "        -- as filed; a dedicated reconciliation/outbox owns recovery.\n     "
            "               p_outcome := 'external_action_in_progress';\n                "
            "ELSE\n                    p_outcome := 'conflict';\n                END IF;"
            "\n                \nRETURN QUERY SELECT\n    p_outcome,\n    v_turn.message_"
            "id,\n    v_turn.conversation_id,\n    v_turn.request_id,\n    v_turn.target_"
            "butler,\n    v_turn.target_kind,\n    v_turn.route_inbox_id,\n    v_turn.can"
            "cel_requested_at,\n    v_turn.cancel_confirmed_at,\n    v_turn.terminal_stat"
            "e,\n    v_turn.terminal_at;\nRETURN;\n\n            END IF;\n            v_t"
            "arget_butler := CASE\n                WHEN p_kind = 'bug_report' THEN 'qa'\n"
            "                ELSE 'switchboard'\n            END;\n            UPDATE pub"
            "lic.dashboard_conversation_turns\n            SET request_id = coalesce(requ"
            "est_id, p_request_id),\n                ingress_state = 'accepted',\n       "
            "         target_butler = v_target_butler,\n                target_kind = p_k"
            "ind,\n                target_claimed_at = now(),\n                external_a"
            "ction_claimed_at = now(),\n                updated_at = now()\n            W"
            "HERE message_id = p_message_id\n            RETURNING * INTO v_turn;\n      "
            "      p_outcome := 'claimed';\n            \nRETURN QUERY SELECT\n    p_outc"
            "ome,\n    v_turn.message_id,\n    v_turn.conversation_id,\n    v_turn.reques"
            "t_id,\n    v_turn.target_butler,\n    v_turn.target_kind,\n    v_turn.route_"
            "inbox_id,\n    v_turn.cancel_requested_at,\n    v_turn.cancel_confirmed_at,"
            "\n    v_turn.terminal_state,\n    v_turn.terminal_at;\nRETURN;\n\n        EN"
            "D;\n        "
        ),
        "alembic/versions/core/core_193_dashboard_turn_cancellation.py",
    ),
    (
        "core",
        "public",
        "dashboard_turn_mark_terminal",
        "uuid,text",
        True,
        (
            "CREATE OR REPLACE FUNCTION public.dashboard_turn_mark_terminal(\n           "
            " p_message_id uuid,\n            p_state text\n        )\n        \nRETURNS "
            "TABLE(\n    outcome text,\n    message_id uuid,\n    conversation_id uuid,\n"
            "    request_id uuid,\n    target_butler text,\n    target_kind text,\n    ro"
            "ute_inbox_id uuid,\n    cancel_requested_at timestamptz,\n    cancel_confirm"
            "ed_at timestamptz,\n    terminal_state text,\n    terminal_at timestamptz\n)"
            "\n\n        LANGUAGE plpgsql\n        SECURITY DEFINER\nSET search_path = pg"
            "_catalog, pg_temp\n"
        ),
        (
            "\n        #variable_conflict use_column\n        DECLARE\n            v_turn"
            " public.dashboard_conversation_turns%ROWTYPE;\n            v_has_open_sessio"
            "n boolean;\n            p_outcome text;\n        BEGIN\n            -- Cance"
            "llation has a separate acknowledgement protocol.  A target\n            -- r"
            "untime must not be able to bypass the other live sessions by\n            --"
            " directly writing a terminal cancelled state.\n            IF p_state NOT IN"
            " ('completed', 'failed') THEN\n                RAISE EXCEPTION 'invalid dash"
            "board turn terminal state: %', p_state\n                    USING ERRCODE = "
            "'22023';\n            END IF;\n            SELECT * INTO v_turn\n           "
            " FROM public.dashboard_conversation_turns\n            WHERE message_id = p_"
            "message_id\n            FOR UPDATE;\n            IF NOT FOUND THEN\n        "
            "        \nRETURN QUERY SELECT\n    'missing'::text,\n    NULL::uuid,\n    NU"
            "LL::uuid,\n    NULL::uuid,\n    NULL::text,\n    NULL::text,\n    NULL::uuid"
            ",\n    NULL::timestamptz,\n    NULL::timestamptz,\n    NULL::text,\n    NULL"
            "::timestamptz;\nRETURN;\n\n            END IF;\n            IF v_turn.target"
            "_kind = 'route' AND v_turn.target_butler IS NOT NULL THEN\n                P"
            "ERFORM public.dashboard_turn_require_role(\n                    'butler_' ||"
            " v_turn.target_butler || '_rw'\n                );\n            ELSE\n      "
            "          -- Classification and terminal-action lanes are Switchboard-owned."
            "\n                PERFORM public.dashboard_turn_require_role('butler_switchb"
            "oard_rw');\n            END IF;\n            IF v_turn.terminal_at IS NOT NU"
            "LL THEN\n                p_outcome := CASE\n                    WHEN v_turn."
            "terminal_state = 'cancelled' THEN 'cancelled'\n                    WHEN v_tu"
            "rn.terminal_state = 'ambiguous' THEN 'ambiguous'\n                    ELSE '"
            "finished'\n                END;\n                \nRETURN QUERY SELECT\n    "
            "p_outcome,\n    v_turn.message_id,\n    v_turn.conversation_id,\n    v_turn."
            "request_id,\n    v_turn.target_butler,\n    v_turn.target_kind,\n    v_turn."
            "route_inbox_id,\n    v_turn.cancel_requested_at,\n    v_turn.cancel_confirme"
            "d_at,\n    v_turn.terminal_state,\n    v_turn.terminal_at;\nRETURN;\n\n     "
            "       END IF;\n            SELECT EXISTS (\n                SELECT 1\n     "
            "           FROM public.dashboard_conversation_turn_sessions\n               "
            " WHERE message_id = p_message_id\n                  AND completed_at IS NULL"
            "\n            ) INTO v_has_open_session;\n            IF p_state <> 'cancell"
            "ed' AND v_has_open_session THEN\n                UPDATE public.dashboard_con"
            "versation_turns\n                SET pending_terminal_state = p_state,\n    "
            "                pending_terminal_at = now(),\n                    updated_at"
            " = now()\n                WHERE message_id = p_message_id\n                R"
            "ETURNING * INTO v_turn;\n                p_outcome := 'pending';\n          "
            "      \nRETURN QUERY SELECT\n    p_outcome,\n    v_turn.message_id,\n    v_t"
            "urn.conversation_id,\n    v_turn.request_id,\n    v_turn.target_butler,\n   "
            " v_turn.target_kind,\n    v_turn.route_inbox_id,\n    v_turn.cancel_requeste"
            "d_at,\n    v_turn.cancel_confirmed_at,\n    v_turn.terminal_state,\n    v_tu"
            "rn.terminal_at;\nRETURN;\n\n            END IF;\n            UPDATE public.d"
            "ashboard_conversation_turns\n            SET terminal_state = CASE\n        "
            "            WHEN cancel_requested_at IS NOT NULL THEN 'cancelled'\n         "
            "           ELSE p_state\n                END,\n                terminal_at ="
            " now(),\n                updated_at = now()\n            WHERE message_id = "
            "p_message_id\n            RETURNING * INTO v_turn;\n            p_outcome :="
            " CASE\n                WHEN v_turn.terminal_state = 'cancelled' THEN 'cancel"
            "led'\n                WHEN v_turn.terminal_state = 'ambiguous' THEN 'ambiguo"
            "us'\n                ELSE 'finished'\n            END;\n            \nRETURN"
            " QUERY SELECT\n    p_outcome,\n    v_turn.message_id,\n    v_turn.conversati"
            "on_id,\n    v_turn.request_id,\n    v_turn.target_butler,\n    v_turn.target"
            "_kind,\n    v_turn.route_inbox_id,\n    v_turn.cancel_requested_at,\n    v_t"
            "urn.cancel_confirmed_at,\n    v_turn.terminal_state,\n    v_turn.terminal_at"
            ";\nRETURN;\n\n        END;\n        "
        ),
        "alembic/versions/core/core_193_dashboard_turn_cancellation.py",
    ),
    (
        "core",
        "public",
        "dashboard_turn_request_cancel",
        "uuid",
        True,
        (
            "CREATE OR REPLACE FUNCTION public.dashboard_turn_request_cancel(\n          "
            "  p_message_id uuid\n        )\n        \nRETURNS TABLE(\n    outcome text,"
            "\n    message_id uuid,\n    conversation_id uuid,\n    request_id uuid,\n   "
            " target_butler text,\n    target_kind text,\n    route_inbox_id uuid,\n    c"
            "ancel_requested_at timestamptz,\n    cancel_confirmed_at timestamptz,\n    t"
            "erminal_state text,\n    terminal_at timestamptz\n)\n\n        LANGUAGE plpg"
            "sql\n        SECURITY DEFINER\nSET search_path = pg_catalog, pg_temp\n"
        ),
        (
            "\n        #variable_conflict use_column\n        DECLARE\n            v_turn"
            " public.dashboard_conversation_turns%ROWTYPE;\n            v_has_active_invo"
            "cation boolean;\n            v_has_prior_invocation boolean;\n            v_"
            "turn_is_ambiguous boolean;\n            p_outcome text;\n        BEGIN\n    "
            "        SELECT * INTO v_turn\n            FROM public.dashboard_conversation"
            "_turns\n            WHERE message_id = p_message_id\n            FOR UPDATE;"
            "\n            IF NOT FOUND THEN\n                \nRETURN QUERY SELECT\n    "
            "'missing'::text,\n    NULL::uuid,\n    NULL::uuid,\n    NULL::uuid,\n    NUL"
            "L::text,\n    NULL::text,\n    NULL::uuid,\n    NULL::timestamptz,\n    NULL"
            "::timestamptz,\n    NULL::text,\n    NULL::timestamptz;\nRETURN;\n\n        "
            "    END IF;\n            IF v_turn.terminal_at IS NOT NULL\n               A"
            "ND v_turn.terminal_state IS DISTINCT FROM 'ambiguous' THEN\n                "
            "p_outcome := CASE\n                    WHEN v_turn.terminal_state = 'cancell"
            "ed' THEN 'cancelled'\n                    ELSE 'finished'\n                E"
            "ND;\n                \nRETURN QUERY SELECT\n    p_outcome,\n    v_turn.messa"
            "ge_id,\n    v_turn.conversation_id,\n    v_turn.request_id,\n    v_turn.targ"
            "et_butler,\n    v_turn.target_kind,\n    v_turn.route_inbox_id,\n    v_turn."
            "cancel_requested_at,\n    v_turn.cancel_confirmed_at,\n    v_turn.terminal_s"
            "tate,\n    v_turn.terminal_at;\nRETURN;\n\n            END IF;\n            "
            "v_turn_is_ambiguous := v_turn.terminal_state = 'ambiguous';\n            IF "
            "v_turn.target_kind IN ('bug_report', 'dead_letter')\n               AND v_tu"
            "rn.external_action_claimed_at IS NOT NULL THEN\n                -- Do not wr"
            "ite cancellation intent over an externally-visible\n                -- actio"
            "n reservation. Its outcome is not yet known, so Stop\n                -- mus"
            "t remain explicitly unconfirmed.\n                p_outcome := 'external_act"
            "ion_in_progress';\n                \nRETURN QUERY SELECT\n    p_outcome,\n  "
            "  v_turn.message_id,\n    v_turn.conversation_id,\n    v_turn.request_id,\n "
            "   v_turn.target_butler,\n    v_turn.target_kind,\n    v_turn.route_inbox_id"
            ",\n    v_turn.cancel_requested_at,\n    v_turn.cancel_confirmed_at,\n    v_t"
            "urn.terminal_state,\n    v_turn.terminal_at;\nRETURN;\n\n            END IF;"
            "\n            UPDATE public.dashboard_conversation_turns\n            SET ca"
            "ncel_requested_at = coalesce(cancel_requested_at, now()),\n                u"
            "pdated_at = now()\n            WHERE message_id = p_message_id\n            "
            "RETURNING * INTO v_turn;\n            SELECT EXISTS (\n                SELEC"
            "T 1\n                FROM public.dashboard_conversation_turn_sessions\n     "
            "           WHERE message_id = p_message_id\n                  AND completed_"
            "at IS NULL\n                  AND invoke_active IS TRUE\n            ) INTO "
            "v_has_active_invocation;\n            SELECT EXISTS (\n                SELEC"
            "T 1\n                FROM public.dashboard_conversation_turn_sessions\n     "
            "           WHERE message_id = p_message_id\n                  AND invoke_cla"
            "imed_at IS NOT NULL\n            ) INTO v_has_prior_invocation;\n           "
            " IF v_turn_is_ambiguous THEN\n                -- Keep the no-replay terminal"
            " fact, but still address every\n                -- exact active predecessor "
            "through the normal Stop API.\n                p_outcome := CASE\n           "
            "         WHEN v_has_active_invocation THEN 'cancelling'\n                   "
            " ELSE 'ambiguous'\n                END;\n                \nRETURN QUERY SELE"
            "CT\n    p_outcome,\n    v_turn.message_id,\n    v_turn.conversation_id,\n   "
            " v_turn.request_id,\n    v_turn.target_butler,\n    v_turn.target_kind,\n   "
            " v_turn.route_inbox_id,\n    v_turn.cancel_requested_at,\n    v_turn.cancel_"
            "confirmed_at,\n    v_turn.terminal_state,\n    v_turn.terminal_at;\nRETURN;"
            "\n\n            END IF;\n            IF NOT v_has_active_invocation\n       "
            "         AND NOT v_has_prior_invocation\n                AND v_turn.ingress_"
            "state = 'submitting' THEN\n                -- The generator has claimed its "
            "one outbound Switchboard\n                -- submission. A Stop now prevents"
            " every later runtime\n                -- boundary, but cannot truthfully cla"
            "im that this already\n                -- crossing external call was stopped."
            "\n                p_outcome := 'settling';\n            ELSIF NOT v_has_acti"
            "ve_invocation AND NOT v_has_prior_invocation THEN\n                UPDATE pu"
            "blic.dashboard_conversation_turns\n                SET cancel_confirmed_at ="
            " coalesce(cancel_confirmed_at, now()),\n                    terminal_state ="
            " 'cancelled',\n                    terminal_at = now(),\n                   "
            " updated_at = now()\n                WHERE message_id = p_message_id\n      "
            "          RETURNING * INTO v_turn;\n                p_outcome := 'cancelled'"
            ";\n            ELSIF NOT v_has_active_invocation THEN\n                -- An"
            " invocation has already ended but the logical session has\n                -"
            "- not yet recorded its actual result.  Stop now gates all\n                -"
            "- future attempts, but must not be mislabeled as a kill.\n                p_"
            "outcome := 'settling';\n            ELSE\n                p_outcome := 'canc"
            "elling';\n            END IF;\n            \nRETURN QUERY SELECT\n    p_outc"
            "ome,\n    v_turn.message_id,\n    v_turn.conversation_id,\n    v_turn.reques"
            "t_id,\n    v_turn.target_butler,\n    v_turn.target_kind,\n    v_turn.route_"
            "inbox_id,\n    v_turn.cancel_requested_at,\n    v_turn.cancel_confirmed_at,"
            "\n    v_turn.terminal_state,\n    v_turn.terminal_at;\nRETURN;\n\n        EN"
            "D;\n        "
        ),
        "alembic/versions/core/core_193_dashboard_turn_cancellation.py",
    ),
    (
        "core",
        "public",
        "dashboard_turn_acknowledge_cancel",
        "uuid,uuid",
        True,
        (
            "CREATE OR REPLACE FUNCTION public.dashboard_turn_acknowledge_cancel(\n      "
            "      p_message_id uuid,\n            p_session_id uuid\n        )\n        "
            "\nRETURNS TABLE(\n    outcome text,\n    message_id uuid,\n    conversation_"
            "id uuid,\n    request_id uuid,\n    target_butler text,\n    target_kind tex"
            "t,\n    route_inbox_id uuid,\n    cancel_requested_at timestamptz,\n    canc"
            "el_confirmed_at timestamptz,\n    terminal_state text,\n    terminal_at time"
            "stamptz\n)\n\n        LANGUAGE plpgsql\n        SECURITY DEFINER\nSET search"
            "_path = pg_catalog, pg_temp\n"
        ),
        (
            "\n        #variable_conflict use_column\n        DECLARE\n            v_turn"
            " public.dashboard_conversation_turns%ROWTYPE;\n            v_butler_name tex"
            "t;\n            p_outcome text;\n        BEGIN\n            SELECT * INTO v_"
            "turn\n            FROM public.dashboard_conversation_turns\n            WHER"
            "E message_id = p_message_id\n            FOR UPDATE;\n            IF NOT FOU"
            "ND THEN\n                \nRETURN QUERY SELECT\n    'missing'::text,\n    NU"
            "LL::uuid,\n    NULL::uuid,\n    NULL::uuid,\n    NULL::text,\n    NULL::text"
            ",\n    NULL::uuid,\n    NULL::timestamptz,\n    NULL::timestamptz,\n    NULL"
            "::text,\n    NULL::timestamptz;\nRETURN;\n\n            END IF;\n           "
            " IF v_turn.cancel_requested_at IS NULL THEN\n                p_outcome := CA"
            "SE\n                    WHEN v_turn.terminal_state = 'ambiguous' THEN 'ambig"
            "uous'\n                    WHEN v_turn.terminal_at IS NOT NULL THEN 'finishe"
            "d'\n                    ELSE 'conflict'\n                END;\n             "
            "   \nRETURN QUERY SELECT\n    p_outcome,\n    v_turn.message_id,\n    v_turn"
            ".conversation_id,\n    v_turn.request_id,\n    v_turn.target_butler,\n    v_"
            "turn.target_kind,\n    v_turn.route_inbox_id,\n    v_turn.cancel_requested_a"
            "t,\n    v_turn.cancel_confirmed_at,\n    v_turn.terminal_state,\n    v_turn."
            "terminal_at;\nRETURN;\n\n            END IF;\n            SELECT butler_name"
            " INTO v_butler_name\n            FROM public.dashboard_conversation_turn_ses"
            "sions\n            WHERE message_id = p_message_id\n              AND sessio"
            "n_id = p_session_id\n              AND invoke_claimed_at IS NOT NULL\n      "
            "        AND completed_at IS NULL;\n            IF NOT FOUND THEN\n          "
            "      p_outcome := 'conflict';\n                \nRETURN QUERY SELECT\n    p"
            "_outcome,\n    v_turn.message_id,\n    v_turn.conversation_id,\n    v_turn.r"
            "equest_id,\n    v_turn.target_butler,\n    v_turn.target_kind,\n    v_turn.r"
            "oute_inbox_id,\n    v_turn.cancel_requested_at,\n    v_turn.cancel_confirmed"
            "_at,\n    v_turn.terminal_state,\n    v_turn.terminal_at;\nRETURN;\n\n      "
            "      END IF;\n            PERFORM public.dashboard_turn_require_role('butle"
            "r_' || v_butler_name || '_rw');\n            IF v_turn.terminal_at IS NOT NU"
            "LL\n               AND v_turn.terminal_state IS DISTINCT FROM 'ambiguous' TH"
            "EN\n                p_outcome := CASE\n                    WHEN v_turn.termi"
            "nal_state = 'cancelled' THEN 'cancelled'\n                    ELSE 'finished"
            "'\n                END;\n                \nRETURN QUERY SELECT\n    p_outcom"
            "e,\n    v_turn.message_id,\n    v_turn.conversation_id,\n    v_turn.request_"
            "id,\n    v_turn.target_butler,\n    v_turn.target_kind,\n    v_turn.route_in"
            "box_id,\n    v_turn.cancel_requested_at,\n    v_turn.cancel_confirmed_at,\n "
            "   v_turn.terminal_state,\n    v_turn.terminal_at;\nRETURN;\n\n            E"
            "ND IF;\n            UPDATE public.dashboard_conversation_turn_sessions\n    "
            "        SET cancel_acknowledged_at = coalesce(cancel_acknowledged_at, now())"
            "\n            WHERE message_id = p_message_id\n              AND session_id "
            "= p_session_id;\n            p_outcome := CASE\n                WHEN v_turn."
            "terminal_state = 'ambiguous' THEN 'ambiguous'\n                ELSE 'cancell"
            "ing'\n            END;\n            \nRETURN QUERY SELECT\n    p_outcome,\n "
            "   v_turn.message_id,\n    v_turn.conversation_id,\n    v_turn.request_id,\n"
            "    v_turn.target_butler,\n    v_turn.target_kind,\n    v_turn.route_inbox_i"
            "d,\n    v_turn.cancel_requested_at,\n    v_turn.cancel_confirmed_at,\n    v_"
            "turn.terminal_state,\n    v_turn.terminal_at;\nRETURN;\n\n        END;\n    "
            "    "
        ),
        "alembic/versions/core/core_193_dashboard_turn_cancellation.py",
    ),
    (
        "core",
        "public",
        "dashboard_turn_confirm_cancel",
        "uuid",
        True,
        (
            "CREATE OR REPLACE FUNCTION public.dashboard_turn_confirm_cancel(\n          "
            "  p_message_id uuid\n        )\n        \nRETURNS TABLE(\n    outcome text,"
            "\n    message_id uuid,\n    conversation_id uuid,\n    request_id uuid,\n   "
            " target_butler text,\n    target_kind text,\n    route_inbox_id uuid,\n    c"
            "ancel_requested_at timestamptz,\n    cancel_confirmed_at timestamptz,\n    t"
            "erminal_state text,\n    terminal_at timestamptz\n)\n\n        LANGUAGE plpg"
            "sql\n        SECURITY DEFINER\nSET search_path = pg_catalog, pg_temp\n"
        ),
        (
            "\n        #variable_conflict use_column\n        DECLARE\n            v_turn"
            " public.dashboard_conversation_turns%ROWTYPE;\n            v_unconfirmed_inv"
            "ocation boolean;\n            p_outcome text;\n        BEGIN\n            SE"
            "LECT * INTO v_turn\n            FROM public.dashboard_conversation_turns\n  "
            "          WHERE message_id = p_message_id\n            FOR UPDATE;\n        "
            "    IF NOT FOUND THEN\n                \nRETURN QUERY SELECT\n    'missing':"
            ":text,\n    NULL::uuid,\n    NULL::uuid,\n    NULL::uuid,\n    NULL::text,\n"
            "    NULL::text,\n    NULL::uuid,\n    NULL::timestamptz,\n    NULL::timestam"
            "ptz,\n    NULL::text,\n    NULL::timestamptz;\nRETURN;\n\n            END IF"
            ";\n            IF v_turn.cancel_requested_at IS NULL THEN\n                p"
            "_outcome := CASE\n                    WHEN v_turn.terminal_state = 'ambiguou"
            "s' THEN 'ambiguous'\n                    WHEN v_turn.terminal_at IS NOT NULL"
            " THEN 'finished'\n                    ELSE 'conflict'\n                END;"
            "\n                \nRETURN QUERY SELECT\n    p_outcome,\n    v_turn.message_"
            "id,\n    v_turn.conversation_id,\n    v_turn.request_id,\n    v_turn.target_"
            "butler,\n    v_turn.target_kind,\n    v_turn.route_inbox_id,\n    v_turn.can"
            "cel_requested_at,\n    v_turn.cancel_confirmed_at,\n    v_turn.terminal_stat"
            "e,\n    v_turn.terminal_at;\nRETURN;\n\n            END IF;\n            IF "
            "v_turn.terminal_at IS NOT NULL THEN\n                p_outcome := CASE\n    "
            "                WHEN v_turn.terminal_state = 'cancelled' THEN 'cancelled'\n "
            "                   WHEN v_turn.terminal_state = 'ambiguous' THEN 'ambiguous'"
            "\n                    ELSE 'finished'\n                END;\n               "
            " \nRETURN QUERY SELECT\n    p_outcome,\n    v_turn.message_id,\n    v_turn.c"
            "onversation_id,\n    v_turn.request_id,\n    v_turn.target_butler,\n    v_tu"
            "rn.target_kind,\n    v_turn.route_inbox_id,\n    v_turn.cancel_requested_at,"
            "\n    v_turn.cancel_confirmed_at,\n    v_turn.terminal_state,\n    v_turn.te"
            "rminal_at;\nRETURN;\n\n            END IF;\n            IF EXISTS (\n       "
            "         SELECT 1\n                FROM public.dashboard_conversation_turn_s"
            "essions\n                WHERE message_id = p_message_id\n                  "
            "AND completed_at IS NULL\n                  AND invoke_active IS TRUE\n     "
            "       ) THEN\n                p_outcome := 'cancelling';\n                "
            "\nRETURN QUERY SELECT\n    p_outcome,\n    v_turn.message_id,\n    v_turn.co"
            "nversation_id,\n    v_turn.request_id,\n    v_turn.target_butler,\n    v_tur"
            "n.target_kind,\n    v_turn.route_inbox_id,\n    v_turn.cancel_requested_at,"
            "\n    v_turn.cancel_confirmed_at,\n    v_turn.terminal_state,\n    v_turn.te"
            "rminal_at;\nRETURN;\n\n            END IF;\n            -- A runtime can rel"
            "ease its live invoke lease before its outer\n            -- session records "
            "the real outcome. Do not turn that narrow\n            -- release window int"
            "o a false owner-cancel confirmation: every\n            -- still-open invoca"
            "tion must persist its own acknowledgement.\n            SELECT EXISTS (\n   "
            "             SELECT 1\n                FROM public.dashboard_conversation_tu"
            "rn_sessions\n                WHERE message_id = p_message_id\n              "
            "    AND invoke_claimed_at IS NOT NULL\n                  AND completed_at IS"
            " NULL\n                  AND cancel_acknowledged_at IS NULL\n            ) I"
            "NTO v_unconfirmed_invocation;\n            IF v_unconfirmed_invocation THEN"
            "\n                p_outcome := 'settling';\n                \nRETURN QUERY S"
            "ELECT\n    p_outcome,\n    v_turn.message_id,\n    v_turn.conversation_id,\n"
            "    v_turn.request_id,\n    v_turn.target_butler,\n    v_turn.target_kind,\n"
            "    v_turn.route_inbox_id,\n    v_turn.cancel_requested_at,\n    v_turn.canc"
            "el_confirmed_at,\n    v_turn.terminal_state,\n    v_turn.terminal_at;\nRETUR"
            "N;\n\n            END IF;\n            UPDATE public.dashboard_conversation_"
            "turns\n            SET cancel_confirmed_at = coalesce(cancel_confirmed_at, n"
            "ow()),\n                terminal_state = 'cancelled',\n                termi"
            "nal_at = coalesce(terminal_at, now()),\n                updated_at = now()\n"
            "            WHERE message_id = p_message_id\n            RETURNING * INTO v_"
            "turn;\n            p_outcome := 'cancelled';\n            \nRETURN QUERY SEL"
            "ECT\n    p_outcome,\n    v_turn.message_id,\n    v_turn.conversation_id,\n  "
            "  v_turn.request_id,\n    v_turn.target_butler,\n    v_turn.target_kind,\n  "
            "  v_turn.route_inbox_id,\n    v_turn.cancel_requested_at,\n    v_turn.cancel"
            "_confirmed_at,\n    v_turn.terminal_state,\n    v_turn.terminal_at;\nRETURN;"
            "\n\n        END;\n        "
        ),
        "alembic/versions/core/core_193_dashboard_turn_cancellation.py",
    ),
    (
        "core",
        "public",
        "dashboard_turn_live_sessions",
        "uuid",
        True,
        (
            "CREATE OR REPLACE FUNCTION public.dashboard_turn_live_sessions(\n           "
            " p_message_id uuid\n        )\n        RETURNS TABLE(\n            message_i"
            "d uuid,\n            session_id uuid,\n            butler_name text,\n      "
            "      phase text,\n            registered_at timestamptz,\n            invok"
            "e_claimed_at timestamptz,\n            invoke_active boolean\n        )\n   "
            "     LANGUAGE sql\n        SECURITY DEFINER\nSET search_path = pg_catalog, p"
            "g_temp\n"
        ),
        (
            "\n            SELECT\n                s.message_id,\n                s.sessi"
            "on_id,\n                s.butler_name,\n                s.phase,\n          "
            "      s.registered_at,\n                s.invoke_claimed_at,\n              "
            "  s.invoke_active\n            FROM public.dashboard_conversation_turn_sessi"
            "ons AS s\n            WHERE s.message_id = p_message_id\n              AND s"
            ".completed_at IS NULL\n            ORDER BY s.registered_at ASC\n        "
        ),
        "alembic/versions/core/core_193_dashboard_turn_cancellation.py",
    ),
    (
        "core",
        "public",
        "dashboard_turn_dispatch_status",
        "uuid",
        True,
        (
            "CREATE OR REPLACE FUNCTION public.dashboard_turn_dispatch_status(\n         "
            "   p_message_id uuid\n        )\n        \nRETURNS TABLE(\n    outcome text,"
            "\n    message_id uuid,\n    conversation_id uuid,\n    request_id uuid,\n   "
            " target_butler text,\n    target_kind text,\n    route_inbox_id uuid,\n    c"
            "ancel_requested_at timestamptz,\n    cancel_confirmed_at timestamptz,\n    t"
            "erminal_state text,\n    terminal_at timestamptz\n)\n\n        LANGUAGE plpg"
            "sql\n        SECURITY DEFINER\nSET search_path = pg_catalog, pg_temp\n"
        ),
        (
            "\n        #variable_conflict use_column\n        DECLARE\n            v_turn"
            " public.dashboard_conversation_turns%ROWTYPE;\n            p_outcome text;\n"
            "        BEGIN\n            SELECT * INTO v_turn\n            FROM public.das"
            "hboard_conversation_turns\n            WHERE message_id = p_message_id;\n   "
            "         IF NOT FOUND THEN\n                \nRETURN QUERY SELECT\n    'miss"
            "ing'::text,\n    NULL::uuid,\n    NULL::uuid,\n    NULL::uuid,\n    NULL::te"
            "xt,\n    NULL::text,\n    NULL::uuid,\n    NULL::timestamptz,\n    NULL::tim"
            "estamptz,\n    NULL::text,\n    NULL::timestamptz;\nRETURN;\n\n            E"
            "ND IF;\n            p_outcome := CASE\n                WHEN v_turn.terminal_"
            "state = 'cancelled' THEN 'cancelled'\n                WHEN v_turn.terminal_s"
            "tate = 'ambiguous' THEN 'ambiguous'\n                WHEN v_turn.terminal_at"
            " IS NOT NULL THEN 'finished'\n                WHEN v_turn.cancel_requested_a"
            "t IS NOT NULL THEN 'cancelling'\n                WHEN v_turn.target_kind IN "
            "('bug_report', 'dead_letter')\n                     AND v_turn.external_acti"
            "on_claimed_at IS NOT NULL\n                    THEN 'external_action_in_prog"
            "ress'\n                ELSE 'active'\n            END;\n            \nRETURN"
            " QUERY SELECT\n    p_outcome,\n    v_turn.message_id,\n    v_turn.conversati"
            "on_id,\n    v_turn.request_id,\n    v_turn.target_butler,\n    v_turn.target"
            "_kind,\n    v_turn.route_inbox_id,\n    v_turn.cancel_requested_at,\n    v_t"
            "urn.cancel_confirmed_at,\n    v_turn.terminal_state,\n    v_turn.terminal_at"
            ";\nRETURN;\n\n        END;\n        "
        ),
        "alembic/versions/core/core_193_dashboard_turn_cancellation.py",
    ),
    (
        "core",
        "public",
        "runtime_probe_control_receipt_retention",
        "",
        False,
        (
            "CREATE OR REPLACE FUNCTION public.runtime_probe_control_receipt_retention()"
            "\n        RETURNS trigger\n        LANGUAGE plpgsql\nSET search_path = pg_ca"
            "talog, pg_temp\n"
        ),
        (
            "\n        BEGIN\n            IF OLD.capability_exp >= now() - INTERVAL '5 se"
            "conds' THEN\n                RAISE EXCEPTION\n                    'runtime-p"
            "robe control receipt is still within its replay window';\n            END IF"
            ";\n            RETURN OLD;\n        END\n        "
        ),
        "alembic/versions/core/core_201_runtime_probe_control_receipts.py",
    ),
    (
        "core",
        "public",
        "record_runtime_probe_verification",
        "uuid,boolean,integer,text",
        True,
        (
            "CREATE OR REPLACE FUNCTION public.record_runtime_probe_verification(\n      "
            "      p_catalog_entry_id uuid,\n            p_ok boolean,\n            p_lat"
            "ency_ms integer,\n            p_error text\n        )\n        RETURNS boole"
            "an\n        LANGUAGE plpgsql\n        SECURITY DEFINER\nSET search_path = pg"
            "_catalog, pg_temp\n"
        ),
        (
            "\n        DECLARE\n            v_error text;\n            v_latency integer;"
            "\n        BEGIN\n            IF p_catalog_entry_id IS NULL OR p_ok IS NULL T"
            "HEN\n                RAISE EXCEPTION 'runtime-probe verification requires an"
            " entry and an outcome';\n            END IF;\n\n            -- A success has"
            " no error to keep, and a caller-supplied dump is\n            -- truncated t"
            "he same way the dashboard verify path truncates it.\n            IF p_ok THE"
            "N\n                v_error := NULL;\n            ELSE\n                v_err"
            "or := left(p_error, 4096);\n            END IF;\n\n            IF p_latency_"
            "ms IS NULL OR p_latency_ms < 0 THEN\n                v_latency := NULL;\n   "
            "         ELSE\n                v_latency := p_latency_ms;\n            END I"
            "F;\n\n            -- Verification evidence only.  enabled, priority, and eve"
            "ry\n            -- breaker-derived column are deliberately out of reach, so "
            "a probe\n            -- success can never close a breaker.\n            UPDA"
            "TE public.model_catalog\n               SET last_verified_at         = now()"
            ",\n                   last_verified_latency_ms = v_latency,\n               "
            "    last_verified_ok         = p_ok,\n                   last_verified_error"
            "      = v_error,\n                   updated_at               = now()\n     "
            "        WHERE id = p_catalog_entry_id;\n\n            RETURN FOUND;\n       "
            " END\n        "
        ),
        "alembic/versions/core/core_201_runtime_probe_control_receipts.py",
    ),
    (
        "core",
        "public",
        "runtime_probe_control_receipts_no_truncate",
        "",
        False,
        (
            "CREATE OR REPLACE FUNCTION public.runtime_probe_control_receipts_no_truncate"
            "()\n        RETURNS trigger\n        LANGUAGE plpgsql\nSET search_path = pg_"
            "catalog, pg_temp\n"
        ),
        (
            "\n        BEGIN\n            RAISE EXCEPTION\n                'runtime-probe"
            " control receipts cannot be truncated; '\n                'purge_expired() i"
            "s the only supported retention path';\n        END\n        "
        ),
        "alembic/versions/core/core_212_runtime_probe_control_receipts_owner_fence.py",
    ),
    (
        "core",
        "public",
        "resolve_relationship_prepared_action_status",
        "uuid",
        True,
        (
            "CREATE OR REPLACE FUNCTION public.resolve_relationship_prepared_action_statu"
            "s(\n    p_action_id uuid\n)\nRETURNS TABLE(status text)\nLANGUAGE plpgsql\nS"
            "TABLE\nSECURITY DEFINER\nSET search_path = pg_catalog, pg_temp\n"
        ),
        (
            "\nBEGIN\n    RETURN QUERY\n    SELECT pa.status\n    FROM relationship.pendi"
            "ng_actions pa\n    WHERE pa.id = p_action_id\n      AND pa.origin = 'prepare"
            "d';\nEND;\n"
        ),
        "alembic/versions/core/core_226_relationship_prepared_action_status_definer.py",
    ),
    (
        "core",
        "public",
        "cost_claim_immutable_assertion",
        "",
        False,
        (
            "CREATE OR REPLACE FUNCTION public.cost_claim_immutable_assertion() RETURNS t"
            "rigger\n        LANGUAGE plpgsql\nSET search_path = pg_catalog, pg_temp\n"
        ),
        (
            "\n        BEGIN\n            IF (NEW.claim_key, NEW.asserted_by, NEW.asserte"
            "d_by_role, NEW.kind,\n                NEW.direction, NEW.amount, NEW.currenc"
            "y, NEW.counterparty_entity_id,\n                NEW.counterparty_label, NEW."
            "expected_on, NEW.description,\n                NEW.evidence_kind, NEW.eviden"
            "ce_ref, NEW.asserted_at)\n               IS DISTINCT FROM\n               (O"
            "LD.claim_key, OLD.asserted_by, OLD.asserted_by_role, OLD.kind,\n            "
            "    OLD.direction, OLD.amount, OLD.currency, OLD.counterparty_entity_id,\n  "
            "              OLD.counterparty_label, OLD.expected_on, OLD.description,\n   "
            "             OLD.evidence_kind, OLD.evidence_ref, OLD.asserted_at)\n        "
            "    THEN\n                RAISE EXCEPTION 'cost claim assertions are immutab"
            "le; supersede the row';\n            END IF;\n            RETURN NEW;\n     "
            "   END "
        ),
        "alembic/versions/core/core_239_cost_claims.py",
    ),
    (
        "core",
        "public",
        "cost_claim_audit_claim",
        "",
        False,
        (
            "CREATE OR REPLACE FUNCTION public.cost_claim_audit_claim() RETURNS trigger\n"
            "        LANGUAGE plpgsql\nSET search_path = pg_catalog, pg_temp\n"
        ),
        (
            "\n        BEGIN\n            IF current_user = (\n                   SELECT "
            "pg_get_userbyid(c.relowner)\n                   FROM pg_class AS c\n        "
            "           WHERE c.oid = TG_RELID\n               )\n               AND curr"
            "ent_setting('butlers.cost_claim_restore', true) = 'on'\n            THEN\n  "
            "              RETURN NEW;\n            END IF;\n            INSERT INTO publ"
            "ic.cost_claim_events\n                (claim_id, actor_role, action, old_sta"
            "te, new_state, idempotency_key)\n            VALUES (\n                NEW.i"
            "d, current_user,\n                CASE WHEN TG_OP = 'INSERT' THEN 'asserted'"
            "\n                     WHEN NEW.retracted_at IS DISTINCT FROM OLD.retracted_"
            "at THEN 'retracted'\n                     WHEN NEW.superseded_at IS DISTINCT"
            " FROM OLD.superseded_at THEN 'superseded'\n                     ELSE 'amende"
            "d' END,\n                CASE WHEN TG_OP = 'UPDATE' THEN to_jsonb(OLD) ELSE "
            "NULL END,\n                to_jsonb(NEW), gen_random_uuid()::text\n         "
            "   );\n            RETURN NEW;\n        END "
        ),
        "alembic/versions/core/core_239_cost_claims.py",
    ),
    (
        "core",
        "public",
        "cost_claim_audit_resolution",
        "",
        False,
        (
            "CREATE OR REPLACE FUNCTION public.cost_claim_audit_resolution() RETURNS trig"
            "ger\n        LANGUAGE plpgsql\nSET search_path = pg_catalog, pg_temp\n"
        ),
        (
            "\n        BEGIN\n            IF current_user = (\n                   SELECT "
            "pg_get_userbyid(c.relowner)\n                   FROM pg_class AS c\n        "
            "           WHERE c.oid = TG_RELID\n               )\n               AND curr"
            "ent_setting('butlers.cost_claim_restore', true) = 'on'\n            THEN\n  "
            "              RETURN NEW;\n            END IF;\n            INSERT INTO publ"
            "ic.cost_claim_events\n                (claim_id, actor_role, action, old_sta"
            "te, new_state, idempotency_key)\n            VALUES (\n                NEW.c"
            "laim_id, current_user, 'resolved',\n                CASE WHEN TG_OP = 'UPDAT"
            "E' THEN to_jsonb(OLD) ELSE NULL END,\n                to_jsonb(NEW), gen_ran"
            "dom_uuid()::text\n            );\n            RETURN NEW;\n        END "
        ),
        "alembic/versions/core/core_239_cost_claims.py",
    ),
    (
        "core",
        "public",
        "cost_claim_restore_row",
        "text,jsonb",
        True,
        (
            "CREATE OR REPLACE FUNCTION public.cost_claim_restore_row(\n            p_rel"
            "ation TEXT,\n            p_payload JSONB\n        ) RETURNS void\n        LA"
            "NGUAGE plpgsql\n        SECURITY DEFINER\n        \n        SET row_security"
            " = on\nSET search_path = pg_catalog, pg_temp\n"
        ),
        (
            "\n        BEGIN\n            PERFORM set_config('butlers.cost_claim_restore'"
            ", 'on', true);\n\n            CASE p_relation\n                WHEN 'cost_cl"
            "aims' THEN\n                    INSERT INTO public.cost_claims\n            "
            "        SELECT (jsonb_populate_record(NULL::public.cost_claims, p_payload))."
            "*;\n                WHEN 'cost_claim_resolutions' THEN\n                    "
            "INSERT INTO public.cost_claim_resolutions\n                    SELECT (jsonb"
            "_populate_record(\n                        NULL::public.cost_claim_resolutio"
            "ns, p_payload\n                    )).*;\n                WHEN 'cost_claim_e"
            "vents' THEN\n                    INSERT INTO public.cost_claim_events OVERRI"
            "DING SYSTEM VALUE\n                    SELECT (jsonb_populate_record(NULL::p"
            "ublic.cost_claim_events, p_payload)).*;\n                    PERFORM setval("
            "\n                        pg_get_serial_sequence('public.cost_claim_events',"
            " 'id'),\n                        GREATEST((SELECT max(id) FROM public.cost_c"
            "laim_events), 1),\n                        EXISTS (SELECT 1 FROM public.cost"
            "_claim_events)\n                    );\n                ELSE\n              "
            "      RAISE EXCEPTION 'unsupported cost-claim restore relation';\n          "
            "  END CASE;\n        END\n        "
        ),
        "alembic/versions/core/core_239_cost_claims.py",
    ),
    (
        "core",
        "dashboard_auth",
        "api",
        "text,jsonb",
        True,
        (
            "CREATE FUNCTION dashboard_auth.api(action text, p jsonb) RETURNS jsonb\nLANG"
            "UAGE plpgsql SECURITY DEFINER\nSET search_path = pg_catalog, pg_temp\n"
        ),
        (
            "\nDECLARE\n s dashboard_auth.instance%ROWTYPE; c dashboard_auth.contexts%ROW"
            "TYPE;\n i dashboard_auth.intents%ROWTYPE; q dashboard_auth.ceremonies%ROWTYP"
            "E;\n k dashboard_auth.credentials%ROWTYPE; se dashboard_auth.sessions%ROWTYP"
            "E;\n t timestamptz := clock_timestamp(); deadline timestamptz; bucket text;"
            "\n cap integer; n integer; result jsonb; mode_ok boolean; browser_ok boolean"
            ";\nBEGIN\n SELECT * INTO s FROM dashboard_auth.instance WHERE singleton FOR "
            'UPDATE;\n t := clock_timestamp();\n IF NOT FOUND THEN RETURN \'{"error":"A'
            "UTH_UNAVAILABLE\"}'; END IF;\n IF (s.state='configured_key') IS DISTINCT FRO"
            'M (s.key_generation IS NOT NULL) THEN\n  RETURN \'{"error":"AUTH_UNAVAILAB'
            "LE\"}'; END IF;\n mode_ok := s.key_generation IS NOT DISTINCT FROM p->>'key_"
            "generation';\n browser_ok := COALESCE(mode_ok AND s.origin IS NOT NULL AND s"
            ".origin=p->>'origin'\n                    AND s.rp_id=p->>'rp_id', false);\n"
            " IF s.state='keyless_enrolled' AND NOT EXISTS (\n  SELECT FROM dashboard_aut"
            "h.credentials WHERE active AND credential_epoch=s.credential_epoch\n   AND c"
            "redential_id IS NOT NULL AND credential_data IS NOT NULL AND user_handle IS "
            'NOT NULL\n ) THEN RETURN \'{"error":"AUTH_UNAVAILABLE"}\'; END IF;\n IF NO'
            'T mode_ok THEN RETURN \'{"error":"AUTH_UNAVAILABLE"}\'; END IF;\n IF actio'
            'n=\'status\' THEN\n  IF NOT browser_ok THEN RETURN \'{"state":"unavailable"'
            ',"authenticated":false,"session_expires_at":null}\'; END IF;\n  SELECT * '
            "INTO se FROM dashboard_auth.sessions WHERE digest=p->>'session_digest'\n   A"
            "ND NOT revoked AND expires_at>t AND credential_epoch=s.credential_epoch\n   "
            "AND session_epoch=s.session_epoch;\n  RETURN jsonb_build_object('state',s.st"
            "ate,'authenticated',FOUND,\n   'session_expires_at',se.expires_at);\n END IF"
            ";\n IF action='header' THEN\n  IF s.state<>'configured_key' OR s.key_generat"
            'ion IS NULL THEN\n   RETURN \'{"error":"UNAUTHORIZED"}\'; END IF;\n  RETUR'
            'N \'{"method":"header","expires_at":null}\';\n END IF;\n IF action IN (\''
            "authorize','csrf','revoke','revoke_all') THEN\n  IF p->>'method'='header' TH"
            "EN\n   IF s.state<>'configured_key' OR s.key_generation IS NULL THEN\n    RE"
            'TURN \'{"error":"UNAUTHORIZED"}\'; END IF;\n  ELSE\n   IF NOT browser_ok T'
            'HEN RETURN \'{"error":"AUTH_UNAVAILABLE"}\'; END IF;\n   SELECT * INTO se '
            "FROM dashboard_auth.sessions WHERE digest=p->>'session_digest'\n    AND NOT "
            "revoked AND expires_at>t AND credential_epoch=s.credential_epoch\n    AND se"
            'ssion_epoch=s.session_epoch;\n   IF NOT FOUND THEN RETURN \'{"error":"UNAU'
            "THORIZED\"}'; END IF;\n   IF COALESCE((p->>'unsafe')::boolean,false) AND NOT"
            " EXISTS (\n    SELECT FROM dashboard_auth.csrf WHERE session_digest=se.diges"
            "t\n     AND digest=p->>'csrf_digest' AND expires_at>t\n   ) THEN RETURN '{\""
            "error\":\"FORBIDDEN\"}'; END IF;\n  END IF;\n  IF action='authorize' THEN\n "
            "  RETURN jsonb_build_object('method',COALESCE(p->>'method','cookie'),'expire"
            "s_at',se.expires_at);\n  ELSIF action='csrf' THEN\n   deadline := LEAST(se.e"
            "xpires_at,t+interval '30 minutes');\n   DELETE FROM dashboard_auth.csrf WHER"
            "E session_digest=se.digest AND digest IN (\n    SELECT digest FROM dashboard"
            "_auth.csrf WHERE session_digest=se.digest\n     ORDER BY created_at DESC,dig"
            "est OFFSET 3);\n   INSERT INTO dashboard_auth.csrf(session_digest,digest,exp"
            "ires_at)\n    VALUES(se.digest,p->>'new_csrf_digest',deadline);\n   RETURN j"
            "sonb_build_object('csrf_expires_at',deadline);\n  ELSIF action='revoke_all' "
            "THEN\n   UPDATE dashboard_auth.instance SET session_epoch=session_epoch+1 WH"
            "ERE singleton;\n   UPDATE dashboard_auth.sessions SET revoked=true WHERE NOT"
            " revoked;\n  ELSE\n   UPDATE dashboard_auth.sessions SET revoked=true WHERE "
            "digest=p->>'session_digest';\n  END IF;\n  INSERT INTO dashboard_auth.audit("
            "action,outcome,actor) VALUES\n   (CASE WHEN action='revoke_all' THEN 'revoke"
            "' ELSE 'logout' END,'success','owner');\n  RETURN '{}';\n END IF;\n IF NOT b"
            'rowser_ok THEN RETURN \'{"error":"AUTH_UNAVAILABLE"}\'; END IF;\n IF actio'
            "n IN ('key_session','key_session_attempt') AND s.state<>'configured_key' THE"
            'N RETURN \'{"error":"AUTH_UNAVAILABLE"}\'; END IF;\n IF action NOT IN (\'ke'
            "y_session','key_session_attempt','context') AND s.state='configured_key' THE"
            'N\n  RETURN \'{"error":"AUTH_UNAVAILABLE"}\'; END IF;\n -- Fixed, global b'
            "uckets are independent of visitor-supplied labels.\n bucket := CASE WHEN act"
            "ion='context' THEN 'context'\n  WHEN action IN ('registration_options','logi"
            "n_options') THEN 'options'\n  WHEN action IN ('snapshot_registration','snaps"
            "hot_login','key_session_attempt') THEN 'finish' END;\n IF bucket IS NOT NULL"
            " THEN\n  cap := CASE bucket WHEN 'context' THEN 30 WHEN 'options' THEN 60 EL"
            "SE 120 END;\n  INSERT INTO dashboard_auth.rate_buckets(kind,minute,count)\n "
            "  VALUES(bucket,date_trunc('minute',t),1)\n   ON CONFLICT(kind) DO UPDATE SE"
            "T minute=EXCLUDED.minute,\n    count=CASE WHEN rate_buckets.minute=EXCLUDED."
            "minute THEN rate_buckets.count+1 ELSE 1 END\n   RETURNING count INTO n;\n  I"
            'F n>cap THEN RETURN \'{"error":"RATE_LIMITED"}\'; END IF;\n END IF;\n IF a'
            "ction='key_session_attempt' THEN RETURN '{}'; END IF;\n IF action='context' "
            "THEN\n  SELECT count(*) INTO n FROM dashboard_auth.contexts WHERE NOT revoke"
            'd AND expires_at>t;\n  IF n>=64 THEN RETURN \'{"error":"RATE_LIMITED"}\'; '
            "END IF;\n  deadline := t+interval '5 minutes';\n  INSERT INTO dashboard_auth"
            ".contexts(digest,csrf_digest,expires_at,credential_epoch)\n   VALUES(p->>'co"
            "ntext_digest',p->>'csrf_digest',deadline,s.credential_epoch);\n  RETURN json"
            "b_build_object('csrf_expires_at',deadline);\n END IF;\n IF action<>'key_sess"
            "ion' THEN\n  SELECT * INTO c FROM dashboard_auth.contexts WHERE digest=p->>'"
            "context_digest' FOR UPDATE;\n  t := clock_timestamp();\n  IF NOT FOUND THEN "
            'RETURN \'{"error":"UNAUTHORIZED"}\'; END IF;\n  IF c.revoked OR c.expires_'
            'at<=t THEN RETURN \'{"error":"AUTH_RESTART_REQUIRED"}\'; END IF;\n  IF c.c'
            "srf_digest IS DISTINCT FROM p->>'csrf_digest' THEN RETURN '{\"error\":\"FORB"
            "IDDEN\"}'; END IF;\n  IF c.credential_epoch<>s.credential_epoch THEN RETURN "
            '\'{"error":"AUTH_RESTART_REQUIRED"}\'; END IF;\n  IF action IN (\'snapshot_'
            "registration','snapshot_login') THEN\n   UPDATE dashboard_auth.contexts SET "
            "finish_minute=date_trunc('minute',t),\n    finish_count=CASE WHEN finish_min"
            "ute=date_trunc('minute',t) THEN finish_count+1 ELSE 1 END\n    WHERE digest="
            'c.digest RETURNING finish_count INTO n;\n   IF n>10 THEN RETURN \'{"error":'
            "\"RATE_LIMITED\"}'; END IF;\n  END IF;\n END IF;\n IF action='intent' THEN\n"
            "  IF p->>'operation'='enroll' AND s.state<>'keyless_unenrolled' OR\n     p->"
            ">'operation'='recover' AND s.state NOT IN ('keyless_enrolled','recovery_pend"
            "ing') OR\n     p->>'operation' NOT IN ('enroll','recover') THEN RETURN '{\"e"
            'rror":"AUTH_RESTART_REQUIRED"}\'; END IF;\n  SELECT * INTO i FROM dashboar'
            "d_auth.intents WHERE context_digest=c.digest\n   AND NOT consumed AND expire"
            "s_at>t;\n  IF FOUND THEN\n   IF i.operation<>p->>'operation' THEN RETURN '{"
            '"error":"AUTH_RESTART_REQUIRED"}\'; END IF;\n   RETURN jsonb_build_object'
            "('request_id',i.id,'expires_at',i.expires_at,\n    'operation',i.operation,'"
            "canonical_origin',s.origin);\n  END IF;\n  SELECT count(*) INTO n FROM dashb"
            "oard_auth.intents WHERE NOT consumed AND expires_at>t;\n  IF n>=16 THEN RETU"
            'RN \'{"error":"RATE_LIMITED"}\'; END IF;\n  INSERT INTO dashboard_auth.int'
            "ents(id,context_digest,operation,credential_epoch,expires_at)\n   VALUES(p->"
            ">'request_id',c.digest,p->>'operation',s.credential_epoch,c.expires_at);\n  "
            "RETURN jsonb_build_object('request_id',p->>'request_id','expires_at',c.expir"
            "es_at,\n   'operation',p->>'operation','canonical_origin',s.origin);\n END I"
            "F;\n IF action='cancel' THEN\n  UPDATE dashboard_auth.intents SET consumed=t"
            "rue,authorized=false WHERE context_digest=c.digest\n   AND NOT consumed AND "
            "(id=p->>'request_id' OR id=(\n    SELECT intent_id FROM dashboard_auth.cerem"
            "onies\n     WHERE id=p->>'ceremony_id' AND context_digest=c.digest));\n  UPD"
            "ATE dashboard_auth.ceremonies SET consumed=true WHERE context_digest=c.diges"
            "t AND NOT consumed\n   AND (id=p->>'ceremony_id' OR intent_id=p->>'request_i"
            "d');\n  RETURN '{}';\n END IF;\n IF action IN ('registration_options','login"
            "_options') THEN\n  IF action='registration_options' THEN\n   SELECT * INTO i"
            " FROM dashboard_auth.intents WHERE id=p->>'request_id' FOR UPDATE;\n  t := c"
            "lock_timestamp();\n   IF NOT FOUND OR i.context_digest<>c.digest OR i.consum"
            "ed OR i.expires_at<=t\n    OR i.credential_epoch<>s.credential_epoch THEN RE"
            'TURN \'{"error":"AUTH_RESTART_REQUIRED"}\'; END IF;\n   IF NOT i.authorize'
            "d THEN RETURN jsonb_build_object('state','pending','expires_at',i.expires_at"
            "); END IF;\n  ELSIF s.state<>'keyless_enrolled' THEN RETURN '{\"error\":\"AU"
            "TH_RESTART_REQUIRED\"}'; END IF;\n  SELECT * INTO q FROM dashboard_auth.cere"
            "monies WHERE context_digest=c.digest\n   AND NOT consumed AND expires_at>t F"
            "OR UPDATE;\n  IF FOUND THEN\n   IF (action='login_options')<>(q.operation='l"
            'ogin\') THEN RETURN \'{"error":"AUTH_RESTART_REQUIRED"}\'; END IF;\n   RETU'
            "RN to_jsonb(q);\n  END IF;\n  INSERT INTO dashboard_auth.ceremonies(id,conte"
            "xt_digest,intent_id,operation,\n   credential_epoch,session_epoch,challenge,"
            "user_handle,expires_at)\n   VALUES(p->>'ceremony_id',c.digest,i.id,COALESCE("
            "i.operation,'login'),s.credential_epoch,\n    s.session_epoch,p->>'challenge"
            "',CASE WHEN i.id IS NOT NULL THEN p->>'user_handle' END,\n    LEAST(c.expire"
            "s_at,COALESCE(i.expires_at,c.expires_at))) RETURNING * INTO q;\n  RETURN to_"
            "jsonb(q);\n END IF;\n IF action IN ('snapshot_registration','snapshot_login'"
            ",'finish_registration','finish_login') THEN\n  -- Singleton -> context -> in"
            "tent -> ceremony is the global lock order.\n  SELECT * INTO i FROM dashboard"
            "_auth.intents WHERE id=(\n   SELECT intent_id FROM dashboard_auth.ceremonies"
            " WHERE id=p->>'ceremony_id') FOR UPDATE;\n  SELECT * INTO q FROM dashboard_a"
            "uth.ceremonies WHERE id=p->>'ceremony_id' FOR UPDATE;\n  t := clock_timestam"
            "p();\n  IF NOT FOUND OR q.context_digest<>c.digest OR q.consumed OR q.expire"
            "s_at<=t\n   OR q.credential_epoch<>s.credential_epoch OR q.session_epoch<>s."
            'session_epoch THEN\n   RETURN \'{"error":"AUTH_RESTART_REQUIRED"}\'; END I'
            "F;\n  IF action IN ('snapshot_registration','finish_registration') THEN\n   "
            "IF q.operation NOT IN ('enroll','recover') OR i.id IS NULL OR NOT i.authoriz"
            "ed\n    OR i.consumed OR i.expires_at<=t OR i.credential_epoch<>s.credential"
            "_epoch\n    OR i.context_digest<>c.digest OR\n    (q.operation='enroll' AND "
            "s.state<>'keyless_unenrolled') OR\n    (q.operation='recover' AND s.state<>'"
            'recovery_pending\') THEN\n     RETURN \'{"error":"AUTH_RESTART_REQUIRED"}\''
            "; END IF;\n  ELSE\n   IF q.operation<>'login' OR s.state<>'keyless_enrolled'"
            ' THEN RETURN \'{"error":"AUTH_RESTART_REQUIRED"}\'; END IF;\n   SELECT * I'
            "NTO k FROM dashboard_auth.credentials WHERE active AND credential_epoch=s.cr"
            'edential_epoch FOR UPDATE;\n   IF NOT FOUND THEN RETURN \'{"error":"AUTH_U'
            "NAVAILABLE\"}'; END IF;\n  END IF;\n  IF action IN ('snapshot_registration',"
            "'snapshot_login') THEN\n   RETURN jsonb_build_object('ceremony',to_jsonb(q),"
            "'credential',to_jsonb(k));\n  END IF;\n  -- Lock waits never preserve author"
            "ity past its absolute DB-clock deadline.\n  t := clock_timestamp();\n  IF c."
            "expires_at<=t OR q.expires_at<=t OR (i.id IS NOT NULL AND i.expires_at<=t) T"
            'HEN\n   RETURN \'{"error":"AUTH_RESTART_REQUIRED"}\'; END IF;\n  -- A veri'
            "fier result is accepted only for the exact snapshot still current.\n  IF p->"
            ">'challenge' IS DISTINCT FROM q.challenge OR\n   (p->>'credential_epoch')::b"
            'igint<>s.credential_epoch THEN RETURN \'{"error":"AUTH_RESTART_REQUIRED"}'
            "'; END IF;\n  IF action='finish_registration' THEN\n   IF EXISTS(SELECT FROM"
            ' dashboard_auth.credentials WHERE active) THEN RETURN \'{"error":"AUTH_RES'
            "TART_REQUIRED\"}'; END IF;\n   INSERT INTO dashboard_auth.credentials(creden"
            "tial_epoch,credential_id,credential_data,\n    user_handle,backup_eligible,b"
            "ackup_state,counter)\n    VALUES(s.credential_epoch,p->>'credential_id',p->>"
            "'credential_data',q.user_handle,\n     (p->>'backup_eligible')::boolean,(p->"
            ">'backup_state')::boolean,(p->>'counter')::bigint);\n   UPDATE dashboard_aut"
            "h.instance SET state='keyless_enrolled' WHERE singleton;\n   UPDATE dashboar"
            "d_auth.intents SET consumed=true,authorized=false WHERE id=i.id;\n  ELSE\n  "
            " IF p->>'credential_id' IS DISTINCT FROM k.credential_id OR\n    (p->>'backu"
            "p_eligible')::boolean IS DISTINCT FROM k.backup_eligible OR\n    ((p->>'back"
            "up_state')::boolean AND NOT k.backup_eligible) OR\n    (NOT k.backup_eligibl"
            "e AND (k.counter>0 OR (p->>'counter')::bigint>0)\n     AND (p->>'counter')::"
            'bigint<=k.counter) THEN RETURN \'{"error":"UNAUTHORIZED"}\'; END IF;\n   I'
            "F k.backup_eligible AND (p->>'counter')::bigint<=k.counter THEN\n    INSERT "
            "INTO dashboard_auth.audit(action,outcome,actor) VALUES('login','counter_anom"
            "aly','owner');\n   END IF;\n   UPDATE dashboard_auth.credentials SET counter"
            "=GREATEST(counter,(p->>'counter')::bigint),\n    backup_state=(p->>'backup_s"
            "tate')::boolean WHERE credential_epoch=s.credential_epoch;\n  END IF;\n  UPD"
            "ATE dashboard_auth.ceremonies SET consumed=true WHERE id=q.id;\n  UPDATE das"
            "hboard_auth.contexts SET revoked=true WHERE digest=c.digest;\n ELSIF action<"
            ">'key_session' THEN\n  RETURN '{\"error\":\"AUTH_UNAVAILABLE\"}';\n END IF;"
            "\n deadline := t+interval '12 hours';\n INSERT INTO dashboard_auth.sessions("
            "digest,credential_epoch,session_epoch,expires_at)\n  VALUES(p->>'new_session"
            "_digest',s.credential_epoch,s.session_epoch,deadline);\n INSERT INTO dashboa"
            "rd_auth.csrf(session_digest,digest,expires_at)\n  VALUES(p->>'new_session_di"
            "gest',p->>'new_csrf_digest',t+interval '30 minutes');\n INSERT INTO dashboar"
            "d_auth.audit(action,outcome,actor) VALUES\n  (CASE action WHEN 'finish_regis"
            "tration' THEN 'registration' WHEN 'finish_login' THEN 'login'\n   ELSE 'sess"
            "ion' END,'success','owner');\n RETURN jsonb_build_object('csrf_expires_at',t"
            "+interval '30 minutes','session_expires_at',deadline);\nEND "
        ),
        "alembic/versions/core/core_240_dashboard_owner_auth.py",
    ),
    (
        "core",
        "dashboard_auth",
        "host",
        "text,jsonb",
        True,
        (
            "CREATE FUNCTION dashboard_auth.host(action text,p jsonb) RETURNS jsonb\nLANG"
            "UAGE plpgsql SECURITY DEFINER\nSET search_path = pg_catalog, pg_temp\n"
        ),
        (
            "\nDECLARE s dashboard_auth.instance%ROWTYPE; i dashboard_auth.intents%ROWTYP"
            "E;\n t timestamptz := clock_timestamp(); next_state text; event text;\nBEGIN"
            "\n SELECT * INTO s FROM dashboard_auth.instance WHERE singleton FOR UPDATE;"
            '\n t := clock_timestamp();\n IF NOT FOUND THEN RETURN \'{"error":"AUTH_UNA'
            "VAILABLE\"}'; END IF;\n IF (s.state='configured_key') IS DISTINCT FROM (s.ke"
            'y_generation IS NOT NULL) THEN\n  RETURN \'{"error":"AUTH_UNAVAILABLE"}\';'
            " END IF;\n IF action='clear_pending' THEN\n  IF NOT COALESCE((p->>'confirm_r"
            'evoke\')::boolean,false) THEN RETURN \'{"error":"FORBIDDEN"}\'; END IF;\n  '
            "IF s.key_generation IS DISTINCT FROM p->>'key_generation' THEN RETURN '{\"er"
            'ror":"AUTH_UNAVAILABLE"}\'; END IF;\n  UPDATE dashboard_auth.contexts SET '
            "revoked=true WHERE NOT revoked;\n  UPDATE dashboard_auth.intents SET consume"
            "d=true,authorized=false WHERE NOT consumed;\n  UPDATE dashboard_auth.ceremon"
            "ies SET consumed=true WHERE NOT consumed;\n  INSERT INTO dashboard_auth.audi"
            "t(action,outcome,actor) VALUES(action,'success','host_operator');\n  RETURN "
            "jsonb_build_object('operation',action,'canonical_origin',s.origin,'changed',"
            "true);\n END IF;\n IF action='revoke_sessions' THEN\n  IF NOT COALESCE((p->>"
            "'confirm_revoke')::boolean,false) THEN RETURN '{\"error\":\"FORBIDDEN\"}'; E"
            "ND IF;\n  IF s.key_generation IS DISTINCT FROM p->>'key_generation' THEN RET"
            'URN \'{"error":"AUTH_UNAVAILABLE"}\'; END IF;\n  UPDATE dashboard_auth.ins'
            "tance SET session_epoch=session_epoch+1 WHERE singleton;\n  UPDATE dashboard"
            "_auth.sessions SET revoked=true WHERE NOT revoked;\n  UPDATE dashboard_auth."
            "ceremonies SET consumed=true WHERE operation='login' AND NOT consumed;\n  IN"
            "SERT INTO dashboard_auth.audit(action,outcome,actor) VALUES(action,'success'"
            ",'host_operator');\n  RETURN jsonb_build_object('operation',action,'canonica"
            "l_origin',s.origin,'changed',true);\n END IF;\n IF action IN ('reconcile_mod"
            "e','rebind_origin') THEN\n  IF NOT COALESCE((p->>'confirm_revoke')::boolean,"
            'false) THEN RETURN \'{"error":"FORBIDDEN"}\'; END IF;\n  IF action=\'reconc'
            "ile_mode' THEN\n   IF s.key_generation IS NOT DISTINCT FROM p->>'key_generat"
            "ion'\n    AND s.origin IS NOT DISTINCT FROM p->>'origin' AND s.rp_id IS NOT "
            "DISTINCT FROM p->>'rp_id'\n    THEN RETURN jsonb_build_object('operation',ac"
            "tion,'canonical_origin',s.origin,'changed',false); END IF;\n   IF s.origin I"
            "S NOT NULL AND (s.origin IS DISTINCT FROM p->>'origin' OR s.rp_id IS DISTINC"
            "T FROM p->>'rp_id')\n    THEN RETURN '{\"error\":\"AUTH_UNAVAILABLE\"}'; END"
            " IF;\n   next_state := CASE WHEN p->>'key_generation' IS NULL THEN 'keyless_"
            "unenrolled' ELSE 'configured_key' END;\n  ELSE\n   IF p->>'origin' IS NULL O"
            "R p->>'rp_id' IS NULL THEN RETURN '{\"error\":\"AUTH_UNAVAILABLE\"}'; END IF"
            ";\n   IF s.key_generation IS DISTINCT FROM p->>'key_generation' THEN RETURN "
            '\'{"error":"AUTH_UNAVAILABLE"}\'; END IF;\n   next_state := CASE WHEN s.ke'
            "y_generation IS NULL THEN 'recovery_pending' ELSE 'configured_key' END;\n  E"
            "ND IF;\n  UPDATE dashboard_auth.instance SET credential_epoch=credential_epo"
            "ch+1,session_epoch=session_epoch+1,\n   state=next_state,origin=p->>'origin'"
            ",rp_id=p->>'rp_id',key_generation=p->>'key_generation' WHERE singleton;\n EL"
            "SE\n  IF s.origin IS DISTINCT FROM p->>'origin' OR s.rp_id IS DISTINCT FROM "
            "p->>'rp_id'\n   OR s.origin IS NULL OR s.key_generation IS DISTINCT FROM p->"
            ">'key_generation'\n   OR s.state='configured_key' THEN RETURN '{\"error\":\""
            "AUTH_UNAVAILABLE\"}'; END IF;\n  SELECT * INTO i FROM dashboard_auth.intents"
            " WHERE id=p->>'request_id' FOR UPDATE;\n  t := clock_timestamp();\n  IF NOT "
            "FOUND OR i.consumed OR i.expires_at<=t OR i.credential_epoch<>s.credential_e"
            "poch\n   OR NOT EXISTS(SELECT FROM dashboard_auth.contexts WHERE digest=i.co"
            'ntext_digest\n    AND NOT revoked AND expires_at>t) THEN RETURN \'{"error":'
            "\"AUTH_RESTART_REQUIRED\"}'; END IF;\n  IF action='authorize_registration' T"
            "HEN\n   IF s.state<>'keyless_unenrolled' OR i.operation<>'enroll' THEN RETUR"
            'N \'{"error":"AUTH_RESTART_REQUIRED"}\'; END IF;\n  ELSIF action=\'authoriz'
            "e_recovery' THEN\n   IF NOT COALESCE((p->>'confirm_revoke')::boolean,false) "
            'THEN RETURN \'{"error":"FORBIDDEN"}\'; END IF;\n   IF s.state NOT IN (\'key'
            "less_enrolled','recovery_pending') OR i.operation<>'recover' THEN\n    RETUR"
            'N \'{"error":"AUTH_RESTART_REQUIRED"}\'; END IF;\n  ELSE RETURN \'{"error'
            '":"AUTH_UNAVAILABLE"}\'; END IF;\n  IF i.authorized THEN RETURN jsonb_buil'
            "d_object('operation',action,'canonical_origin',s.origin,'changed',false); EN"
            "D IF;\n  IF action='authorize_registration' THEN\n   UPDATE dashboard_auth.i"
            "ntents SET consumed=true,authorized=false WHERE authorized AND id<>i.id;\n  "
            " UPDATE dashboard_auth.intents SET authorized=true,expires_at=LEAST(expires_"
            "at,t+interval '5 minutes') WHERE id=i.id;\n   INSERT INTO dashboard_auth.aud"
            "it(action,outcome,actor) VALUES(action,'success','host_operator');\n   RETUR"
            "N jsonb_build_object('operation',action,'canonical_origin',s.origin,'changed"
            "',true);\n  END IF;\n  UPDATE dashboard_auth.instance SET credential_epoch=c"
            "redential_epoch+1,session_epoch=session_epoch+1,\n   state='recovery_pending"
            "' WHERE singleton;\n END IF;\n UPDATE dashboard_auth.credentials SET active="
            "false,retired_at=t WHERE active;\n UPDATE dashboard_auth.sessions SET revoke"
            "d=true WHERE NOT revoked;\n UPDATE dashboard_auth.ceremonies SET consumed=tr"
            "ue WHERE NOT consumed;\n UPDATE dashboard_auth.intents SET consumed=true,aut"
            "horized=false WHERE NOT consumed AND (i.id IS NULL OR id<>i.id);\n UPDATE da"
            "shboard_auth.contexts SET revoked=true WHERE NOT revoked AND (i.id IS NULL O"
            "R digest<>i.context_digest);\n IF i.id IS NOT NULL THEN\n  UPDATE dashboard_"
            "auth.intents SET authorized=true,credential_epoch=s.credential_epoch+1,\n   "
            "expires_at=LEAST(expires_at,t+interval '5 minutes') WHERE id=i.id;\n  UPDATE"
            " dashboard_auth.contexts SET credential_epoch=s.credential_epoch+1 WHERE dig"
            "est=i.context_digest;\n END IF;\n INSERT INTO dashboard_auth.audit(action,ou"
            "tcome,actor) VALUES(action,'success','host_operator');\n RETURN jsonb_build_"
            "object('operation',action,'canonical_origin',COALESCE(p->>'origin',s.origin)"
            ",'changed',true);\nEND "
        ),
        "alembic/versions/core/core_240_dashboard_owner_auth.py",
    ),
    (
        "core",
        "dashboard_auth",
        "cleanup",
        "",
        True,
        (
            "CREATE FUNCTION dashboard_auth.cleanup() RETURNS void\nLANGUAGE plpgsql SECU"
            "RITY DEFINER\nSET search_path = pg_catalog, pg_temp\n"
        ),
        (
            "\nDECLARE t timestamptz := clock_timestamp();\nBEGIN\n -- A bounded batch pe"
            "r table. Repeated independent sweeps drain historical state.\n DELETE FROM d"
            "ashboard_auth.intents WHERE id IN (SELECT id FROM dashboard_auth.intents WHE"
            "RE expires_at<t-interval '24 hours' LIMIT 1000);\n DELETE FROM dashboard_aut"
            "h.ceremonies WHERE id IN (SELECT id FROM dashboard_auth.ceremonies WHERE exp"
            "ires_at<t-interval '24 hours' LIMIT 1000);\n DELETE FROM dashboard_auth.cont"
            "exts WHERE digest IN (SELECT digest FROM dashboard_auth.contexts WHERE expir"
            "es_at<t-interval '23 hours' LIMIT 1000);\n DELETE FROM dashboard_auth.sessio"
            "ns WHERE digest IN (SELECT digest FROM dashboard_auth.sessions WHERE expires"
            "_at<t-interval '23 hours' LIMIT 1000);\n DELETE FROM dashboard_auth.csrf WHE"
            "RE (session_digest,digest) IN (SELECT session_digest,digest FROM dashboard_a"
            "uth.csrf WHERE expires_at<t-interval '23 hours' LIMIT 1000);\n UPDATE dashbo"
            "ard_auth.credentials SET credential_id=NULL,credential_data=NULL,user_handle"
            "=NULL,\n  backup_eligible=false,backup_state=false,counter=0,retired_at=NULL"
            "\n  WHERE credential_epoch IN (SELECT credential_epoch FROM dashboard_auth.c"
            "redentials\n   WHERE NOT active AND retired_at<t-interval '23 hours' AND cre"
            "dential_id IS NOT NULL LIMIT 1000);\n DELETE FROM dashboard_auth.audit WHERE"
            " ctid IN (SELECT ctid FROM dashboard_auth.audit WHERE ts<t-interval '30 days"
            "' LIMIT 2000);\nEND "
        ),
        "alembic/versions/core/core_240_dashboard_owner_auth.py",
    ),
    (
        "core",
        "public",
        "guard_entity_rebind_identity",
        "",
        False,
        (
            "CREATE OR REPLACE FUNCTION public.guard_entity_rebind_identity()\n        RE"
            "TURNS trigger\n        LANGUAGE plpgsql\nSET search_path = pg_catalog, pg_te"
            "mp\n"
        ),
        (
            "\n        BEGIN\n            IF NEW.rebind_id IS DISTINCT FROM OLD.rebind_id"
            "\n               OR NEW.source_entity_id IS DISTINCT FROM OLD.source_entity_"
            "id\n               OR NEW.target_entity_id IS DISTINCT FROM OLD.target_entit"
            "y_id\n               OR NEW.target_schema IS DISTINCT FROM OLD.target_schema"
            "\n            THEN\n                RAISE EXCEPTION 'entity rebind receipt i"
            "dentity is immutable';\n            END IF;\n            RETURN NEW;\n      "
            "  END\n        "
        ),
        "alembic/versions/core/core_243_entity_rebind_log.py",
    ),
    (
        "core",
        "public",
        "resolve_finance_bill_status",
        "uuid",
        True,
        (
            "CREATE OR REPLACE FUNCTION public.resolve_finance_bill_status(p_bill_id uuid"
            ")\nRETURNS TABLE(status text)\nLANGUAGE plpgsql\nSTABLE\nSECURITY DEFINER\nS"
            "ET search_path = pg_catalog, pg_temp\n"
        ),
        (
            "\nBEGIN\n    RETURN QUERY\n    SELECT b.status::text\n    FROM finance.bills"
            " b\n    WHERE b.id = p_bill_id;\nEND;\n"
        ),
        "alembic/versions/core/core_255_insight_premise_binding.py",
    ),
    (
        "core",
        "public",
        "enqueue_premise_amendments",
        "text,text,timestamp with time zone,text",
        True,
        (
            "CREATE OR REPLACE FUNCTION public.enqueue_premise_amendments(\n    p_source "
            "text,\n    p_fingerprint text,\n    p_resolved_at timestamptz,\n    p_summar"
            "y text\n)\nRETURNS integer\nLANGUAGE plpgsql\nSECURITY DEFINER\nSET search_p"
            "ath = pg_catalog, pg_temp\n"
        ),
        (
            "\nDECLARE\n    inserted integer;\nBEGIN\n    INSERT INTO public.insight_amen"
            "dments (candidate_id, episode_key, reason, summary)\n    SELECT c.id,\n     "
            "      p_fingerprint || '@' || p_resolved_at::text,\n           'premise_reso"
            "lved',\n           p_summary\n    FROM public.insight_candidates c\n    WHER"
            "E c.status = 'delivered'\n      AND c.delivered_at <= p_resolved_at\n      A"
            "ND c.premise ->> 'kind' = 'owner_condition'\n      AND c.premise ->> 'source"
            "' = p_source\n      AND c.premise ->> 'fingerprint' = p_fingerprint\n    ON "
            "CONFLICT (candidate_id, episode_key) DO NOTHING;\n    GET DIAGNOSTICS insert"
            "ed = ROW_COUNT;\n    RETURN inserted;\nEND;\n"
        ),
        "alembic/versions/core/core_255_insight_premise_binding.py",
    ),
    (
        "core",
        "public",
        "posture_writer_allowed",
        "text",
        False,
        (
            "CREATE OR REPLACE FUNCTION public.posture_writer_allowed(role_name text)\n  "
            "      RETURNS boolean\n        LANGUAGE sql\n        IMMUTABLE\nSET search_p"
            "ath = pg_catalog, pg_temp\n"
        ),
        (
            "\n            SELECT NOT (\n                (role_name ~ '^butler_.+_rw$' OR"
            " role_name = 'connector_writer')\n                AND role_name <> 'butler_r"
            "elationship_rw'\n            )\n        "
        ),
        "alembic/versions/core/core_256_entity_posture.py",
    ),
    (
        "core",
        "public",
        "guard_entity_posture_writer",
        "",
        False,
        (
            "CREATE OR REPLACE FUNCTION public.guard_entity_posture_writer()\n        RET"
            "URNS trigger\n        LANGUAGE plpgsql\nSET search_path = pg_catalog, pg_tem"
            "p\n"
        ),
        (
            "\n        BEGIN\n            IF (NEW.posture IS DISTINCT FROM OLD.posture\n "
            "               OR NEW.posture_since IS DISTINCT FROM OLD.posture_since\n    "
            "            OR NEW.posture_set_by IS DISTINCT FROM OLD.posture_set_by)\n    "
            "           AND NOT public.posture_writer_allowed(current_user::text) THEN\n "
            "               RAISE EXCEPTION 'entity posture may only be changed by the re"
            "lationship butler'\n                    USING ERRCODE = 'insufficient_privil"
            "ege';\n            END IF;\n            RETURN NEW;\n        END\n        "
        ),
        "alembic/versions/core/core_256_entity_posture.py",
    ),
    (
        "core",
        "public",
        "qa_publication_operations_guard",
        "",
        False,
        (
            "CREATE OR REPLACE FUNCTION public.qa_publication_operations_guard() RETURNS "
            "trigger\n        LANGUAGE plpgsql\nSET search_path = pg_catalog, pg_temp\n"
        ),
        (
            "\n        \n        BEGIN\n            IF OLD.stage = 'reconciled' THEN\n   "
            "             RAISE EXCEPTION 'qa_publication_operations: a reconciled row is"
            " final'\n                    USING ERRCODE = 'check_violation';\n           "
            " END IF;\n            IF NEW.id IS DISTINCT FROM OLD.id\n                OR "
            "NEW.attempt_id IS DISTINCT FROM OLD.attempt_id\n                OR NEW.kind "
            "IS DISTINCT FROM OLD.kind\n                OR NEW.binding_digest IS DISTINCT"
            " FROM OLD.binding_digest\n                OR NEW.request_digest IS DISTINCT "
            "FROM OLD.request_digest\n                OR NEW.expected_object_id IS DISTIN"
            "CT FROM OLD.expected_object_id\n                OR NEW.started_at IS DISTINC"
            "T FROM OLD.started_at THEN\n                RAISE EXCEPTION 'qa_publication_"
            "operations: the operation binding is immutable'\n                    USING E"
            "RRCODE = 'check_violation';\n            END IF;\n\n            IF NEW.stage"
            "_order < OLD.stage_order\n                OR (NEW.stage_order = OLD.stage_or"
            "der AND NEW.stage IS DISTINCT FROM OLD.stage) THEN\n                RAISE EX"
            "CEPTION 'qa_publication_operations: a stage can only move forward'\n        "
            "            USING ERRCODE = 'check_violation';\n            END IF;\n\n     "
            "       IF OLD.new_object_id IS NOT NULL\n                AND NEW.new_object_"
            "id IS DISTINCT FROM OLD.new_object_id THEN\n                RAISE EXCEPTION "
            "'qa_publication_operations: new_object_id is set-once'\n                    "
            "USING ERRCODE = 'check_violation';\n            END IF;\n            IF OLD."
            "result IS NOT NULL AND NEW.result IS DISTINCT FROM OLD.result THEN\n        "
            "        RAISE EXCEPTION 'qa_publication_operations: result is set-once'\n   "
            "                 USING ERRCODE = 'check_violation';\n            END IF;\n  "
            "          RETURN NEW;\n        END\n\n        "
        ),
        "alembic/versions/core/core_268_qa_publication_ledger_hardening.py",
    ),
    (
        "core",
        "public",
        "qa_publication_operations_no_held_delete",
        "",
        False,
        (
            "CREATE OR REPLACE FUNCTION public.qa_publication_operations_no_held_delete()"
            " RETURNS trigger\n        LANGUAGE plpgsql\nSET search_path = pg_catalog, pg"
            "_temp\n"
        ),
        (
            "\n        BEGIN\n            IF OLD.stage IN ('egress_started', 'ambiguous')"
            " THEN\n                RAISE EXCEPTION 'qa_publication_operations: a held ro"
            "w cannot be deleted'\n                    USING ERRCODE = 'check_violation';"
            "\n            END IF;\n            RETURN OLD;\n        END\n        "
        ),
        "alembic/versions/core/core_268_qa_publication_ledger_hardening.py",
    ),
    (
        "core",
        "public",
        "resolve_health_medication_refill",
        "text,numeric,numeric",
        True,
        (
            "CREATE OR REPLACE FUNCTION public.resolve_health_medication_refill(\n    p_s"
            "ubject text,\n    p_doses_per_day numeric,\n    p_warning_days numeric\n)\nR"
            "ETURNS boolean\nLANGUAGE plpgsql\nSTABLE\nSECURITY DEFINER\nSET search_path "
            "= pg_catalog, pg_temp\n"
        ),
        (
            "\nDECLARE\n    v_id uuid;\n    v_validity text;\n    v_meta jsonb;\n    v_do"
            "ses bigint;\nBEGIN\n    SELECT f.id, f.validity, f.metadata INTO v_id, v_val"
            "idity, v_meta\n    FROM health.facts f\n    WHERE f.predicate = 'medication'"
            "\n      AND f.scope = 'health'\n      AND f.subject = p_subject\n    ORDER B"
            "Y (f.validity = 'active') DESC, f.created_at DESC\n    LIMIT 1;\n    IF NOT "
            "FOUND THEN\n        RETURN NULL;\n    END IF;\n    IF NOT (v_validity = 'act"
            "ive' AND COALESCE((v_meta ->> 'active')::boolean, false)) THEN\n        RETU"
            "RN false;\n    END IF;\n    IF v_meta ->> 'quantity' IS NULL\n       OR v_me"
            "ta ->> 'quantity_updated_at' IS NULL\n       OR p_doses_per_day IS NULL\n   "
            "    OR p_doses_per_day <= 0\n       OR p_warning_days IS NULL THEN\n        "
            "RETURN NULL;\n    END IF;\n    SELECT count(*) INTO v_doses\n    FROM health"
            ".facts d\n    WHERE d.predicate = 'took_dose'\n      AND d.scope = 'health'"
            "\n      AND d.validity = 'active'\n      AND d.metadata ->> 'medication_id' "
            "= v_id::text\n      AND d.valid_at >= (v_meta ->> 'quantity_updated_at')::ti"
            "mestamptz\n      AND COALESCE((d.metadata ->> 'skipped')::boolean, false) = "
            "false;\n    RETURN GREATEST(0, ((v_meta ->> 'quantity')::numeric - v_doses) "
            "/ p_doses_per_day)\n           <= p_warning_days;\nEND;\n"
        ),
        "alembic/versions/core/core_272_health_refill_premise_probe.py",
    ),
    (
        "core",
        "public",
        "guard_capability_catalog",
        "",
        False,
        (
            "CREATE OR REPLACE FUNCTION public.guard_capability_catalog() RETURNS trigger"
            "\n        LANGUAGE plpgsql\nSET search_path = pg_catalog, pg_temp\n"
        ),
        (
            "\n        BEGIN\n            IF TG_TABLE_NAME='capability_catalog_history' T"
            "HEN\n                RAISE EXCEPTION 'catalog history is immutable' USING ER"
            "RCODE='23514';\n            END IF;\n            IF TG_OP='DELETE' THEN\n   "
            "             RAISE EXCEPTION 'catalog registration cannot be deleted' USING "
            "ERRCODE='23514';\n            END IF;\n            IF TG_OP='INSERT' THEN\n "
            "               IF NEW.epoch<>1 THEN\n                    RAISE EXCEPTION 'ca"
            "talog first epoch must be one' USING ERRCODE='23514';\n                END I"
            "F;\n            ELSE\n                IF NEW.domain IS DISTINCT FROM OLD.dom"
            "ain\n                   OR NEW.created_at IS DISTINCT FROM OLD.created_at\n "
            "                  OR NEW.epoch NOT IN (OLD.epoch,OLD.epoch+1) THEN\n        "
            "            RAISE EXCEPTION 'catalog generation must advance' USING ERRCODE="
            "'23514';\n                END IF;\n                IF NEW.epoch=OLD.epoch AN"
            "D (\n                    NEW.instance_id IS DISTINCT FROM OLD.instance_id\n "
            "                   OR NEW.revision_id IS DISTINCT FROM OLD.revision_id\n    "
            "                OR (NOT OLD.active AND NEW.active)\n                    OR ("
            "OLD.expires_at<=clock_timestamp() AND NEW.active)\n                ) THEN\n "
            "                   RAISE EXCEPTION 'catalog lease cannot resurrect or change"
            " identity'\n                        USING ERRCODE='23514';\n                "
            "END IF;\n            END IF;\n            IF NEW.active AND NEW.expires_at>c"
            "lock_timestamp()+interval '90 seconds' THEN\n                RAISE EXCEPTION"
            " 'catalog lease exceeds bound' USING ERRCODE='23514';\n            END IF;\n"
            "            RETURN NEW;\n        END "
        ),
        "alembic/versions/core/core_275_capability_catalog_publication.py",
    ),
    (
        "core",
        "public",
        "publish_capability_catalog",
        "text,uuid,uuid,jsonb,text,text,integer,uuid",
        False,
        (
            "CREATE OR REPLACE FUNCTION public.publish_capability_catalog(\n            p"
            "_domain TEXT,p_instance UUID,p_revision UUID,p_body JSONB,\n            p_di"
            "gest TEXT,p_authorization TEXT,p_lease INTEGER,p_expected UUID\n        ) RE"
            "TURNS void LANGUAGE plpgsql\nSET search_path = pg_catalog, pg_temp\n"
        ),
        (
            "\n        DECLARE next_epoch BIGINT; prior public.capability_catalog_current"
            "%ROWTYPE;\n        BEGIN\n            IF current_user::text <> 'butler_'||p_"
            "domain||'_rw' OR p_lease<>90 THEN\n                RAISE EXCEPTION 'catalog "
            "publisher role required' USING ERRCODE='42501';\n            END IF;\n      "
            "      PERFORM 1 FROM public.capability_catalog_control WHERE singleton AND e"
            "nabled;\n            IF NOT FOUND THEN\n                RAISE EXCEPTION 'cat"
            "alog publication disabled' USING ERRCODE='42501';\n            END IF;\n    "
            "        -- Serialize first publication and replacement even without a row.\n"
            "            -- No cross-domain row lock or UPDATE authority on membership.\n"
            "            PERFORM pg_advisory_xact_lock(hashtextextended('capability_catal"
            "og:'||p_domain,0));\n            PERFORM 1 FROM public.capability_catalog_me"
            "mbers\n                WHERE domain=p_domain AND included;\n            IF N"
            "OT FOUND THEN\n                RAISE EXCEPTION 'catalog member unavailable' "
            "USING ERRCODE='42501';\n            END IF;\n            SELECT * INTO prior"
            " FROM public.capability_catalog_current\n                WHERE domain=p_doma"
            "in FOR UPDATE OF capability_catalog_current;\n            IF p_expected IS N"
            "ULL THEN\n                -- A genuinely new boot may replace the previous p"
            "rocess. A boot\n                -- that ever published cannot erase its fenc"
            "e and reacquire.\n                IF EXISTS (SELECT 1 FROM public.capability"
            "_catalog_history\n                           WHERE domain=p_domain AND insta"
            "nce_id=p_instance) THEN\n                    RAISE EXCEPTION 'catalog publis"
            "her is not current' USING ERRCODE='23514';\n                END IF;\n       "
            "     ELSIF prior.instance_id IS DISTINCT FROM p_instance\n               OR "
            "prior.revision_id IS DISTINCT FROM p_expected\n               OR prior.activ"
            "e IS DISTINCT FROM true\n               OR prior.expires_at<=clock_timestamp"
            "() THEN\n                RAISE EXCEPTION 'catalog publisher is not current' "
            "USING ERRCODE='23514';\n            END IF;\n            next_epoch:=COALESC"
            "E(prior.epoch+1,1);\n            INSERT INTO public.capability_catalog_histo"
            "ry\n                (domain,epoch,instance_id,revision_id,body,body_digest,a"
            "uthorization_digest)\n            VALUES(p_domain,next_epoch,p_instance,p_re"
            "vision,p_body,p_digest,p_authorization);\n            -- BEFORE INSERT enfor"
            "ces epoch one before conflict resolution.\n            -- The serializer and"
            " locked prior row make these separate writes\n            -- safe while pres"
            "erving the distinct INSERT and UPDATE guards.\n            IF prior.domain I"
            "S NULL THEN\n                INSERT INTO public.capability_catalog_current\n"
            "                    (domain,epoch,instance_id,revision_id,active,expires_at)"
            "\n                VALUES(p_domain,next_epoch,p_instance,p_revision,true,\n  "
            "                     clock_timestamp()+make_interval(secs=>p_lease));\n     "
            "       ELSE\n                UPDATE public.capability_catalog_current\n     "
            "               SET epoch=next_epoch,instance_id=p_instance,revision_id=p_rev"
            "ision,\n                        active=true,expires_at=clock_timestamp()+mak"
            "e_interval(secs=>p_lease),\n                        updated_at=clock_timesta"
            "mp()\n                    WHERE domain=p_domain;\n            END IF;\n     "
            "   END "
        ),
        "alembic/versions/core/core_275_capability_catalog_publication.py",
    ),
    (
        "core",
        "public",
        "renew_capability_catalog",
        "text,uuid,uuid,integer",
        False,
        (
            "CREATE OR REPLACE FUNCTION public.renew_capability_catalog(\n            p_d"
            "omain TEXT,p_instance UUID,p_revision UUID,p_lease INTEGER\n        ) RETURN"
            "S BOOLEAN LANGUAGE plpgsql\nSET search_path = pg_catalog, pg_temp\n"
        ),
        (
            "\n        BEGIN\n            IF current_user::text <> 'butler_'||p_domain||'"
            "_rw' OR p_lease<>90 THEN\n                RAISE EXCEPTION 'catalog publisher"
            " role required' USING ERRCODE='42501';\n            END IF;\n            PER"
            "FORM 1 FROM public.capability_catalog_control WHERE singleton AND enabled;\n"
            "            IF NOT FOUND THEN RETURN false; END IF;\n            PERFORM 1 F"
            "ROM public.capability_catalog_members\n                WHERE domain=p_domain"
            " AND included;\n            IF NOT FOUND THEN RETURN false; END IF;\n       "
            "     UPDATE public.capability_catalog_current\n                SET expires_a"
            "t=clock_timestamp()+make_interval(secs=>p_lease),\n                    updat"
            "ed_at=clock_timestamp()\n                WHERE domain=p_domain AND instance_"
            "id=p_instance AND revision_id=p_revision\n                  AND active AND e"
            "xpires_at>clock_timestamp();\n            RETURN FOUND;\n        END "
        ),
        "alembic/versions/core/core_275_capability_catalog_publication.py",
    ),
    (
        "core",
        "public",
        "audit_log_bind_writer",
        "",
        False,
        (
            "CREATE OR REPLACE FUNCTION public.audit_log_bind_writer()\n        RETURNS t"
            "rigger\n        LANGUAGE plpgsql\n        SECURITY INVOKER\nSET search_path "
            "= pg_catalog, pg_temp\n"
        ),
        (
            "\n        BEGIN\n            IF NEW.writer_role IS NOT NULL\n               "
            "AND NEW.writer_role IS DISTINCT FROM current_user::text THEN\n              "
            "  RAISE EXCEPTION 'audit writer identity must match the inserting database r"
            "ole'\n                    USING ERRCODE = '42501';\n            END IF;\n   "
            "         NEW.writer_role := current_user::text;\n            RETURN NEW;\n  "
            "      END;\n        "
        ),
        "alembic/versions/core/core_279_audit_writer_integrity.py",
    ),
    (
        "core",
        "public",
        "audit_log_refuse_mutation",
        "",
        False,
        (
            "CREATE OR REPLACE FUNCTION public.audit_log_refuse_mutation()\n        RETUR"
            "NS trigger\n        LANGUAGE plpgsql\n        SECURITY INVOKER\nSET search_p"
            "ath = pg_catalog, pg_temp\n"
        ),
        (
            "\n        BEGIN\n            RAISE EXCEPTION 'audit history is append-only' "
            "USING ERRCODE = '42501';\n        END;\n        "
        ),
        "alembic/versions/core/core_279_audit_writer_integrity.py",
    ),
    (
        "approvals",
        "__SELECTED_SCHEMA__",
        "prevent_approval_events_mutation",
        "",
        False,
        (
            "CREATE OR REPLACE FUNCTION __SELECTED_SCHEMA__.prevent_approval_events_mutat"
            "ion()\n        RETURNS trigger\n        LANGUAGE plpgsql\nSET search_path = "
            "pg_catalog, pg_temp\n"
        ),
        (
            "\n        BEGIN\n            RAISE EXCEPTION 'approval_events is append-only"
            ": % is not allowed', TG_OP;\n        END;\n        "
        ),
        "src/butlers/modules/approvals/migrations/001_approvals_tables.py",
    ),
    (
        "approvals",
        "__SELECTED_SCHEMA__",
        "validate_approval_event_action_reference",
        "",
        False,
        (
            "CREATE OR REPLACE FUNCTION __SELECTED_SCHEMA__.validate_approval_event_actio"
            "n_reference()\n        RETURNS trigger\n        LANGUAGE plpgsql\nSET search"
            "_path = pg_catalog, pg_temp\n"
        ),
        (
            "\n        BEGIN\n            IF NEW.action_id IS NULL THEN\n                "
            "RETURN NEW;\n            END IF;\n\n            PERFORM 1\n            FROM "
            "__SELECTED_SCHEMA__.pending_actions\n            WHERE id = NEW.action_id\n "
            "           FOR KEY SHARE;\n\n            IF NOT FOUND THEN\n                "
            "RAISE EXCEPTION\n                    'approval event action_id % does not re"
            "ference a live pending action',\n                    NEW.action_id\n        "
            "            USING ERRCODE = 'foreign_key_violation';\n            END IF;\n"
            "\n            RETURN NEW;\n        END;\n        "
        ),
        "src/butlers/modules/approvals/migrations/008_retain_event_action_provenance.py",
    ),
    (
        "approvals",
        "__SELECTED_SCHEMA__",
        "validate_approval_rule_creator_reference",
        "",
        False,
        (
            "CREATE OR REPLACE FUNCTION __SELECTED_SCHEMA__.validate_approval_rule_creato"
            "r_reference()\n        RETURNS trigger\n        LANGUAGE plpgsql\nSET search"
            "_path = pg_catalog, pg_temp\n"
        ),
        (
            "\n        BEGIN\n            IF NEW.created_from IS NULL THEN\n             "
            "   RETURN NEW;\n            END IF;\n\n            PERFORM 1\n            FR"
            "OM __SELECTED_SCHEMA__.pending_actions\n            WHERE id = NEW.created_f"
            "rom\n            FOR KEY SHARE;\n\n            IF NOT FOUND THEN\n          "
            "      RAISE EXCEPTION\n                    'approval rule created_from % doe"
            "s not reference a live pending action',\n                    NEW.created_fro"
            "m\n                    USING ERRCODE = 'foreign_key_violation';\n           "
            " END IF;\n\n            RETURN NEW;\n        END;\n        "
        ),
        "src/butlers/modules/approvals/migrations/009_retain_rule_creator_provenance.py",
    ),
    (
        "approvals",
        "__SELECTED_SCHEMA__",
        "validate_approval_event_rule_reference",
        "",
        False,
        (
            "CREATE OR REPLACE FUNCTION __SELECTED_SCHEMA__.validate_approval_event_rule_"
            "reference()\n        RETURNS trigger\n        LANGUAGE plpgsql\nSET search_p"
            "ath = pg_catalog, pg_temp\n"
        ),
        (
            "\n        BEGIN\n            IF NEW.rule_id IS NULL THEN\n                RE"
            "TURN NEW;\n            END IF;\n\n            PERFORM 1\n            FROM __"
            "SELECTED_SCHEMA__.approval_rules\n            WHERE id = NEW.rule_id\n      "
            "      FOR KEY SHARE;\n\n            IF NOT FOUND THEN\n                RAISE"
            " EXCEPTION\n                    'approval event rule_id % does not reference"
            " a live approval rule',\n                    NEW.rule_id\n                  "
            "  USING ERRCODE = 'foreign_key_violation';\n            END IF;\n\n         "
            "   RETURN NEW;\n        END;\n        "
        ),
        "src/butlers/modules/approvals/migrations/011_retain_event_rule_provenance.py",
    ),
    (
        "approvals",
        "__SELECTED_SCHEMA__",
        "validate_approval_delivery_presentation_subject",
        "",
        False,
        (
            "CREATE OR REPLACE FUNCTION __SELECTED_SCHEMA__.validate_approval_delivery_pr"
            "esentation_subject()\n        RETURNS trigger LANGUAGE plpgsql\nSET search_p"
            "ath = pg_catalog, pg_temp\n"
        ),
        (
            "\n        BEGIN\n            IF NEW.subject_kind = 'action' AND NOT EXISTS ("
            "\n                SELECT 1 FROM __SELECTED_SCHEMA__.approval_delivery_intent"
            "s\n                 WHERE id = NEW.intent_id AND action_key = NEW.subject_ke"
            "y\n            ) THEN\n                RAISE EXCEPTION 'approval delivery ac"
            "tion subject/key mismatch';\n            ELSIF NEW.subject_kind = 'cohort' A"
            "ND NOT EXISTS (\n                SELECT 1 FROM __SELECTED_SCHEMA__.approval_"
            "delivery_cohorts\n                 WHERE id = NEW.cohort_id AND cohort_key ="
            " NEW.subject_key\n            ) THEN\n                RAISE EXCEPTION 'appro"
            "val delivery cohort subject/key mismatch';\n            END IF;\n           "
            " RETURN NEW;\n        END "
        ),
        "src/butlers/modules/approvals/migrations/015_delivery_intent_recovery.py",
    ),
    (
        "approvals",
        "__SELECTED_SCHEMA__",
        "prevent_approval_delivery_attempts_mutation",
        "",
        False,
        (
            "CREATE OR REPLACE FUNCTION __SELECTED_SCHEMA__.prevent_approval_delivery_att"
            "empts_mutation()\n        RETURNS trigger LANGUAGE plpgsql\nSET search_path "
            "= pg_catalog, pg_temp\n"
        ),
        (
            "\n        BEGIN\n            IF TG_OP = 'DELETE' AND pg_trigger_depth() > 1 "
            "THEN\n                RETURN OLD;\n            END IF;\n            RAISE EX"
            "CEPTION 'approval_delivery_attempts is append-only: % is not allowed', TG_OP"
            ";\n        END "
        ),
        "src/butlers/modules/approvals/migrations/016_retention_cascade_attempt_cleanup.py",
    ),
    (
        "memory",
        "__SELECTED_SCHEMA__",
        "predicate_registry_search_vector_trigger",
        "",
        False,
        (
            "CREATE OR REPLACE FUNCTION __SELECTED_SCHEMA__.predicate_registry_search_vec"
            "tor_trigger()\n        RETURNS trigger LANGUAGE plpgsql\nSET search_path = p"
            "g_catalog, pg_temp\n"
        ),
        (
            "\n        BEGIN\n            NEW.search_vector :=\n                setweight"
            "(to_tsvector('english', coalesce(NEW.name, '')), 'A') ||\n                se"
            "tweight(to_tsvector('english',\n                    coalesce(array_to_string"
            "(NEW.aliases, ' '), '')), 'A') ||\n                setweight(to_tsvector('en"
            "glish', coalesce(NEW.description, '')), 'B');\n            RETURN NEW;\n    "
            "    END;\n        "
        ),
        "src/butlers/modules/memory/migrations/001_memory_schema.py",
    ),
    (
        "memory",
        "__SELECTED_SCHEMA__",
        "record_episode_tombstone",
        "",
        False,
        (
            "CREATE OR REPLACE FUNCTION __SELECTED_SCHEMA__.record_episode_tombstone()\n "
            "       RETURNS trigger\n        LANGUAGE plpgsql\nSET search_path = pg_catal"
            "og, pg_temp\n"
        ),
        (
            "\n        BEGIN\n            -- The trigger's relation owns the tombstone.  "
            "Do not allow a\n            -- same-named table later in the search path to "
            "receive evidence.\n            EXECUTE format(\n                'INSERT INTO"
            " %I.episode_tombstones (episode_id) '\n                'VALUES ($1) ON CONFL"
            "ICT (episode_id) DO NOTHING',\n                TG_TABLE_SCHEMA\n            "
            ") USING OLD.id;\n            RETURN OLD;\n        END;\n        "
        ),
        "src/butlers/modules/memory/migrations/010_preserve_episode_provenance.py",
    ),
    (
        "education",
        "education",
        "require_active_map_content",
        "",
        False,
        (
            "CREATE OR REPLACE FUNCTION education.require_active_map_content()\n        R"
            "ETURNS trigger LANGUAGE plpgsql\nSET search_path = pg_catalog, pg_temp\n"
        ),
        (
            "\n        BEGIN\n            IF NEW.status = 'active' AND NOT EXISTS (\n    "
            "            SELECT 1 FROM education.mind_map_nodes WHERE mind_map_id = NEW.i"
            "d\n            ) THEN\n                RAISE EXCEPTION 'An active curriculum"
            " requires at least one concept'\n                    USING ERRCODE = '23514'"
            ";\n            END IF;\n            RETURN NEW;\n        END "
        ),
        "roster/education/migrations/006_mind_map_lifecycle.py",
    ),
    (
        "education",
        "education",
        "serialize_concept_removal",
        "",
        False,
        (
            "CREATE OR REPLACE FUNCTION education.serialize_concept_removal()\n        RE"
            "TURNS trigger LANGUAGE plpgsql\nSET search_path = pg_catalog, pg_temp\n"
        ),
        (
            "\n        DECLARE parent_id uuid; destination_id uuid;\n        BEGIN\n     "
            "       IF TG_OP = 'UPDATE' THEN\n                IF OLD.mind_map_id = NEW.mi"
            "nd_map_id THEN RETURN NEW; END IF;\n                destination_id := NEW.mi"
            "nd_map_id;\n            END IF;\n            FOR parent_id IN\n             "
            "   SELECT id FROM education.mind_maps\n                WHERE id = OLD.mind_m"
            "ap_id\n                   OR id = destination_id\n                ORDER BY i"
            "d\n            LOOP\n                UPDATE education.mind_maps SET updated_"
            "at = updated_at WHERE id = parent_id;\n            END LOOP;\n            IF"
            " TG_OP = 'UPDATE' THEN RETURN NEW; END IF;\n            RETURN OLD;\n       "
            " END "
        ),
        "roster/education/migrations/006_mind_map_lifecycle.py",
    ),
    (
        "education",
        "education",
        "require_content_after_removal",
        "",
        False,
        (
            "CREATE OR REPLACE FUNCTION education.require_content_after_removal()\n      "
            "  RETURNS trigger LANGUAGE plpgsql\nSET search_path = pg_catalog, pg_temp\n"
        ),
        (
            "\n        BEGIN\n            IF EXISTS (SELECT 1 FROM education.mind_maps\n "
            "                      WHERE id = OLD.mind_map_id AND status = 'active')\n   "
            "            AND NOT EXISTS (SELECT 1 FROM education.mind_map_nodes\n        "
            "                       WHERE mind_map_id = OLD.mind_map_id) THEN\n          "
            "      RAISE EXCEPTION 'Cannot remove the final concept of an active curricul"
            "um'\n                    USING ERRCODE = '23514';\n            END IF;\n    "
            "        RETURN OLD;\n        END "
        ),
        "roster/education/migrations/006_mind_map_lifecycle.py",
    ),
    (
        "general",
        "__SELECTED_SCHEMA__",
        "collections_classification_guard",
        "",
        False,
        (
            "CREATE OR REPLACE FUNCTION __SELECTED_SCHEMA__.collections_classification_gu"
            "ard()\n        RETURNS trigger\n        LANGUAGE plpgsql\nSET search_path = "
            "pg_catalog, pg_temp\n"
        ),
        (
            "\n        BEGIN\n            IF NEW.custody_private IS DISTINCT FROM OLD.cus"
            "tody_private THEN\n                IF OLD.custody_private THEN\n            "
            "        RAISE EXCEPTION 'a private collection classification cannot be clear"
            "ed'\n                        USING ERRCODE = '55000';\n                END I"
            "F;\n                NEW.eligibility_generation := OLD.eligibility_generation"
            " + 1;\n            ELSIF NEW.eligibility_generation IS DISTINCT FROM OLD.eli"
            "gibility_generation THEN\n                RAISE EXCEPTION 'eligibility_gener"
            "ation only advances with classification'\n                    USING ERRCODE "
            "= '55000';\n            END IF;\n            RETURN NEW;\n        END;\n    "
            "    "
        ),
        "roster/general/migrations/003_ordinary_source_authority.py",
    ),
    (
        "general",
        "__SELECTED_SCHEMA__",
        "collection_vocabulary_key",
        "text",
        False,
        (
            "CREATE OR REPLACE FUNCTION __SELECTED_SCHEMA__.collection_vocabulary_key(spe"
            "lling TEXT)\n        RETURNS TEXT\n        LANGUAGE sql\n        IMMUTABLE\n"
            "        STRICT\n        PARALLEL SAFE\nSET search_path = pg_catalog, pg_temp"
            "\n"
        ),
        (
            "\n            SELECT pg_catalog.btrim(\n                pg_catalog.regexp_re"
            "place(\n                    pg_catalog.regexp_replace(\n                    "
            "    pg_catalog.lower(spelling), '[[:space:]_-]+', '-', 'g'\n                "
            "    ),\n                    '[^[:alnum:]-]', '', 'g'\n                ),\n  "
            "              '-'\n            )\n        "
        ),
        "roster/general/migrations/003_ordinary_source_authority.py",
    ),
    (
        "general",
        "__SELECTED_SCHEMA__",
        "collection_vocabulary_keys_stamp",
        "",
        False,
        (
            "CREATE OR REPLACE FUNCTION __SELECTED_SCHEMA__.collection_vocabulary_keys_st"
            "amp()\n        RETURNS trigger\n        LANGUAGE plpgsql\nSET search_path = "
            "pg_catalog, pg_temp\n"
        ),
        (
            "\n        BEGIN\n            SELECT parent.custody_private INTO NEW.custody_"
            "private\n            FROM __SELECTED_SCHEMA__.collections AS parent\n       "
            "     WHERE parent.id = NEW.collection_id\n            FOR SHARE;\n          "
            "  IF NEW.custody_private IS NULL THEN\n                RAISE EXCEPTION 'voca"
            "bulary key parent is unavailable' USING ERRCODE = '23503';\n            END "
            "IF;\n            RETURN NEW;\n        END;\n        "
        ),
        "roster/general/migrations/003_ordinary_source_authority.py",
    ),
    (
        "general",
        "__SELECTED_SCHEMA__",
        "collections_classification_mirror",
        "",
        False,
        (
            "CREATE OR REPLACE FUNCTION __SELECTED_SCHEMA__.collections_classification_mi"
            "rror()\n        RETURNS trigger\n        LANGUAGE plpgsql\nSET search_path ="
            " pg_catalog, pg_temp\n"
        ),
        (
            "\n        BEGIN\n            UPDATE __SELECTED_SCHEMA__.collection_vocabular"
            "y_keys\n            SET custody_private = NEW.custody_private\n            W"
            "HERE collection_id = NEW.id;\n            RETURN NULL;\n        END;\n      "
            "  "
        ),
        "roster/general/migrations/003_ordinary_source_authority.py",
    ),
    (
        "general",
        "__SELECTED_SCHEMA__",
        "source_versions_immutable",
        "",
        False,
        (
            "CREATE OR REPLACE FUNCTION __SELECTED_SCHEMA__.source_versions_immutable()\n"
            "        RETURNS trigger\n        LANGUAGE plpgsql\nSET search_path = pg_cata"
            "log, pg_temp\n"
        ),
        (
            "\n        BEGIN\n            RAISE EXCEPTION 'General source versions are im"
            "mutable' USING ERRCODE = '55000';\n        END;\n        "
        ),
        "roster/general/migrations/003_ordinary_source_authority.py",
    ),
    (
        "relationship",
        "relationship",
        "reject_fact_evidence_update",
        "",
        False,
        (
            "CREATE OR REPLACE FUNCTION relationship.reject_fact_evidence_update()\n     "
            "   RETURNS trigger\nSET search_path = pg_catalog, pg_temp\n"
        ),
        (
            "\n        BEGIN\n            RAISE EXCEPTION\n                'relationship."
            "fact_evidence is append-only; evidence row % cannot be rewritten',\n        "
            "        OLD.id\n                USING ERRCODE = 'integrity_constraint_violat"
            "ion';\n        END;\n        "
        ),
        "roster/relationship/migrations/034_fact_evidence_and_coverage.py",
    ),
    (
        "relationship",
        "relationship",
        "guard_fact_report_authority",
        "",
        False,
        (
            "CREATE FUNCTION relationship.guard_fact_report_authority() RETURNS trigger\n"
            "        LANGUAGE plpgsql\nSET search_path = pg_catalog, pg_temp\n"
        ),
        (
            " BEGIN\n            IF NEW.content_authority IS DISTINCT FROM OLD.content_au"
            "thority OR\n               NEW.authority_original_entity_id IS DISTINCT FROM"
            " OLD.authority_original_entity_id OR\n               NEW.authority_entity_cr"
            "eated_at IS DISTINCT FROM OLD.authority_entity_created_at OR\n              "
            " (NEW.authority_entity_id IS DISTINCT FROM OLD.authority_entity_id AND\n    "
            "            NEW.authority_entity_id IS NOT NULL) THEN\n                RAISE"
            " EXCEPTION 'fact report attribution is immutable within a version';\n       "
            "     END IF;\n            RETURN NEW;\n        END "
        ),
        "roster/relationship/migrations/037_fact_content_authority.py",
    ),
    (
        "switchboard",
        "switchboard",
        "switchboard_connector_heartbeat_log_ensure_partition",
        "timestamp with time zone",
        True,
        (
            "CREATE OR REPLACE FUNCTION switchboard.switchboard_connector_heartbeat_log_e"
            "nsure_partition(\n            reference_ts TIMESTAMPTZ DEFAULT now()\n      "
            "  ) RETURNS TEXT\n        LANGUAGE plpgsql\n        SECURITY DEFINER\nSET se"
            "arch_path = pg_catalog, pg_temp\n"
        ),
        (
            "\nDECLARE\n    c_schema CONSTANT text := 'switchboard';\n    month_start    "
            "TIMESTAMPTZ;\n    month_end      TIMESTAMPTZ;\n    partition_name TEXT;\n   "
            " next_start     TIMESTAMPTZ;\n    next_end       TIMESTAMPTZ;\n    next_name"
            "      TEXT;\nBEGIN\n\n    -- One lock covers both the requested and proactiv"
            "ely-created next\n    -- partition, including overlapping calls for adjacent"
            " months.\n    PERFORM pg_advisory_xact_lock(\n        hashtext(c_schema),\n "
            "       hashtext('switchboard_connector_heartbeat_log_ensure_partition')\n   "
            " );\n\n    month_start    := date_trunc('month', reference_ts);\n    month_e"
            "nd      := month_start + INTERVAL '1 month';\n    partition_name := format('"
            "connector_heartbeat_log_p%s', to_char(month_start, 'YYYYMM'));\n\n    EXECUT"
            "E format(\n        'CREATE TABLE IF NOT EXISTS %I.%I PARTITION OF %I.connect"
            "or_heartbeat_log '\n        'FOR VALUES FROM (%L) TO (%L)',\n        c_schem"
            "a, partition_name, c_schema, month_start, month_end\n    );\n\n    -- Next m"
            "onth partition (proactive)\n    next_start := month_end;\n    next_end   := "
            "next_start + INTERVAL '1 month';\n    next_name  := format('connector_heartb"
            "eat_log_p%s', to_char(next_start, 'YYYYMM'));\n\n    EXECUTE format(\n      "
            "  'CREATE TABLE IF NOT EXISTS %I.%I PARTITION OF %I.connector_heartbeat_log "
            "'\n        'FOR VALUES FROM (%L) TO (%L)',\n        c_schema, next_name, c_s"
            "chema, next_start, next_end\n    );\n\n    RETURN partition_name;\nEND;\n"
        ),
        "roster/switchboard/migrations/039_pin_definer_search_paths.py",
    ),
    (
        "switchboard",
        "switchboard",
        "switchboard_connector_heartbeat_log_drop_expired_partitions",
        "interval,timestamp with time zone",
        True,
        (
            "CREATE OR REPLACE FUNCTION switchboard.switchboard_connector_heartbeat_log_d"
            "rop_expired_partitions(\n            retention INTERVAL DEFAULT INTERVAL '7 "
            "days',\n            reference_ts TIMESTAMPTZ DEFAULT now()\n        ) RETURN"
            "S INTEGER\n        LANGUAGE plpgsql\n        SECURITY DEFINER\nSET search_pa"
            "th = pg_catalog, pg_temp\n"
        ),
        (
            "\nDECLARE\n    c_schema CONSTANT text := 'switchboard';\n    partition_name "
            "TEXT;\n    partition_month DATE;\n    cutoff_month DATE;\n    dropped_count "
            "INTEGER := 0;\nBEGIN\n    cutoff_month := date_trunc('month', reference_ts -"
            " retention)::date;\n\n    FOR partition_name IN\n        SELECT child.relnam"
            "e\n        FROM pg_catalog.pg_inherits\n        JOIN pg_catalog.pg_class par"
            "ent ON parent.oid = pg_inherits.inhparent\n        JOIN pg_catalog.pg_namesp"
            "ace parent_ns ON parent_ns.oid = parent.relnamespace\n        JOIN pg_catalo"
            "g.pg_class child ON child.oid = pg_inherits.inhrelid\n        JOIN pg_catalo"
            "g.pg_namespace ns ON ns.oid = child.relnamespace\n        WHERE parent.relna"
            "me = 'connector_heartbeat_log'\n        AND parent_ns.nspname = c_schema\n  "
            "      AND ns.nspname = c_schema\n        AND child.relname ~ '^connector_hea"
            "rtbeat_log_p[0-9]{6}$'\n    LOOP\n        partition_month := to_date(substri"
            "ng(partition_name from '[0-9]{6}$'), 'YYYYMM');\n        IF partition_month "
            "< cutoff_month THEN\n            EXECUTE format('DROP TABLE IF EXISTS %I.%I'"
            ", c_schema, partition_name);\n            dropped_count := dropped_count + 1"
            ";\n        END IF;\n    END LOOP;\n\n    RETURN dropped_count;\nEND;\n"
        ),
        "roster/switchboard/migrations/039_pin_definer_search_paths.py",
    ),
    (
        "switchboard",
        "switchboard",
        "switchboard_prune_email_metadata_refs",
        "interval,timestamp with time zone",
        False,
        (
            "CREATE OR REPLACE FUNCTION switchboard.switchboard_prune_email_metadata_refs"
            "(\n            retention INTERVAL DEFAULT INTERVAL '90 days',\n            r"
            "eference_ts TIMESTAMPTZ DEFAULT now()\n        ) RETURNS INTEGER\n        LA"
            "NGUAGE plpgsql\nSET search_path = pg_catalog, pg_temp\n"
        ),
        (
            "\n        DECLARE\n            deleted_count INTEGER;\n        BEGIN\n      "
            "      WITH deleted AS (\n                DELETE FROM switchboard.email_metad"
            "ata_refs\n                WHERE created_at < (reference_ts - retention)\n   "
            "             RETURNING 1\n            )\n            SELECT COUNT(*) INTO de"
            "leted_count FROM deleted;\n\n            RETURN deleted_count;\n        END;"
            "\n        "
        ),
        "roster/switchboard/migrations/004_switchboard_email.py",
    ),
    (
        "switchboard",
        "switchboard",
        "preserve_registry_policy",
        "",
        False,
        (
            "CREATE OR REPLACE FUNCTION switchboard.preserve_registry_policy()\n        R"
            "ETURNS trigger\n        LANGUAGE plpgsql\nSET search_path = pg_catalog, pg_t"
            "emp\n"
        ),
        (
            "\n        DECLARE\n            v_policy text;\n            v_previous_quaran"
            "tined_at timestamptz;\n            v_previous_reason text;\n        BEGIN\n "
            "           SELECT policy_state INTO v_policy\n            FROM switchboard.b"
            "utler_registry_control_plane\n            WHERE name = NEW.name;\n          "
            "  IF v_policy IN ('paused', 'quarantined', 'review_required') THEN\n        "
            "        IF TG_OP = 'UPDATE' THEN\n                    v_previous_quarantined"
            "_at := OLD.quarantined_at;\n                    v_previous_reason := OLD.qua"
            "rantine_reason;\n                END IF;\n                NEW.eligibility_st"
            "ate := 'quarantined';\n                NEW.quarantined_at := COALESCE(\n    "
            "                v_previous_quarantined_at, NEW.quarantined_at, clock_timesta"
            "mp()\n                );\n                NEW.quarantine_reason := COALESCE("
            "\n                    v_previous_reason, NEW.quarantine_reason, 'protected_p"
            "olicy:' || v_policy\n                );\n            END IF;\n            RE"
            "TURN NEW;\n        END;\n        "
        ),
        "roster/switchboard/migrations/035_registry_control_plane.py",
    ),
    (
        "switchboard",
        "switchboard",
        "ensure_registry_control",
        "",
        True,
        (
            "CREATE OR REPLACE FUNCTION switchboard.ensure_registry_control()\n        RE"
            "TURNS trigger\n        LANGUAGE plpgsql SECURITY DEFINER\nSET search_path = "
            "pg_catalog, pg_temp\n"
        ),
        (
            "\n        BEGIN\n            INSERT INTO switchboard.butler_registry_control"
            "_plane (name)\n            VALUES (NEW.name)\n            ON CONFLICT (name)"
            " DO NOTHING;\n            RETURN NEW;\n        END;\n        "
        ),
        "roster/switchboard/migrations/035_registry_control_plane.py",
    ),
    (
        "switchboard",
        "public",
        "register_butler_boot",
        "text,uuid",
        True,
        (
            "CREATE OR REPLACE FUNCTION public.register_butler_boot(\n            p_name "
            "text, p_instance uuid\n        ) RETURNS bigint\n        LANGUAGE plpgsql SE"
            "CURITY DEFINER\nSET search_path = pg_catalog, pg_temp\n"
        ),
        (
            "\n        DECLARE\n            v_epoch bigint;\n            v_current_epoch "
            "bigint;\n            v_current_instance uuid;\n            v_prior_epoch big"
            "int;\n        BEGIN\n            IF p_instance IS NULL OR p_name IS NULL\n  "
            "             OR current_setting('role', true) IS DISTINCT FROM\n            "
            "        'butler_' || p_name || '_rw' THEN\n                RAISE EXCEPTION '"
            "boot registration requires the matching runtime role'\n                    U"
            "SING ERRCODE = '42501';\n            END IF;\n            IF NOT EXISTS (\n "
            "               SELECT 1 FROM switchboard.butler_registry WHERE name = p_name"
            "\n            ) THEN\n                RAISE EXCEPTION 'unknown roster regist"
            "ration'\n                    USING ERRCODE = '22023';\n            END IF;\n"
            "            INSERT INTO switchboard.butler_registry_control_plane (name)\n  "
            "          VALUES (p_name) ON CONFLICT (name) DO NOTHING;\n            SELECT"
            " boot_epoch, boot_instance_id\n              INTO v_current_epoch, v_current"
            "_instance\n              FROM switchboard.butler_registry_control_plane\n   "
            "          WHERE name = p_name\n             FOR UPDATE;\n            SELECT "
            "boot_epoch INTO v_prior_epoch\n              FROM switchboard.butler_boot_re"
            "gistrations\n             WHERE name = p_name AND boot_instance_id = p_insta"
            "nce;\n            IF v_prior_epoch IS NOT NULL THEN\n                IF v_pr"
            "ior_epoch = v_current_epoch\n                   AND v_current_instance = p_i"
            "nstance THEN\n                    RETURN v_prior_epoch;\n                END"
            " IF;\n                RAISE EXCEPTION 'superseded boot registration cannot b"
            "ecome current'\n                    USING ERRCODE = '55000';\n            EN"
            "D IF;\n            v_epoch := v_current_epoch + 1;\n            INSERT INTO "
            "switchboard.butler_boot_registrations (\n                name, boot_instance"
            "_id, boot_epoch\n            ) VALUES (p_name, p_instance, v_epoch);\n      "
            "      UPDATE switchboard.butler_registry_control_plane\n               SET b"
            "oot_epoch = v_epoch,\n                   boot_instance_id = p_instance,\n   "
            "                observed_state = 'observer_unknown',\n                   obs"
            "erved_boot_epoch = NULL,\n                   route_compatible = NULL,\n     "
            "              accepting_routes = NULL,\n                   probe_failure_cla"
            "ss = NULL,\n                   updated_at = clock_timestamp()\n             "
            "WHERE name = p_name;\n            RETURN v_epoch;\n        END;\n        "
        ),
        "roster/switchboard/migrations/035_registry_control_plane.py",
    ),
    (
        "switchboard",
        "public",
        "reserve_butler_probe",
        "text",
        True,
        (
            "CREATE OR REPLACE FUNCTION public.reserve_butler_probe(p_name text)\n       "
            " RETURNS TABLE(boot_epoch bigint, probe_sequence bigint)\n        LANGUAGE p"
            "lpgsql SECURITY DEFINER\nSET search_path = pg_catalog, pg_temp\n"
        ),
        (
            "\n        BEGIN\n            IF current_setting('role', true) IS DISTINCT FR"
            "OM\n                    'butler_switchboard_rw'\n               AND NOT (\n "
            "                   current_setting('role', true) = 'dashboard_api'\n        "
            "       ) THEN\n                RAISE EXCEPTION 'probe reservation requires a"
            " control-plane role'\n                    USING ERRCODE = '42501';\n        "
            "    END IF;\n            RETURN QUERY\n            UPDATE switchboard.butler"
            "_registry_control_plane AS c\n               SET probe_sequence = c.probe_se"
            "quence + 1,\n                   updated_at = clock_timestamp()\n            "
            " WHERE c.name = p_name AND c.boot_epoch > 0\n             RETURNING c.boot_e"
            "poch, c.probe_sequence;\n        END;\n        "
        ),
        "roster/switchboard/migrations/035_registry_control_plane.py",
    ),
    (
        "switchboard",
        "public",
        "record_butler_probe",
        "text,bigint,bigint,boolean,boolean,boolean,text",
        True,
        (
            "CREATE OR REPLACE FUNCTION public.record_butler_probe(\n            p_name t"
            "ext, p_epoch bigint, p_sequence bigint,\n            p_healthy boolean, p_co"
            "mpatible boolean, p_accepting boolean,\n            p_failure_class text\n  "
            "      ) RETURNS boolean\n        LANGUAGE plpgsql SECURITY DEFINER\nSET sear"
            "ch_path = pg_catalog, pg_temp\n"
        ),
        (
            "\n        DECLARE v_updated boolean;\n        BEGIN\n            IF current_"
            "setting('role', true) IS DISTINCT FROM\n                    'butler_switchbo"
            "ard_rw'\n               AND NOT (\n                    current_setting('role"
            "', true) = 'dashboard_api'\n               ) THEN\n                RAISE EXC"
            "EPTION 'probe recording requires a control-plane role'\n                    "
            "USING ERRCODE = '42501';\n            END IF;\n            IF p_healthy IS N"
            "ULL OR p_epoch IS NULL OR p_sequence IS NULL\n               OR (p_healthy A"
            "ND (p_compatible IS NULL OR p_accepting IS NULL)) THEN\n                RAIS"
            "E EXCEPTION 'probe outcome and fence are required'\n                    USIN"
            "G ERRCODE = '22023';\n            END IF;\n            UPDATE switchboard.bu"
            "tler_registry_control_plane AS c\n               SET recorded_probe_sequence"
            " = p_sequence,\n                   last_probe_at = clock_timestamp(),\n     "
            "              healthy_observed_at = CASE\n                       WHEN p_heal"
            "thy THEN clock_timestamp()\n                       ELSE c.healthy_observed_a"
            "t END,\n                   observed_boot_epoch = p_epoch,\n                 "
            "  observed_state = CASE\n                       WHEN p_healthy THEN 'healthy"
            "' ELSE 'unavailable' END,\n                   route_compatible = CASE WHEN p"
            "_healthy THEN p_compatible ELSE NULL END,\n                   accepting_rout"
            "es = CASE WHEN p_healthy THEN p_accepting ELSE NULL END,\n                  "
            " probe_failure_class = CASE\n                       WHEN p_healthy THEN NULL"
            "\n                       WHEN p_failure_class IN (\n                        "
            "   'timeout', 'connection', 'invalid_identity',\n                           "
            "'invalid_contract', 'not_accepting'\n                       ) THEN p_failure"
            "_class\n                       ELSE 'observer_error' END,\n                 "
            "  updated_at = clock_timestamp()\n             WHERE c.name = p_name\n      "
            "         AND c.boot_epoch = p_epoch\n               AND c.boot_epoch > 0\n  "
            "             AND c.probe_sequence = p_sequence\n               AND c.recorde"
            "d_probe_sequence < p_sequence;\n            v_updated := FOUND;\n           "
            " RETURN v_updated;\n        END;\n        "
        ),
        "roster/switchboard/migrations/035_registry_control_plane.py",
    ),
    (
        "switchboard",
        "public",
        "set_butler_registry_policy",
        "text,text",
        True,
        (
            "CREATE OR REPLACE FUNCTION public.set_butler_registry_policy(\n            p"
            "_name text, p_policy text\n        ) RETURNS text\n        LANGUAGE plpgsql "
            "SECURITY DEFINER\nSET search_path = pg_catalog, pg_temp\n"
        ),
        (
            "\n        DECLARE v_legacy text;\n        BEGIN\n            IF current_sett"
            "ing('role', true) IS DISTINCT FROM 'dashboard_api' THEN\n                RAI"
            "SE EXCEPTION 'operator policy requires the dashboard owner boundary'\n      "
            "              USING ERRCODE = '42501';\n            END IF;\n            IF "
            "p_policy NOT IN ('active', 'paused', 'quarantined', 'review_required')\n    "
            "           OR p_policy IS NULL THEN\n                RAISE EXCEPTION 'invali"
            "d registry policy' USING ERRCODE = '22023';\n            END IF;\n          "
            "  IF NOT EXISTS (\n                SELECT 1 FROM switchboard.butler_registry"
            " WHERE name = p_name\n            ) THEN\n                RAISE EXCEPTION 'u"
            "nknown registry target' USING ERRCODE = '22023';\n            END IF;\n     "
            "       INSERT INTO switchboard.butler_registry_control_plane (name)\n       "
            "     VALUES (p_name) ON CONFLICT (name) DO NOTHING;\n            UPDATE swit"
            "chboard.butler_registry_control_plane\n               SET policy_state = p_p"
            "olicy,\n                   policy_actor = 'owner',\n                   polic"
            "y_reason = 'operator_action',\n                   policy_changed_at = clock_"
            "timestamp(),\n                   policy_provenance = 'operator',\n          "
            "         updated_at = clock_timestamp()\n             WHERE name = p_name;\n"
            "            v_legacy := CASE WHEN p_policy = 'active' THEN 'active'\n       "
            "                      ELSE 'quarantined' END;\n            UPDATE switchboar"
            "d.butler_registry\n               SET eligibility_state = v_legacy,\n       "
            "            eligibility_updated_at = clock_timestamp(),\n                   "
            "quarantined_at = CASE WHEN v_legacy = 'active'\n                            "
            "             THEN NULL ELSE clock_timestamp() END,\n                   quara"
            "ntine_reason = CASE WHEN v_legacy = 'active' THEN NULL\n                    "
            "                        ELSE 'operator_' || p_policy END\n             WHERE"
            " name = p_name;\n            RETURN v_legacy;\n        END;\n        "
        ),
        "roster/switchboard/migrations/035_registry_control_plane.py",
    ),
    (
        "switchboard",
        "switchboard",
        "goal_ledger_immutable",
        "",
        False,
        (
            "CREATE OR REPLACE FUNCTION switchboard.goal_ledger_immutable()\n        RETU"
            "RNS trigger LANGUAGE plpgsql\nSET search_path = pg_catalog, pg_temp\n"
        ),
        (
            "\n        BEGIN\n            IF TG_TABLE_NAME='admission_decisions' THEN\n  "
            "              RAISE EXCEPTION 'Admission history is immutable';\n           "
            " ELSIF TG_TABLE_NAME='goals' THEN\n                -- thread_id may be bound"
            " once, after admission; never rebound.\n                IF (to_jsonb(NEW) - "
            "ARRAY['status','exposed','thread_id','updated_at'])\n                   IS D"
            "ISTINCT FROM\n                   (to_jsonb(OLD) - ARRAY['status','exposed','"
            "thread_id','updated_at'])\n                   OR (OLD.exposed AND NOT NEW.ex"
            "posed)\n                   OR (OLD.thread_id IS NOT NULL\n                  "
            "     AND NEW.thread_id IS DISTINCT FROM OLD.thread_id) THEN\n               "
            "     RAISE EXCEPTION 'Goal authority and source association are immutable';"
            "\n                END IF;\n            ELSIF TG_TABLE_NAME='invocations' THE"
            "N\n                IF (NEW.goal_id,NEW.parent_invocation_id,NEW.depth,NEW.ro"
            "le,NEW.lead_epoch,\n                    NEW.grant_snapshot,NEW.grant_digest,"
            "NEW.max_turns,NEW.max_tokens) IS DISTINCT FROM\n                   (OLD.goal"
            "_id,OLD.parent_invocation_id,OLD.depth,OLD.role,OLD.lead_epoch,\n           "
            "         OLD.grant_snapshot,OLD.grant_digest,OLD.max_turns,OLD.max_tokens)\n"
            "                   OR (OLD.exposed AND NOT NEW.exposed) THEN\n              "
            "      RAISE EXCEPTION 'Invocation lineage, grant and exposure are immutable'"
            ";\n                END IF;\n            ELSIF TG_TABLE_NAME='goal_inbox' THE"
            "N\n                IF (to_jsonb(NEW) - 'consumed_at') IS DISTINCT FROM (to_j"
            "sonb(OLD) - 'consumed_at')\n                   OR (OLD.consumed_at IS NOT NU"
            "LL\n                       AND NEW.consumed_at IS DISTINCT FROM OLD.consumed"
            "_at) THEN\n                    RAISE EXCEPTION 'Goal inbox delivery is immut"
            "able';\n                END IF;\n            ELSIF TG_TABLE_NAME='effects' T"
            "HEN\n                IF (NEW.goal_id,NEW.invocation_id,NEW.capability,NEW.ar"
            "gs_digest,\n                    NEW.delivery_ref,NEW.recipient_entity,NEW.re"
            "cipient_channel) IS DISTINCT FROM\n                   (OLD.goal_id,OLD.invoc"
            "ation_id,OLD.capability,OLD.args_digest,\n                    OLD.delivery_r"
            "ef,OLD.recipient_entity,OLD.recipient_channel) THEN\n                    RAI"
            "SE EXCEPTION 'Effect lineage and recipient association are immutable';\n    "
            "            END IF;\n            END IF;\n            RETURN NEW;\n        E"
            "ND "
        ),
        "roster/switchboard/migrations/047_goal_ledger.py",
    ),
    (
        "switchboard",
        "switchboard",
        "goal_invocation_exposure",
        "",
        False,
        (
            "CREATE OR REPLACE FUNCTION switchboard.goal_invocation_exposure()\n        R"
            "ETURNS trigger LANGUAGE plpgsql\nSET search_path = pg_catalog, pg_temp\n"
        ),
        (
            "\n        BEGIN\n            IF TG_OP='INSERT' AND NOT NEW.exposed AND EXIST"
            "S (\n                SELECT 1 FROM switchboard.goals WHERE id=NEW.goal_id AN"
            "D exposed) THEN\n                RAISE EXCEPTION 'An invocation starts with "
            "its goal''s exposure';\n            END IF;\n            IF NEW.exposed THEN"
            "\n                UPDATE switchboard.goals SET exposed=true, updated_at=now("
            ")\n                WHERE id=NEW.goal_id AND NOT exposed;\n            END IF"
            ";\n            RETURN NULL;\n        END "
        ),
        "roster/switchboard/migrations/047_goal_ledger.py",
    ),
    (
        "switchboard",
        "switchboard",
        "guard_admission_attempt_binding",
        "",
        False,
        (
            "CREATE FUNCTION switchboard.guard_admission_attempt_binding()\n        RETUR"
            "NS trigger LANGUAGE plpgsql\nSET search_path = pg_catalog, pg_temp\n"
        ),
        "\n        BEGIN RAISE EXCEPTION 'Admission attempt bindings are immutable'; END ",
        "roster/switchboard/migrations/050_admission_attempt_bindings.py",
    ),
    (
        "switchboard",
        "switchboard",
        "switchboard_message_inbox_ensure_partition",
        "timestamp with time zone",
        True,
        (
            "CREATE OR REPLACE FUNCTION switchboard.switchboard_message_inbox_ensure_part"
            "ition(\n            reference_ts TIMESTAMPTZ DEFAULT now()\n        ) RETURN"
            "S TEXT\n        LANGUAGE plpgsql\n        SECURITY DEFINER\nSET search_path "
            "= pg_catalog, pg_temp\n"
        ),
        (
            "\nDECLARE\n    c_schema CONSTANT text := 'switchboard';\n    month_start    "
            "TIMESTAMPTZ;\n    month_end      TIMESTAMPTZ;\n    partition_name TEXT;\n   "
            " next_start     TIMESTAMPTZ;\n    next_end       TIMESTAMPTZ;\n    next_name"
            "      TEXT;\nBEGIN\n\n    month_start    := date_trunc('month', reference_ts"
            ");\n    month_end      := month_start + INTERVAL '1 month';\n    partition_n"
            "ame := format('message_inbox_p%s', to_char(month_start, 'YYYYMM'));\n\n    E"
            "XECUTE format(\n        'CREATE TABLE IF NOT EXISTS %I.%I PARTITION OF %I.me"
            "ssage_inbox '\n        'FOR VALUES FROM (%L) TO (%L)',\n        c_schema, pa"
            "rtition_name, c_schema, month_start, month_end\n    );\n\n    -- Next month "
            "partition (proactive)\n    next_start := month_end;\n    next_end   := next_"
            "start + INTERVAL '1 month';\n    next_name  := format('message_inbox_p%s', t"
            "o_char(next_start, 'YYYYMM'));\n\n    EXECUTE format(\n        'CREATE TABLE"
            " IF NOT EXISTS %I.%I PARTITION OF %I.message_inbox '\n        'FOR VALUES FR"
            "OM (%L) TO (%L)',\n        c_schema, next_name, c_schema, next_start, next_e"
            "nd\n    );\n\n    RETURN partition_name;\nEND;\n"
        ),
        "roster/switchboard/migrations/039_pin_definer_search_paths.py",
    ),
    (
        "switchboard",
        "switchboard",
        "switchboard_message_inbox_drop_expired_partitions",
        "interval,timestamp with time zone",
        True,
        (
            "CREATE OR REPLACE FUNCTION switchboard.switchboard_message_inbox_drop_expire"
            "d_partitions(\n            retention INTERVAL DEFAULT INTERVAL '1 month',\n "
            "           reference_ts TIMESTAMPTZ DEFAULT now()\n        ) RETURNS INTEGER"
            "\n        LANGUAGE plpgsql\n        SECURITY DEFINER\nSET search_path = pg_c"
            "atalog, pg_temp\n"
        ),
        (
            "\nDECLARE\n    c_schema CONSTANT text := 'switchboard';\n    partition_name "
            "TEXT;\n    partition_month DATE;\n    cutoff_month DATE;\n    dropped_count "
            "INTEGER := 0;\nBEGIN\n    cutoff_month := date_trunc('month', reference_ts -"
            " retention)::date;\n\n    FOR partition_name IN\n        SELECT child.relnam"
            "e\n        FROM pg_catalog.pg_inherits\n        JOIN pg_catalog.pg_class par"
            "ent ON parent.oid = pg_inherits.inhparent\n        JOIN pg_catalog.pg_namesp"
            "ace parent_ns ON parent_ns.oid = parent.relnamespace\n        JOIN pg_catalo"
            "g.pg_class child ON child.oid = pg_inherits.inhrelid\n        JOIN pg_catalo"
            "g.pg_namespace ns ON ns.oid = child.relnamespace\n        WHERE parent.relna"
            "me = 'message_inbox'\n        AND parent_ns.nspname = c_schema\n        AND "
            "ns.nspname = c_schema\n        AND child.relname ~ '^message_inbox_p[0-9]{6}"
            "$'\n    LOOP\n        partition_month := to_date(substring(partition_name fr"
            "om '[0-9]{6}$'), 'YYYYMM');\n        IF partition_month < cutoff_month THEN"
            "\n            EXECUTE format('DROP TABLE IF EXISTS %I.%I', c_schema, partiti"
            "on_name);\n            dropped_count := dropped_count + 1;\n        END IF;"
            "\n    END LOOP;\n\n    RETURN dropped_count;\nEND;\n"
        ),
        "roster/switchboard/migrations/039_pin_definer_search_paths.py",
    ),
    (
        "switchboard",
        "public",
        "qa_local_schedule_policy",
        "",
        True,
        (
            "CREATE OR REPLACE FUNCTION public.qa_local_schedule_policy()\n        RETURN"
            "S TABLE (policy_state text, policy_provenance text)\n        LANGUAGE plpgsq"
            "l\n        SECURITY DEFINER\nSET search_path = pg_catalog, pg_temp\n"
        ),
        (
            "\n        DECLARE\n            v_state text;\n            v_provenance text;"
            "\n        BEGIN\n            -- current_user is the definer here, not the ru"
            "ntime caller.\n            IF current_setting('role', true) IS DISTINCT FROM"
            " 'butler_qa_rw' THEN\n                RAISE EXCEPTION 'qa_policy_denied' USI"
            "NG ERRCODE = '42501';\n            END IF;\n            SELECT p.policy_stat"
            'e, p.policy_provenance INTO v_state, v_provenance\n            FROM "switch'
            "board\".butler_registry_control_plane AS p\n            WHERE p.name = 'qa';"
            "\n            IF NOT FOUND THEN\n                RAISE EXCEPTION 'qa_policy_"
            "missing' USING ERRCODE = 'P0002';\n            END IF;\n            IF NOT C"
            "OALESCE(\n                (v_state = 'active' AND v_provenance IN ('none', '"
            "legacy_ttl', 'operator'))\n                OR (v_state IN ('paused', 'quaran"
            "tined')\n                    AND v_provenance IN ('legacy_operator', 'operat"
            "or'))\n                OR (v_state = 'review_required'\n                    "
            "AND v_provenance IN ('legacy_ambiguous', 'operator')),\n                fals"
            "e\n            ) THEN\n                RAISE EXCEPTION 'qa_policy_malformed'"
            " USING ERRCODE = '22023';\n            END IF;\n            RETURN QUERY SEL"
            "ECT v_state, v_provenance;\n        END\n        "
        ),
        "roster/switchboard/migrations/038_qa_local_schedule_policy.py",
    ),
    (
        "switchboard",
        "public",
        "finance_warning_boot_current",
        "uuid,bigint",
        True,
        (
            "CREATE OR REPLACE FUNCTION public.finance_warning_boot_current(p"
            "_boot uuid,p_epoch bigint)\n        RETURNS boolean LANGUAGE plpg"
            "sql STABLE SECURITY DEFINER\n        SET search_path = pg_catalog"
            ", pg_temp "
        ),
        (
            "\n        DECLARE v_current boolean;\n        BEGIN\n          IF p"
            "g_catalog.current_setting('role',true) IS DISTINCT FROM 'butler_"
            "finance_rw' THEN\n            RAISE EXCEPTION 'finance_boot_read_"
            "role_required' USING ERRCODE='42501';\n          END IF;\n        "
            "  IF p_boot IS NULL OR p_epoch IS NULL OR p_epoch<=0 THEN RETURN"
            " NULL; END IF;\n          SELECT CASE WHEN h.boot_instance_id IS "
            "NULL THEN NULL\n                      ELSE c.boot_instance_id=p_b"
            "oot AND c.boot_epoch=p_epoch END\n            INTO v_current\n    "
            "        FROM __CHAIN_SCHEMA__.butler_registry_control_plane c\n  "
            "          LEFT JOIN __CHAIN_SCHEMA__.butler_boot_registrations h"
            "\n              ON h.name=c.name AND h.boot_instance_id=c.boot_in"
            "stance_id\n                 AND h.boot_epoch=c.boot_epoch\n       "
            "     WHERE c.name='finance';\n          RETURN v_current;\n       "
            " END "
        ),
        "roster/switchboard/migrations/052_finance_warning_boot_read.py",
    ),
)


_TURN_PURPOSE_FUNCTIONS = (
    "dashboard_turn_require_role",
    "dashboard_turn_open",
    "dashboard_turn_claim_ingress",
    "dashboard_turn_bind_ingress",
    "dashboard_turn_record_ingress_failure",
    "dashboard_turn_claim_target",
    "dashboard_turn_mark_route_enqueued",
    "dashboard_turn_register_session",
    "dashboard_turn_reconcile_route_recovery",
    "dashboard_turn_claim_invoke",
    "dashboard_turn_release_invoke",
    "dashboard_turn_complete_session",
    "dashboard_turn_claim_external_action",
    "dashboard_turn_mark_terminal",
    "dashboard_turn_request_cancel",
    "dashboard_turn_acknowledge_cancel",
    "dashboard_turn_confirm_cancel",
    "dashboard_turn_live_sessions",
    "dashboard_turn_dispatch_status",
)

# These identities come from the fixed producers above. Neither a catalog name
# nor a caller-supplied function can select a privileged owner. Each owner's
# underlying permissions are separately enumerated in the relation manifest.
_ORDINARY_DEFINER_OWNERS = {
    ("connectors", "connectors_filtered_events_ensure_partition"): "connector_partition_owner",
    (
        "__SELECTED_SCHEMA__",
        "connectors_filtered_events_ensure_partition",
    ): "connector_partition_owner",
    ("public", "resolve_owner_triple"): "identity_lookup_owner",
    **{("public", name): "dashboard_turn_owner" for name in _TURN_PURPOSE_FUNCTIONS},
    ("public", "record_runtime_probe_verification"): "model_probe_owner",
    ("public", "resolve_relationship_prepared_action_status"): "relationship_status_owner",
    ("public", "cost_claim_restore_row"): "cost_claim_restore_owner",
    ("dashboard_auth", "api"): "dashboard_auth_owner",
    ("dashboard_auth", "host"): "dashboard_auth_owner",
    ("dashboard_auth", "cleanup"): "dashboard_auth_owner",
    ("public", "resolve_finance_bill_status"): "finance_bill_status_owner",
    ("public", "enqueue_premise_amendments"): "premise_amendment_owner",
    ("public", "resolve_health_medication_refill"): "health_refill_status_owner",
    (
        "switchboard",
        "switchboard_connector_heartbeat_log_ensure_partition",
    ): "switchboard_partition_owner",
    (
        "switchboard",
        "switchboard_connector_heartbeat_log_drop_expired_partitions",
    ): "switchboard_partition_owner",
    ("switchboard", "switchboard_message_inbox_ensure_partition"): "switchboard_partition_owner",
    (
        "switchboard",
        "switchboard_message_inbox_drop_expired_partitions",
    ): "switchboard_partition_owner",
    ("switchboard", "ensure_registry_control"): "registry_control_owner",
    ("public", "register_butler_boot"): "registry_control_owner",
    ("public", "reserve_butler_probe"): "registry_control_owner",
    ("public", "record_butler_probe"): "registry_control_owner",
    ("public", "set_butler_registry_policy"): "registry_control_owner",
    ("public", "qa_local_schedule_policy"): "registry_control_owner",
    ("public", "finance_warning_boot_current"): "registry_control_owner",
}


def _declared_function_executors(name, schema, profiles, trigger):
    """Fixed actual consumers; no PUBLIC, observed role or wildcard grants."""
    if trigger:
        return ()
    runtimes = tuple(profile.role for profile in profiles)
    if name == "uuid_generate_v7":
        from .roles import PURPOSE_OWNERS

        return (
            *runtimes,
            "dashboard_api",
            "dashboard_auth_api",
            "connector_writer",
            "credential_store_writer",
            *PURPOSE_OWNERS,
        )
    if name == "resolve_owner_triple":
        return (*runtimes, "connector_writer", "dashboard_api")
    if name == "dashboard_turn_require_role":
        return ()  # Called inside the fixed owner-defined wrappers only.
    if name.startswith("dashboard_turn_"):
        api = {
            "open",
            "claim_ingress",
            "bind_ingress",
            "record_ingress_failure",
            "request_cancel",
            "confirm_cancel",
            "live_sessions",
            "dispatch_status",
        }
        target = {
            "claim_target",
            "mark_route_enqueued",
            "register_session",
            "reconcile_route_recovery",
            "claim_invoke",
            "release_invoke",
            "complete_session",
            "acknowledge_cancel",
            "mark_terminal",
        }
        switchboard = {
            "register_session",
            "claim_invoke",
            "release_invoke",
            "complete_session",
            "acknowledge_cancel",
            "claim_external_action",
            "mark_terminal",
            "dispatch_status",
        }
        suffix = name.removeprefix("dashboard_turn_")
        return tuple(
            sorted(
                set(
                    (("dashboard_api",) if suffix in api else ())
                    + (runtimes if suffix in target else ())
                    + (("butler_switchboard_rw",) if suffix in switchboard else ())
                )
            )
        )
    if name in {"connectors_filtered_events_ensure_partition"}:
        # core135 also leaves a local compatibility wrapper in each selected
        # core schema. Actual serving callers use the fixed global overloads;
        # retaining a source-produced local object creates no extra capability.
        return (
            ("connector_writer", "butler_switchboard_rw")
            if schema in {"public", "connectors"}
            else ()
        )
    if name in {
        "record_runtime_probe_verification",
        "resolve_relationship_prepared_action_status",
        "resolve_finance_bill_status",
        "resolve_health_medication_refill",
    }:
        return ("butler_switchboard_rw",)
    if name == "finance_warning_boot_current":
        return ("butler_finance_rw",)
    if name == "qa_local_schedule_policy":
        return ("butler_qa_rw",)
    if name == "enqueue_premise_amendments":
        return runtimes
    if name in {
        "posture_writer_allowed",
        "publish_capability_catalog",
        "renew_capability_catalog",
        "register_butler_boot",
    }:
        return runtimes
    if name in {"reserve_butler_probe", "record_butler_probe"}:
        return ("butler_switchboard_rw", "dashboard_api")
    if name == "set_butler_registry_policy":
        return ("dashboard_api",)
    if schema == "dashboard_auth" and name in {"api", "cleanup"}:
        return ("dashboard_auth_api",)
    if name == "collection_vocabulary_key":
        return ("butler_general_rw",)
    if name.startswith("switchboard_"):
        return ("butler_switchboard_rw",)
    # Host authentication, repair and restore-row helpers are explicit
    # administrative operations. Their owner can execute; no serving grant.
    return ()


def _function_executors(name, schema, profiles, trigger):
    selected = {profile.role for profile in profiles}
    return tuple(
        role
        for role in _declared_function_executors(name, schema, profiles, trigger)
        if not (role.startswith("butler_") and role.endswith("_rw")) or role in selected
    )


def compile_function_manifest(
    profiles, migration_owner: str, plans
) -> tuple[FunctionAuthority, ...]:
    """Bind current fixed definitions only to selected source chain identities.

    Historical revision requests receive no current serving-function grant.
    Their data/version guards remain in the actual revision producers.
    """
    from .roles import _identifier

    _identifier(migration_owner)
    if plans is None:
        raise AuthorityError("A selected function authority plan is required")
    selected = {
        (chain, schema or "public") for chain, schema, revision in plans if revision is None
    }
    result = {}
    for (
        chain,
        schema,
        name,
        signature,
        definer,
        header,
        body,
        producer,
    ) in _ORDINARY_FUNCTION_SOURCES:
        targets = sorted(target for actual, target in selected if actual == chain)
        if not targets:
            continue
        # A fixed public overload can read only the selected chain's baked-in
        # schema. Keep that source identity separate from its public namespace.
        contexts = targets if "__CHAIN_SCHEMA__" in body else [None]
        if schema != "__SELECTED_SCHEMA__":
            targets = [schema]
        for target, context in ((target, context) for target in targets for context in contexts):
            _identifier(target)
            quoted = '"' + target + '"'
            # Convergence replaces only an already-preflighted fixed overload.
            # Some original producers used CREATE for their first installation.
            actual_header = header.replace(
                "CREATE FUNCTION ", "CREATE OR REPLACE FUNCTION ", 1
            ).replace("__SELECTED_SCHEMA__", quoted)
            actual_body = body.replace("__SELECTED_SCHEMA__", quoted)
            if context is not None:
                _identifier(context)
                actual_body = actual_body.replace("__CHAIN_SCHEMA__", '"' + context + '"')
            if name in {"cost_claim_audit_claim", "cost_claim_audit_resolution"}:
                # A fixed restore purpose replaces the old implicit table-owner
                # execution path. Only that unreachable role and the original
                # owner can suppress duplicate audit under the restore GUC.
                old = "IF current_user = ("
                actual_body = actual_body.replace(
                    old,
                    "IF (current_user = 'cost_claim_restore_owner'::name OR current_user = (",
                    1,
                ).replace(
                    ")\n               AND current_setting('butlers.cost_claim_restore'",
                    "))\n               AND current_setting('butlers.cost_claim_restore'",
                    1,
                )
            trigger = "RETURNS trigger" in header or "RETURNS TRIGGER" in header
            settings = ("search_path=pg_catalog, pg_temp",)
            if "SET row_security = on" in header:
                settings = ("row_security=on", *settings)
            owner = migration_owner
            if definer:
                owner = _ORDINARY_DEFINER_OWNERS.get((schema, name))
                if owner is None:
                    raise AuthorityError("Managed definer purpose is not declared")
            executors = _function_executors(name, target, profiles, trigger)
            if (schema, name) in {
                ("dashboard_auth", "host"),
                ("public", "cost_claim_restore_row"),
                ("switchboard", "ensure_registry_control"),
                ("switchboard", "switchboard_connector_heartbeat_log_drop_expired_partitions"),
                ("switchboard", "switchboard_message_inbox_drop_expired_partitions"),
            }:
                # These existing administrative operations formerly relied on
                # implicit normal-owner EXECUTE. The serving auth API never
                # receives host authority or migration-role membership.
                executors = (*executors, migration_owner)
            row = FunctionAuthority(
                target,
                name,
                signature,
                owner,
                executors,
                definer,
                actual_header,
                actual_body,
                producer,
                settings,
            )
            if row.identity in result:
                raise AuthorityError("Source function authority is ambiguous")
            result[row.identity] = row
    return tuple(result.values())


def deployment_function_plans(profiles):
    """The real source deployment, independent of what a catalog happens to contain."""
    plans = {("core", profile.primary_schema, None) for profile in profiles}
    for profile in profiles:
        plans.add((profile.primary_schema, profile.primary_schema, None))
        for module in profile.modules:
            targets = (
                profile.private_schemas
                if module == "memory" and profile.private_schemas
                else (profile.primary_schema,)
            )
            plans.update(
                (_MODULE_MIGRATION_CHAINS.get(module, module), target, None) for target in targets
            )
    return tuple(sorted(plans, key=lambda row: (row[0], row[1] or "")))


def serving_catalog_contract(database: str) -> dict:
    """Serialize source authority for fresh reads, never source-derived GRANT effects.

    Normal ownership is compared with this database's independently provisioned
    database owner, not an arbitrary name supplied by a serving caller. Fixed
    purpose owners retain their exact identities. This data has no credentials.
    """
    import hashlib

    from .interfaces import final_functions

    normal, maintenance = "manifest_normal_owner", "manifest_maintenance_actor"
    manifest = compile_relation_manifest(database, normal, maintenance, source_database=database)
    functions = (
        *compile_function_manifest(
            manifest.profiles, normal, deployment_function_plans(manifest.profiles)
        ),
        *final_functions(manifest.profiles, normal),
    )
    from .apply import namespace_permissions

    namespace_access = {
        name: set(roles) for name, roles in namespace_permissions(manifest.relations).items()
    }
    for relation in manifest.relations:
        namespace_access.setdefault(relation.schema, set()).add(relation.owner)
    for function in functions:
        namespace_access.setdefault(function.schema, set()).update(
            (function.owner, *function.executors)
        )
    fixed_owners = {
        "dnd_generation_private": ("dnd_generation_owner",),
        "restore_drill_executor": ("restore_drill_executor_owner",),
        "public": (normal, "pg_database_owner"),
    }
    return {
        "schemas": [
            dict(
                name=name,
                owners=[
                    None if role == normal else role for role in fixed_owners.get(name, (normal,))
                ],
                users=[None if role == normal else role for role in sorted(users)],
                creators=sorted(ordinary_schema_creators(manifest.relations).get(name, ())),
            )
            for name, users in sorted(namespace_access.items())
            if name not in {"dashboard_attention", "dashboard_stepup"}
        ],
        "relations": [
            dict(
                schema=row.schema,
                name=row.name,
                kind=row.kind.value,
                owner=None if row.owner == normal else row.owner,
                row_security=relation_security_flags(row)[0],
                forced_rls=relation_security_flags(row)[1],
                required=row.required,
                permissions=[
                    dict(
                        role=None if permission.role == normal else permission.role,
                        verb=verb,
                        columns=list(permission.columns),
                    )
                    for permission in row.permissions
                    for verb in permission.verbs
                ],
            )
            for row in manifest.relations
            if row.schema not in {"dashboard_attention", "dashboard_stepup"}
        ],
        **compiled_security_contract(manifest),
        "functions": [
            dict(
                schema=row.schema,
                name=row.name,
                signature=row.signature,
                owner=None if row.owner == normal else row.owner,
                definer=row.security_definer,
                settings=list(getattr(row, "settings", ("search_path=pg_catalog, pg_temp",))),
                digest=hashlib.sha256(row.body.encode()).hexdigest(),
                executors=[
                    None if role == normal else role for role in row.executors if role != row.owner
                ],
            )
            for row in functions
        ],
    }


# RLS and trigger definitions are source authority too. Names/flags alone cannot
# distinguish an installed fence from an identically named permissive rewrite.
# Expressions below use PostgreSQL's non-pretty deparse spelling. Only lexical
# whitespace and unquoted identifier case may vary; casts, literal contents,
# parentheses, qualifiers, operators and all trigger metadata remain exact.
_SQL_TOKEN_PATTERN = (
    r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|::|<>|!=|<=|>=|\|\||"
    r"[A-Za-z_][A-Za-z_0-9$]*|[0-9]+(?:\.[0-9]+)?|[().,\[\]=<>+*/-]"
)


def security_expression_tokens(expression: str | None):
    """Closed lexical comparison, not SQL rewriting or semantic simplification."""
    import re

    if expression is None:
        return None
    result, position = [], 0
    for match in re.finditer(_SQL_TOKEN_PATTERN, expression):
        if expression[position : match.start()].strip() or expression.startswith(
            ("--", "/*"), match.start()
        ):
            raise AuthorityError("Database security expression is unsupported")
        token = match.group()
        result.append(token.lower() if re.match(r"[A-Za-z_]", token) else token)
        position = match.end()
    if expression[position:].strip() or not result:
        raise AuthorityError("Database security expression is unsupported")
    return result


def compiled_security_contract(manifest: RelationManifest, *, ordinary_purposes=True) -> dict:
    """Fixed policies/triggers; observed catalog rows never extend this set.

    The normal-owner placeholder is bound to the independently selected database
    owner in the catalog predicate. It cannot name a caller-selected authority.
    Ordinary SQL bodies remain independently checked by the function manifest.
    """
    policies, triggers = [], []
    present = {(row.schema, row.name) for row in manifest.relations}

    def policy(schema, table, name, command, using=None, check=None, roles=("PUBLIC",)):
        selected = {profile.role for profile in manifest.profiles}
        roles = tuple(
            role
            for role in roles
            if not (role.startswith("butler_") and role.endswith("_rw")) or role in selected
        )
        if (schema, table) in present and roles:
            policies.append(
                dict(
                    schema=schema,
                    table=table,
                    name=name,
                    command=command,
                    roles=list(roles),
                    permissive=True,
                    using=security_expression_tokens(using),
                    check=security_expression_tokens(check),
                )
            )

    def equal(role):
        return f"(CURRENT_USER = '{role}'::name)"

    def own(table, column="producer_role"):
        predicate = f"({column} = CURRENT_USER)"
        policy("public", table, table + "_read", "r", "true")
        policy("public", table, table + "_insert_own", "a", check=predicate)
        policy("public", table, table + "_update_own", "w", predicate, predicate)

    for table in ("expected_signals", "task_continuity"):
        own(table)
    for table in ("fleet_cases", "fleet_case_links"):
        predicate = equal("butler_switchboard_rw")
        policy("public", table, table + "_read", "r", "true")
        policy("public", table, table + "_insert_switchboard", "a", check=predicate)
        policy("public", table, table + "_update_switchboard", "w", predicate, predicate)
    for table in ("runtime_probe_control_receipts", "insight_feedback", "insight_amendments"):
        predicate = equal("butler_switchboard_rw")
        policy("public", table, table + "_switchboard", "*", predicate, predicate)
    if ordinary_purposes:
        policy(
            "public",
            "insight_amendments",
            "premise_amendment_insert",
            "a",
            check="true",
            roles=("premise_amendment_owner",),
        )
    predicate = "((signal_type <> 'dnd'::text) OR (CURRENT_USER = 'dnd_generation_owner'::name))"
    policy("public", "user_context", "dnd_user_context_select", "r", "true")
    policy("public", "user_context", "dnd_user_context_insert", "a", check=predicate)
    policy("public", "user_context", "dnd_user_context_update", "w", predicate, predicate)
    policy("public", "user_context", "dnd_user_context_delete", "d", predicate)
    for table in ("runtime_attention_outbox", "runtime_attention_delivery_lease"):
        policy(
            "public",
            table,
            table + "_owner",
            "*",
            "true",
            "true",
            ("runtime_attention_outbox_owner",),
        )
        policy(
            "public",
            table,
            table + "_switchboard",
            "*",
            "runtime_attention_active_switchboard_role()",
            "runtime_attention_active_switchboard_role()",
            ("butler_switchboard_rw",),
        )
    policy(
        "public",
        "runtime_attention_condition_episodes",
        "runtime_attention_condition_episodes_owner",
        "*",
        "true",
        "true",
        ("runtime_attention_outbox_owner",),
    )
    for table in ("cost_claims", "cost_claim_resolutions", "cost_claim_events"):
        policy("public", table, table + "_read", "r", "true")
        predicate = equal("__NORMAL_OWNER__")
        if ordinary_purposes:
            predicate = f"({predicate} OR {equal('cost_claim_restore_owner')})"
        policy("public", table, table + "_restore_owner", "a", check=predicate)
    predicate = "(asserted_by_role = CURRENT_USER)"
    policy(
        "public",
        "cost_claims",
        "cost_claims_insert_own",
        "a",
        check="((asserted_by_role = CURRENT_USER) AND "
        "(asserted_by_role = (('butler_'::text || asserted_by) || '_rw'::text)))",
    )
    policy("public", "cost_claims", "cost_claims_update_own", "w", predicate, predicate)
    predicate = equal("butler_finance_rw")
    policy(
        "public",
        "cost_claim_resolutions",
        "cost_claim_resolutions_finance_insert",
        "a",
        check=predicate,
    )
    policy(
        "public",
        "cost_claim_resolutions",
        "cost_claim_resolutions_finance_update",
        "w",
        predicate,
        predicate,
    )
    policy(
        "public",
        "cost_claim_events",
        "cost_claim_events_trigger_insert",
        "a",
        check="((actor_role = CURRENT_USER) AND (pg_trigger_depth() > 0))",
    )
    policy("public", "entity_rebind_log", "entity_rebind_log_read", "r", "true")
    policy(
        "public",
        "entity_rebind_log",
        "entity_rebind_log_insert_relationship",
        "a",
        check=equal("butler_relationship_rw"),
    )
    predicate = (
        "(CURRENT_USER = (('butler_'::text || CASE WHEN (target_schema = "
        "'chronicler_mem'::text) THEN 'chronicler'::text ELSE target_schema END) || '_rw'::text))"
    )
    policy("public", "entity_rebind_log", "entity_rebind_log_update_own", "w", predicate, predicate)
    predicate = equal("butler_switchboard_rw")
    policy(
        "public",
        "ha_person_mapping_receipts",
        "ha_person_mapping_receipts_authority",
        "*",
        predicate,
        predicate,
    )
    policy(
        "connectors",
        "home_assistant_persons",
        "ha_person_mappings_select",
        "r",
        "(CURRENT_USER = ANY (ARRAY['butler_switchboard_rw'::name, 'butler_chronicler_rw'::name]))",
    )
    policy(
        "connectors", "home_assistant_persons", "ha_person_mappings_insert", "a", check=predicate
    )
    predicate = equal("butler_qa_rw")
    policy("public", "qa_publication_operations", "qa_publication_operations_read", "r", "true")
    policy(
        "public",
        "qa_publication_operations",
        "qa_publication_operations_insert",
        "a",
        check=predicate,
    )
    policy(
        "public",
        "qa_publication_operations",
        "qa_publication_operations_update",
        "w",
        predicate,
        predicate,
    )
    policy(
        "public",
        "capability_catalog_installations",
        "catalog_installation_owner",
        "*",
        "((CURRENT_USER)::text = ( SELECT pg_get_userbyid(pg_class.relowner) AS pg_get_userbyid "
        "FROM pg_class WHERE (pg_class.oid = 'capability_catalog_installations'::regclass)))",
    )
    for suffix in ("control", "members", "history", "current"):
        table = "capability_catalog_" + suffix
        policy("public", table, "catalog_read", "r", "true")
        if suffix in {"history", "current"}:
            predicate = (
                "(((CURRENT_USER)::text = (('butler_'::text || domain) || '_rw'::text)) "
                "AND (EXISTS ( SELECT 1 FROM capability_catalog_control WHERE "
                "(capability_catalog_control.singleton AND capability_catalog_control.enabled))) "
                "AND (EXISTS ( SELECT 1 FROM capability_catalog_members m WHERE "
                f"((m.domain = {table}.domain) AND m.included))))"
            )
            policy("public", table, "catalog_insert", "a", check=predicate)
        if suffix == "current":
            predicate = "((CURRENT_USER)::text = (('butler_'::text || domain) || '_rw'::text))"
            policy("public", table, "catalog_update", "w", predicate, predicate)
    policy(
        "switchboard",
        "butler_registry_control_plane",
        "registry_control_read",
        "r",
        "true",
        roles=("butler_switchboard_rw",),
    )
    if ordinary_purposes:
        for table, commands in (
            ("butler_registry_control_plane", (("r", "read"), ("a", "insert"), ("w", "update"))),
            ("butler_boot_registrations", (("r", "read"), ("a", "insert"))),
        ):
            for command, label in commands:
                policy(
                    "switchboard",
                    table,
                    "registry_purpose_" + label,
                    command,
                    None if command == "a" else "true",
                    "true" if command in {"a", "w"} else None,
                    roles=("registry_control_owner",),
                )
    for table in (
        "goals",
        "invocations",
        "effects",
        "budgets",
        "admission_decisions",
        "goal_inbox",
    ):
        for command, label in (("r", "select"), ("a", "insert"), ("w", "update"), ("d", "delete")):
            predicate = equal("butler_switchboard_rw")
            if (table == "admission_decisions" and command in {"w", "d"}) or (
                table == "goal_inbox" and command == "d"
            ):
                predicate = f"({predicate} AND false)"
            policy(
                "switchboard",
                table,
                "goal_ledger_" + label,
                command,
                None if command == "a" else predicate,
                predicate if command in {"a", "w"} else None,
                ("butler_switchboard_rw",),
            )
    for command, label in (("r", "read"), ("a", "insert"), ("w", "update"), ("d", "delete")):
        predicate = equal("butler_switchboard_rw") if command in {"r", "a"} else "false"
        policy(
            "switchboard",
            "admission_attempt_bindings",
            "admission_attempt_" + label,
            command,
            None if command == "a" else predicate,
            predicate if command in {"a", "w"} else None,
            ("butler_switchboard_rw",),
        )

    def trigger(
        schema,
        table,
        name,
        events,
        function,
        *,
        columns=(),
        enabled="O",
        when=None,
        constraint=False,
    ):
        if (schema, table) in present:
            function_schema, function_name = function.split(".")
            triggers.append(
                dict(
                    schema=schema,
                    table=table,
                    name=name,
                    type=events,
                    enabled=enabled,
                    function_schema=function_schema,
                    function_name=function_name,
                    signature="",
                    columns=list(columns),
                    when=security_expression_tokens(None if when is None else "(" + when + ")"),
                    constraint=constraint,
                    deferrable=constraint,
                    deferred=constraint,
                )
            )

    for name, events, function in (
        ("audit_log_writer_identity", 7, "audit_log_bind_writer"),
        ("audit_log_no_row_mutation", 27, "audit_log_refuse_mutation"),
        ("audit_log_no_truncate", 34, "audit_log_refuse_mutation"),
    ):
        trigger("public", "audit_log", name, events, "public." + function, enabled="A")
    for table, name, events, function in (
        (
            "runtime_probe_control_receipts",
            "trg_runtime_probe_control_receipts_retention",
            11,
            "runtime_probe_control_receipt_retention",
        ),
        (
            "runtime_probe_control_receipts",
            "trg_runtime_probe_control_receipts_no_truncate",
            34,
            "runtime_probe_control_receipts_no_truncate",
        ),
        ("cost_claims", "trg_cost_claim_immutable", 19, "cost_claim_immutable_assertion"),
        ("cost_claims", "trg_cost_claim_audit", 21, "cost_claim_audit_claim"),
        (
            "cost_claim_resolutions",
            "trg_cost_claim_resolution_audit",
            21,
            "cost_claim_audit_resolution",
        ),
        ("entity_rebind_log", "trg_entity_rebind_identity", 19, "guard_entity_rebind_identity"),
        (
            "qa_publication_operations",
            "qa_publication_operations_guard",
            19,
            "qa_publication_operations_guard",
        ),
        (
            "qa_publication_operations",
            "qa_publication_operations_no_held_delete",
            11,
            "qa_publication_operations_no_held_delete",
        ),
        (
            "capability_catalog_history",
            "immutable_capability_catalog_history",
            27,
            "guard_capability_catalog",
        ),
        (
            "capability_catalog_current",
            "fenced_capability_catalog_current",
            31,
            "guard_capability_catalog",
        ),
        (
            "runtime_attention_outbox",
            "runtime_attention_outbox_guard_trigger",
            31,
            "runtime_attention_outbox_guard",
        ),
        (
            "runtime_attention_delivery_lease",
            "runtime_attention_delivery_lease_guard_trigger",
            31,
            "runtime_attention_delivery_lease_guard",
        ),
    ):
        trigger("public", table, name, events, "public." + function)
    trigger(
        "public",
        "entities",
        "trg_entities_posture_writer",
        19,
        "public.guard_entity_posture_writer",
        columns=("posture", "posture_since", "posture_set_by"),
    )
    trigger(
        "public",
        "runtime_attention_outbox",
        "runtime_attention_condition_marker_sync_trigger",
        17,
        "public.runtime_attention_sync_condition_marker",
        columns=("lifecycle_state",),
        when=(
            "((new.source = 'control_plane_condition'::text) AND "
            "(new.lifecycle_state IS DISTINCT FROM old.lifecycle_state))"
        ),
    )
    for table, name, events, function in (
        ("butler_registry", "trg_preserve_registry_policy", 23, "preserve_registry_policy"),
        ("butler_registry", "trg_ensure_registry_control", 5, "ensure_registry_control"),
        (
            "admission_attempt_bindings",
            "immutable_admission_attempt_binding",
            27,
            "guard_admission_attempt_binding",
        ),
    ):
        trigger("switchboard", table, name, events, "switchboard." + function)
    for table in ("goals", "invocations", "effects", "admission_decisions", "goal_inbox"):
        trigger(
            "switchboard",
            table,
            "goal_ledger_immutable",
            27 if table == "admission_decisions" else 19,
            "switchboard.goal_ledger_immutable",
        )
    trigger(
        "switchboard",
        "invocations",
        "goal_invocation_exposure",
        21,
        "switchboard.goal_invocation_exposure",
        columns=("exposed",),
    )
    trigger(
        "education",
        "mind_maps",
        "mind_maps_active_content",
        23,
        "education.require_active_map_content",
    )
    trigger(
        "education",
        "mind_map_nodes",
        "mind_map_nodes_removal_lock",
        27,
        "education.serialize_concept_removal",
        columns=("mind_map_id",),
    )
    trigger(
        "education",
        "mind_map_nodes",
        "mind_map_nodes_active_content",
        25,
        "education.require_content_after_removal",
        columns=("mind_map_id",),
    )
    trigger(
        "relationship",
        "fact_evidence",
        "trg_fact_evidence_immutable",
        19,
        "relationship.reject_fact_evidence_update",
    )
    trigger(
        "relationship",
        "entity_facts",
        "guard_fact_report_authority",
        19,
        "relationship.guard_fact_report_authority",
    )
    for table, name, events, function, columns, when in (
        (
            "collections",
            "collections_classification_guard",
            19,
            "collections_classification_guard",
            (),
            None,
        ),
        (
            "collection_vocabulary_keys",
            "collection_vocabulary_keys_stamp",
            23,
            "collection_vocabulary_keys_stamp",
            (),
            None,
        ),
        (
            "collections",
            "collections_classification_mirror",
            17,
            "collections_classification_mirror",
            ("custody_private",),
            "(new.custody_private IS DISTINCT FROM old.custody_private)",
        ),
        ("source_versions", "source_versions_immutable", 27, "source_versions_immutable", (), None),
        (
            "source_versions",
            "source_versions_no_truncate",
            34,
            "source_versions_immutable",
            (),
            None,
        ),
    ):
        trigger("general", table, name, events, "general." + function, columns=columns, when=when)
    # Administrative selected chains still own their fixed schema-local
    # security definitions. Binding a disposable schema does not manufacture a
    # runtime profile or a serving privilege; select_migration_manifest already
    # validated these source chains and target identities.
    module_targets = {
        (module, target)
        for profile in manifest.profiles
        for module in profile.modules
        for target in (
            (profile.private_schemas or (profile.primary_schema,))
            if module == "memory"
            else (profile.primary_schema,)
        )
    }
    module_targets.update(
        (chain, schema or "public")
        for chain, schema, revision in manifest.selected_plans or ()
        if revision is None and chain in {"memory", "approvals"}
    )
    for module, target in sorted(module_targets):
        if module == "memory":
            trigger(
                target,
                "predicate_registry",
                "trg_predicate_registry_search_vector",
                23,
                target + ".predicate_registry_search_vector_trigger",
                columns=("name", "description", "aliases"),
            )
            trigger(
                target,
                "episodes",
                "trg_episodes_preserve_provenance",
                11,
                target + ".record_episode_tombstone",
            )
        if module == "approvals":
            for table, name, events, function in (
                (
                    "approval_events",
                    "trg_approval_events_immutable",
                    27,
                    "prevent_approval_events_mutation",
                ),
                (
                    "approval_events",
                    "trg_approval_events_action_reference",
                    7,
                    "validate_approval_event_action_reference",
                ),
                (
                    "approval_events",
                    "trg_approval_events_rule_reference",
                    7,
                    "validate_approval_event_rule_reference",
                ),
                (
                    "approval_delivery_presentations",
                    "trg_approval_delivery_presentation_subject",
                    23,
                    "validate_approval_delivery_presentation_subject",
                ),
                (
                    "approval_delivery_attempts",
                    "trg_approval_delivery_attempts_immutable",
                    27,
                    "prevent_approval_delivery_attempts_mutation",
                ),
            ):
                trigger(target, table, name, events, target + "." + function)
            trigger(
                target,
                "approval_rules",
                "trg_approval_rules_created_from_reference",
                21,
                target + ".validate_approval_rule_creator_reference",
                columns=("created_from",),
                constraint=True,
            )
    return dict(policies=policies, triggers=triggers)


# Exact retained outputs of Relationship producers. These administrative rows
# confer no serving permission and are never promoted into _DOMAIN_TABLES.
_RELATIONSHIP_RETAINED_NAMES = (
    "contacts",
    "addresses_dedup_bak_rel_030",
    "contact_labels_dedup_bak_rel_030",
    "group_members_dedup_bak_rel_030",
    "important_dates_dedup_bak_rel_030",
    "life_events_dedup_bak_rel_030",
    "relationships_dedup_bak_rel_030",
    "tasks_dedup_bak_rel_030",
    "contact_entity_map_dedup_bak_rel_030",
    "reminders",
)


def relationship_retained_objects(migration_owner, *, relationship_revisions, core_revisions):
    """Closed, conditional outputs of exact checked-in producer edges.

    Current source cannot attest which conditional branch ran. It declares
    only these possible retained identities, without granting a capability or
    requiring that a fresh deployment contain a backup.
    """
    rows = []
    if "rel_001" in relationship_revisions:
        # rel007 can return before its rename when local core calendar
        # producers have not run. rel020 drops only the backup, not this table.
        rows.append(
            ObjectAuthority(
                "relationship",
                "reminders",
                ObjectKind.TABLE,
                migration_owner,
                (),
                "rel001 retained after rel007 calendar-guard skip",
                required=False,
            )
        )
        rows.append(
            ObjectAuthority(
                "relationship",
                "contacts",
                ObjectKind.TABLE,
                migration_owner,
                (),
                "rel001 local producer / rel003 conditional shadow",
                required=False,
            )
        )
    if "rel_030" in relationship_revisions:
        rows.extend(
            ObjectAuthority(
                "relationship",
                name,
                ObjectKind.TABLE,
                migration_owner,
                (),
                "rel030 conditional retained dedup snapshot",
                required=False,
            )
            for name in _RELATIONSHIP_RETAINED_NAMES
            if name.endswith("_dedup_bak_rel_030")
        )
    # rel003 downgrade deliberately leaves this recovery snapshot. Any
    # installed Relationship prefix may be a genuine inverse of that producer;
    # absence remains valid and no access is granted by this retained row.
    if relationship_revisions:
        rows.append(
            ObjectAuthority(
                "public",
                "contacts_preferred_channel_dropbak_rel_003",
                ObjectKind.TABLE,
                migration_owner,
                (),
                "rel003 conditional preference snapshot",
                required=False,
            )
        )
    if "rel_031" in relationship_revisions and "core_151" not in core_revisions:
        rows.append(
            ObjectAuthority(
                "public",
                "entities_contact_profile_bak_rel_031",
                ObjectKind.TABLE,
                migration_owner,
                (),
                "rel031 conditional profile snapshot; core151 retirement",
                required=False,
            )
        )
    return tuple(rows)


@dataclass(frozen=True)
class ComponentAuthorityProfile:
    """A complete fixed source alternative; catalog observations choose no grants."""

    chain: str
    schema: str
    revision: str
    phase: str
    relations: tuple[ObjectAuthority, ...]
    functions: tuple[FunctionAuthority, ...]
    policies: tuple[str, ...]
    triggers: tuple[str, ...]
    dependency_columns: tuple[tuple[str, str, bool], ...]
    forbidden_columns: tuple[str, ...]
    namespace_users: tuple[str, ...]
    context_relations: tuple[ObjectAuthority, ...]
    context_functions: tuple[tuple[FunctionAuthority, ...], ...]
    source_states: tuple[str, ...] = ()


# (introduced, retired-before, source name). These are literal checked-in
# producer windows, not names discovered from a database or a function body.
_RELATIONSHIP_TABLE_WINDOWS = (
    (1, None, "relationships"),
    (1, None, "important_dates"),
    (1, 10, "notes"),
    (1, 10, "interactions"),
    (1, 10, "gifts"),
    (1, 10, "loans"),
    (1, 10, "activity_feed"),
    (1, None, "groups"),
    (1, None, "group_members"),
    (1, None, "labels"),
    (1, None, "contact_labels"),
    (1, 25, "quick_facts"),
    (5, None, "addresses"),
    (8, None, "relationship_types"),
    (8, None, "life_event_categories"),
    (8, None, "life_event_types"),
    (9, None, "life_events"),
    (9, None, "tasks"),
    (13, None, "entity_facts"),
    (14, None, "entity_predicate_registry"),
    (16, None, "credentials"),
    (21, None, "entity_view_marks"),
    (21, None, "merge_reviews"),
    (23, None, "group_labels"),
    (29, None, "contact_entity_map"),
    (34, None, "fact_evidence"),
    (34, None, "fact_coverage"),
    (34, None, "fact_approval_context"),
    (36, None, "meeting_debriefs"),
    (37, None, "fact_approval_rule_context"),
    (37, None, "fact_identity_decisions"),
)
_RELATIONSHIP_REL030_BACKUPS = (
    "addresses",
    "contact_labels",
    "group_members",
    "important_dates",
    "life_events",
    "relationships",
    "tasks",
    "contact_entity_map",
)
_RELATIONSHIP_FUNCTION_EDGES = {
    "reject_fact_evidence_update": "rel_034",
    "guard_fact_report_authority": "rel_037",
}
_RELATIONSHIP_FACT_COLUMNS = (
    (13, "id", "uuid", True),
    (13, "subject", "uuid", True),
    (13, "predicate", "text", True),
    (13, "object", "text", True),
    (13, "object_kind", "text", True),
    (13, "src", "text", True),
    (13, "conf", "double precision", True),
    (13, "last_seen", "timestamp with time zone", False),
    (13, "weight", "integer", False),
    (13, "verified", "boolean", True),
    (13, "primary", "boolean", False),
    (13, "validity", "text", True),
    (13, "created_at", "timestamp with time zone", True),
    (13, "updated_at", "timestamp with time zone", True),
    (21, "observed_at", "timestamp with time zone", False),
    (21, "metadata", "jsonb", False),
    (34, "assert_origin", "text", False),
    (34, "assert_session_id", "uuid", False),
    (34, "assert_action_id", "uuid", False),
    (35, "effective_period_id", "uuid", False),
    (35, "effective_from", "timestamp with time zone", False),
    (35, "effective_from_precision", "text", False),
    (35, "effective_to", "timestamp with time zone", False),
    (35, "effective_to_precision", "text", False),
    (37, "content_authority", "text", False),
    (37, "authority_entity_id", "uuid", False),
    (37, "authority_original_entity_id", "uuid", False),
    (37, "authority_entity_created_at", "timestamp with time zone", False),
    (37, "confirmed_by_entity_id", "uuid", False),
    (37, "confirmed_by_original_entity_id", "uuid", False),
    (37, "confirmed_at", "timestamp with time zone", False),
    (37, "confirmation_source", "text", False),
    (37, "object_entity_id", "uuid", False),
)


_RELATIONSHIP_CONTEXT_FUNCTION_EDGES = {
    "alembic/versions/core/core_135_filtered_events_ensure_partition_security_definer.py": (
        "core",
        "core_135",
    ),
    "src/butlers/modules/approvals/migrations/001_approvals_tables.py": (
        "approvals",
        "approvals_001",
    ),
    "src/butlers/modules/approvals/migrations/008_retain_event_action_provenance.py": (
        "approvals",
        "approvals_008",
    ),
    "src/butlers/modules/approvals/migrations/009_retain_rule_creator_provenance.py": (
        "approvals",
        "approvals_009",
    ),
    "src/butlers/modules/approvals/migrations/011_retain_event_rule_provenance.py": (
        "approvals",
        "approvals_011",
    ),
    "src/butlers/modules/approvals/migrations/015_delivery_intent_recovery.py": (
        "approvals",
        "approvals_015",
    ),
    "src/butlers/modules/approvals/migrations/016_retention_cascade_attempt_cleanup.py": (
        "approvals",
        "approvals_016",
    ),
    "src/butlers/modules/memory/migrations/001_memory_schema.py": ("memory", "mem_001"),
    "src/butlers/modules/memory/migrations/010_preserve_episode_provenance.py": (
        "memory",
        "mem_010",
    ),
    "roster/general/migrations/003_ordinary_source_authority.py": ("general", "gen_003"),
    "roster/relationship/migrations/034_fact_evidence_and_coverage.py": ("relationship", "rel_034"),
    "roster/relationship/migrations/037_fact_content_authority.py": ("relationship", "rel_037"),
}
_RELATIONSHIP_CORE007_WRAPPER = (
    (
        "\n        CREATE OR REPLACE FUNCTION connectors_filtered_even"
        "ts_ensure_partition(\n            reference_ts TIMESTAMPTZ DE"
        "FAULT now()\n        ) RETURNS TEXT\n        LANGUAGE plpgsql\n"
        "        "
    ),
    (
        "\n        DECLARE\n            month_start    TIMESTAMPTZ;\n   "
        "         month_end      TIMESTAMPTZ;\n            partition_n"
        "ame TEXT;\n            next_start     TIMESTAMPTZ;\n          "
        "  next_end       TIMESTAMPTZ;\n            next_name      TEX"
        "T;\n        BEGIN\n            -- Current month partition\n    "
        "        month_start    := date_trunc('month', reference_ts);"
        "\n            month_end      := month_start + INTERVAL '1 mon"
        "th';\n            partition_name := format('filtered_events_%"
        "s', to_char(month_start, 'YYYYMM'));\n\n            EXECUTE fo"
        "rmat(\n                'CREATE TABLE IF NOT EXISTS connectors"
        ".%I '\n                'PARTITION OF connectors.filtered_even"
        "ts '\n                'FOR VALUES FROM (%L) TO (%L)',\n       "
        "         partition_name,\n                month_start,\n      "
        "          month_end\n            );\n\n            -- Next mont"
        "h partition (proactive)\n            next_start := month_end;"
        "\n            next_end   := next_start + INTERVAL '1 month';\n"
        "            next_name  := format('filtered_events_%s', to_ch"
        "ar(next_start, 'YYYYMM'));\n\n            EXECUTE format(\n    "
        "            'CREATE TABLE IF NOT EXISTS connectors.%I '\n    "
        "            'PARTITION OF connectors.filtered_events '\n     "
        "           'FOR VALUES FROM (%L) TO (%L)',\n                n"
        "ext_name,\n                next_start,\n                next_e"
        "nd\n            );\n\n            RETURN partition_name;\n      "
        "  END;\n        "
    ),
)


def _owner_lookup_source_template_identity():
    """Fingerprint actual compiler/graph bytes; never cache a catalog verdict."""
    import hashlib
    from pathlib import Path

    from butlers import migrations

    from . import roles

    paths = {
        Path(__file__).resolve(),
        Path(migrations.__file__).resolve(),
        Path(roles.__file__).resolve(),
        Path(__file__).with_name("interfaces.py"),
        migrations.ALEMBIC_DIR / "alembic.ini",
    }
    for chain in migrations.get_all_chains():
        directory = migrations._resolve_chain_dir(chain)
        if directory is None:
            raise AuthorityError("Source component directory is unavailable")
        paths.update(directory.glob("*.py"))
    digest = hashlib.sha256()
    for path in sorted(paths):
        data = path.read_bytes()
        digest.update(str(path.resolve()).encode())
        digest.update(b"\0" + len(data).to_bytes(8, "big") + data)
    return digest.hexdigest()


@_owner_lookup_template_cache(maxsize=16)
def _owner_lookup_function_templates(profiles, owner, source_identity):
    # The key includes actual graph/compiler bytes and complete declared roles.
    # These are immutable definitions, not physical OIDs/ACLs or prior success.
    from .interfaces import final_functions

    ordinary = compile_function_manifest(
        profiles, owner, (("core", "public", None), *deployment_function_plans(profiles))
    )
    return ordinary, final_functions(profiles, owner)


@_owner_lookup_template_cache(maxsize=16)
def _owner_lookup_graph_templates(source_identity):
    from butlers import migrations

    return tuple(
        (chain, migrations.get_chain_head(chain), migrations.get_chain_revision_ids(chain))
        for chain in migrations.get_all_chains()
    )


@_owner_lookup_template_cache(maxsize=128)
def _owner_lookup_revision_path_template(chain, revision, source_identity):
    from butlers.migrations import get_chain_revision_path

    return get_chain_revision_path(chain, revision)


def _owner_lookup_source_revision_path(chain, revision):
    return _owner_lookup_revision_path_template(
        chain, revision, _owner_lookup_source_template_identity()
    )


@_owner_lookup_template_cache(maxsize=256)
def _owner_lookup_selected_template(manifest, plans, source_identity):
    return select_migration_manifest(manifest, plans)


def _owner_lookup_validate_source_plans(manifest, plans, source_identity):
    # Source admission was originally per ledger row. Reuse only those pure
    # stencils across prefix alternatives; the complete installed vector and
    # its physical catalog still undergo independent component validation.
    for plan in plans:
        _owner_lookup_selected_template(manifest, (plan,), source_identity)


def _owner_lookup_admit_source_plans(manifest, plans):
    return _owner_lookup_selected_template(
        manifest, plans, _owner_lookup_source_template_identity()
    )


def compile_relationship_component_profiles(manifest, *, installed_plans):
    if (
        not isinstance(manifest, RelationManifest)
        or type(installed_plans) is not tuple
        or any(
            type(row) is not tuple
            or len(row) != 3
            or any(type(value) is not str or not value for value in row)
            for row in installed_plans
        )
    ):
        raise AuthorityError("Installed component source vector is unavailable")
    return _compile_relationship_component_profiles(
        manifest, installed_plans, _owner_lookup_source_template_identity()
    )


@_owner_lookup_template_cache(maxsize=16)
def _compile_relationship_component_profiles(manifest, installed_plans, source_identity):
    """Compile all source prefixes independently of desired future batch plans.

    This explicit API does not alter any existing compiler caller/default.
    Actual installed rows must first be physically/source qualified. Revision age
    controls producer requiredness, while source authority phase controls rights.
    Neither observed object absence nor an observed ACL enters this compiler.
    """
    import json
    from dataclasses import replace
    from functools import lru_cache
    from itertools import product

    from butlers import migrations

    # Each actual graph is loaded once for this pure compilation. The outer
    # key fingerprints those same source bytes before every consumer call.
    get_chain_head = lru_cache(maxsize=None)(migrations.get_chain_head)

    def get_chain_revision_path(chain, revision):
        return _owner_lookup_revision_path_template(chain, revision, source_identity)

    if not isinstance(installed_plans, tuple):
        raise AuthorityError("Installed component source vector is unavailable")
    selected = []
    seen = set()
    for row in installed_plans:
        if not isinstance(row, tuple) or len(row) != 3:
            raise AuthorityError("Installed component source row is invalid")
        chain, schema, revision = row
        if any(type(value) is not str or not value for value in row) or (chain, schema) in seen:
            raise AuthorityError("Installed component revision is ambiguous")
        seen.add((chain, schema))
        try:
            path = get_chain_revision_path(chain, revision)
        except RuntimeError as exc:
            raise AuthorityError("Installed component revision is not source-owned") from exc
        if revision not in path:
            raise AuthorityError("Installed component revision is not source-owned")
        selected.append((chain, schema, revision))
    # Use the existing complete source admission once, without recompiling one
    # entire manifest for every repeated per-schema core row.
    _owner_lookup_validate_source_plans(manifest, tuple(selected), source_identity)
    heads = [
        revision
        for chain, schema, revision in selected
        if chain == "relationship" and schema == "relationship"
    ]
    if len(heads) > 1:
        raise AuthorityError("Relationship source vector is ambiguous")
    revision = heads[0] if heads else "base"
    path = frozenset(get_chain_revision_path("relationship", revision)) if heads else frozenset()
    expected = tuple(f"rel_{number:03}" for number in range(1, 38))
    if revision != "base" and (
        revision not in expected or path != frozenset(expected[: expected.index(revision) + 1])
    ):
        raise AuthorityError("Relationship prefix is not an enumerated source state")
    n = expected.index(revision) + 1 if revision != "base" else 0
    declared = {row.name: row for row in manifest.relations if row.schema == "relationship"}
    names = {
        name
        for since, until, name in _RELATIONSHIP_TABLE_WINDOWS
        if since <= n and (until is None or n < until)
    }
    if heads:
        names.add("alembic_version")
    context_relations = ()
    # A qualified current core prefix owns its declared local component. Bounded
    # core rows stay source-known, without claiming a future core producer ran.
    core_heads = [
        head for chain, schema, head in selected if chain == "core" and schema == "relationship"
    ]
    if core_heads:
        core_manifest = _owner_lookup_selected_template(
            manifest,
            (
                (
                    "core",
                    "relationship",
                    None if core_heads[0] == get_chain_head("core") else core_heads[0],
                ),
            ),
            source_identity,
        )
        context_relations = tuple(
            row
            for row in core_manifest.relations
            if row.schema == "relationship"
            and row.name
            not in {
                *_DOMAIN_TABLES["relationship"],
                *_RELATIONSHIP_RETAINED_NAMES,
                "alembic_version",
            }
        )
        if not heads:
            names.add("alembic_version")
            context_relations = tuple(
                row for row in context_relations if row.name != "alembic_version"
            )
    # Exact rel007/020 producer and inverse populations. Calendar guards can
    # skip the rename; rel020 drops only backup, and its inverse recreates it.
    # Both names can therefore coexist. Neither acquires serving privileges.
    if n == 0:
        reminder_variants = ((), ("_reminders_backup",))
    elif n < 7:
        reminder_variants = (("reminders",), ("reminders", "_reminders_backup"))
    elif n < 20:
        reminder_variants = (
            ("_reminders_backup",),
            ("reminders",),
            ("reminders", "_reminders_backup"),
        )
    else:
        reminder_variants = ((), ("reminders",))
    # rel003 can retain the local shadow if core134 ran first; durable current
    # heads do not encode that ordering. Both complete producer shapes are fixed
    # source alternatives. No runtime right is granted to either old identity.
    contacts_variants = (False,) if n == 0 else ((True,) if n < 3 else (False, True))
    backup_variants = (False, True) if n >= 30 else (False,)
    definitions = compile_function_manifest(
        manifest.profiles,
        manifest.migration_owner,
        (("relationship", "relationship", None),)
        + tuple(
            (chain, schema, None)
            for chain, schema, _ in selected
            if schema == "relationship" and chain != "relationship"
        ),
    )
    functions = []
    context_functions = []
    for row in definitions:
        if row.schema != "relationship":
            continue
        edge = _RELATIONSHIP_FUNCTION_EDGES.get(row.name)
        if edge is not None:
            if edge in path:
                functions.append(row)
            continue
        # Core-produced local compatibility functions need their own installed
        # core source edge, not the requested future target's chain.
        edge = _RELATIONSHIP_CONTEXT_FUNCTION_EDGES.get(row.producer)
        if edge is not None:
            chain, edge_revision = edge
            actual = next(
                (head for c, schema, head in selected if c == chain and schema == "relationship"),
                None,
            )
            if actual is not None and edge_revision in get_chain_revision_path(chain, actual):
                ordinary_settings = (
                    ("search_path=pg_catalog, connectors",)
                    if row.name == "connectors_filtered_events_ensure_partition"
                    else ()
                )
                variants = (
                    row,
                    replace(row, executors=()),
                    replace(
                        row,
                        owner=manifest.migration_owner,
                        executors=("PUBLIC",),
                        settings=ordinary_settings,
                    ),
                    replace(
                        row,
                        owner=manifest.migration_owner,
                        executors=(),
                        settings=ordinary_settings,
                    ),
                )
                context_functions.append(tuple(dict.fromkeys(variants)))
    # The core007 wrapper is a distinct ordinary invoker predecessor, not the
    # later core135 source body/definer state with its revision label changed.
    if core_heads:
        core_path = get_chain_revision_path("core", core_heads[0])
        if "core_007" in core_path and "core_135" not in core_path:
            header, body = _RELATIONSHIP_CORE007_WRAPPER
            context_functions.append(
                (
                    FunctionAuthority(
                        "relationship",
                        "connectors_filtered_events_ensure_partition",
                        "timestamp with time zone",
                        manifest.migration_owner,
                        ("PUBLIC",),
                        False,
                        header,
                        body,
                        "alembic/versions/core/core_007_connectors.py",
                        (),
                    ),
                )
            )
    relation_roles = {p.role for p in manifest.profiles if p.primary_schema == "relationship"}
    producer_grants = {
        "entity_facts": (13, _CRUD),
        "entity_predicate_registry": (14, _CRUD),
        "credentials": (16, _CRUD),
        "entity_view_marks": (21, _CRUD),
        "merge_reviews": (21, _CRUD),
        "fact_evidence": (34, _CRUD),
        "fact_coverage": (34, _CRUD),
        "fact_approval_context": (34, _CRUD),
        "meeting_debriefs": (36, _CRUD),
        "fact_approval_rule_context": (37, _CRUD),
        "fact_identity_decisions": (37, _CRUD),
    }
    namespace_current_fs = compile_function_manifest(
        manifest.profiles,
        manifest.migration_owner,
        (("relationship", "relationship", None),)
        + ((("core", "relationship", None),) if core_heads else ()),
    )
    result = []
    birth = {name: since for since, _, name in _RELATIONSHIP_TABLE_WINDOWS}
    birth.update({name + "_dedup_bak_rel_030": 30 for name in _RELATIONSHIP_REL030_BACKUPS})
    birth.update({"contacts": 1, "reminders": 1, "_reminders_backup": 7})
    # Finite actual transitions: producers from base, or a finalized current
    # source component downgraded to a lower source prefix and then upgraded.
    # A new table/function receives only its actual producer effects; retained
    # objects keep the prior current/closed posture. No finalizer is invented.
    transitions = [("producer", 0, core_phase) for core_phase in ("current", "closed")]
    transitions += [
        (phase, low, phase) for phase in ("current", "closed") for low in range(0, n + 1)
    ]
    # rel015/017 repeat the registry CRUD grant without recreating it. Their
    # inverses never revoke it, including a later inverse back to rel014/016.
    transitions += [
        (phase, low, phase + "+registry17")
        for phase in ("current", "closed")
        for low in range(14, n + 1)
    ]
    for contacts, backups, reminders, (phase, low, core_phase) in product(
        contacts_variants, backup_variants, reminder_variants, transitions
    ):
        registry_regranted = core_phase.endswith("+registry17")
        core_phase = core_phase.removesuffix("+registry17")
        state = (
            phase
            + ":"
            + str(low)
            + ":"
            + core_phase
            + ("+registry17" if registry_regranted else "")
        )
        population = names | set(reminders) | ({"contacts"} if contacts else set())
        if backups:
            population.update(name + "_dedup_bak_rel_030" for name in _RELATIONSHIP_REL030_BACKUPS)
        rows = []
        for name in sorted(population):
            row = declared.get(name) or ObjectAuthority(
                "relationship",
                name,
                ObjectKind.TABLE,
                manifest.migration_owner,
                (),
                "fixed Relationship predecessor producer window " + revision,
            )
            retained = phase != "producer" and birth.get(name, 0) <= low
            permissions = (
                row.permissions
                if (
                    (retained and phase == "current")
                    or (birth.get(name, 0) == 0 and core_phase == "current")
                )
                else ()
            )
            if not retained and birth.get(name, 0) > 0:
                grant = producer_grants.get(name)
                permissions = (
                    tuple(Permission(role, grant[1]) for role in sorted(relation_roles))
                    if grant
                    else ()
                )
                if name == "entity_facts" and n >= 33:
                    permissions += (Permission("connector_writer", _READ),)
                if name == "fact_coverage" and n >= 34:
                    permissions += (Permission("connector_writer", _READ),)
            elif name == "entity_facts" and low < 33:
                # rel033 downgrade revokes, and a later upgrade grants only
                # this exact fixed reader. It never restores other ACLs.
                permissions = tuple(p for p in permissions if p.role != "connector_writer")
                if n >= 33:
                    permissions += (Permission("connector_writer", _READ),)
            if name == "entity_predicate_registry" and (
                registry_regranted or any(low < edge <= n for edge in (15, 17))
            ):
                # Exactly the checked-in repeated grants; no namespace, other
                # table or purpose-owner right follows from these statements.
                by_role = {p.role: p for p in permissions}
                for role in relation_roles:
                    by_role[role] = Permission(role, _CRUD)
                permissions = tuple(by_role[role] for role in sorted(by_role))
            rows.append(replace(row, required=True, permissions=permissions))
        rows = tuple(rows)
        fs = []
        for row in functions:
            edge = _RELATIONSHIP_FUNCTION_EDGES.get(row.name)
            newly_produced = edge is not None and (phase == "producer" or int(edge[4:]) > low)
            if newly_produced:
                # Both actual Relationship function producers have no SET
                # clause and ordinary PostgreSQL PUBLIC EXECUTE initially.
                row = replace(row, executors=("PUBLIC",), settings=())
            elif (phase if edge else core_phase) == "closed":
                row = replace(row, executors=())
            fs.append(row)
        fs = tuple(fs)
        profile_manifest = replace(manifest, relations=rows)
        security = compiled_security_contract(
            profile_manifest, ordinary_purposes=phase != "producer"
        )
        allowed_function_names = {row.name for row in fs}
        security["triggers"] = [
            row for row in security["triggers"] if row["function_name"] in allowed_function_names
        ]
        # Namespace grants survive table/function drops. Derive that retained
        # posture from the fixed source base and rel033's explicit inverse,
        # not the final relation grant list or a catalog observation.
        if phase == "current":
            ns_rows = tuple(row for row in manifest.relations if row.schema == "relationship")
            users = {row.owner for row in ns_rows}
            users.update(p.role for row in ns_rows for p in row.permissions)
            current_fs = namespace_current_fs
            users.update(r.owner for r in current_fs if r.schema == "relationship")
            users.update(
                role for r in current_fs if r.schema == "relationship" for role in r.executors
            )
        else:
            users = {manifest.migration_owner}
            if core_phase == "current":
                core_names = {name for name in names if birth.get(name, 0) == 0}
                users.update(
                    p.role
                    for name in core_names
                    for p in declared.get(
                        name,
                        ObjectAuthority(
                            "relationship",
                            name,
                            ObjectKind.TABLE,
                            manifest.migration_owner,
                            (),
                            "core source",
                        ),
                    ).permissions
                )
        users.update(group[0].owner for group in context_functions)
        if phase != "producer" and low < 33:
            users.discard("connector_writer")
        if n >= 33 and (phase == "producer" or low < 33):
            users.add("connector_writer")
        result.append(
            ComponentAuthorityProfile(
                "relationship",
                "relationship",
                revision,
                state,
                rows,
                fs,
                tuple(
                    json.dumps(row, sort_keys=True, separators=(",", ":"))
                    for row in security["policies"]
                ),
                tuple(
                    json.dumps(row, sort_keys=True, separators=(",", ":"))
                    for row in security["triggers"]
                ),
                tuple(
                    (name, kind, required)
                    for since, name, kind, required in _RELATIONSHIP_FACT_COLUMNS
                    if since <= n
                ),
                tuple(name for since, name, _, _ in _RELATIONSHIP_FACT_COLUMNS if since > n),
                tuple(sorted(users)),
                context_relations,
                tuple(context_functions),
                (state,),
            )
        )
    # Equivalent source alternatives are one predicate, not an ambiguous runtime
    # authority choice. A differing catalog right never creates another profile.
    unique = {}
    for profile in result:
        key = (
            profile.relations,
            profile.functions,
            profile.policies,
            profile.triggers,
            profile.dependency_columns,
            profile.forbidden_columns,
            profile.namespace_users,
            profile.context_relations,
            profile.context_functions,
        )
        if key in unique:
            previous = unique[key]
            unique[key] = replace(
                previous, source_states=(*previous.source_states, *profile.source_states)
            )
        else:
            unique[key] = profile
    return tuple(unique.values())


@dataclass(frozen=True)
class PurposeAuthorityProfile:
    capabilities: tuple[PurposeCapability, ...]
    functions: tuple[FunctionAuthority, ...]
    public_capabilities: tuple[PurposeCapability, ...]
    public_relations: tuple[tuple[str, str], ...]
    public_functions: tuple[tuple[str, str, str], ...]
    public_context_functions: tuple[tuple[str, str, str], ...]
    public_schemas: tuple[str, ...]
    public_object_schemas: tuple[str, ...]
    public_default_owners: tuple[str, ...]


@dataclass(frozen=True, order=True)
class PurposeCapability:
    kind: str
    schema: str
    name: str
    signature: str
    column: str
    verb: str


def compile_owner_lookup_capabilities(manifest, *, component, ordinary_closed):
    """Compile the fixed source role allow-set with a fresh source snapshot."""
    return _compile_owner_lookup_capabilities(
        manifest,
        component=component,
        ordinary_closed=ordinary_closed,
        source_identity=_owner_lookup_source_template_identity(),
    )


def _compile_owner_lookup_capabilities(manifest, *, component, ordinary_closed, source_identity):
    """Full fixed-role source allow-set, never the role's observed grant list.

    Ordinary closure is independently established by the complete global
    component validator. A Relationship prefix's exact transition profile is
    independent. Existing current compiler defaults/callers are untouched.
    """
    from dataclasses import replace

    if type(ordinary_closed) is not bool or (
        component is not None and not isinstance(component, ComponentAuthorityProfile)
    ):
        raise AuthorityError("Owner lookup source capability profile is unavailable")
    role = "identity_lookup_owner"
    relations = (
        tuple(replace(row, permissions=()) for row in manifest.relations)
        if ordinary_closed
        else manifest.relations
    )
    if component is not None:
        relations = (
            tuple(row for row in relations if row.schema != "relationship") + component.relations
        )
    else:
        relations = tuple(row for row in relations if row.schema != "relationship")
    functions, protected = _owner_lookup_function_templates(
        manifest.profiles, manifest.migration_owner, source_identity
    )
    if ordinary_closed:
        functions = tuple(replace(row, executors=()) for row in functions)
    if component is not None:
        functions = (
            tuple(row for row in functions if row.schema != "relationship") + component.functions
        )
    else:
        functions = tuple(row for row in functions if row.schema != "relationship")
    functions = (*functions, *protected)
    result = set()
    for row in relations:
        for permission in row.permissions:
            if permission.role != role:
                continue
            for verb in permission.verbs:
                if permission.columns:
                    result.update(
                        PurposeCapability("column", row.schema, row.name, "", column, verb)
                        for column in permission.columns
                    )
                else:
                    result.add(PurposeCapability("relation", row.schema, row.name, "", "", verb))
            result.add(PurposeCapability("namespace", row.schema, "", "", "", "USAGE"))
    for row in functions:
        if role in row.executors and row.owner != role:
            result.add(
                PurposeCapability("function", row.schema, row.name, row.signature, "", "EXECUTE")
            )
        if role in (row.owner, *row.executors):
            result.add(PurposeCapability("namespace", row.schema, "", "", "", "USAGE"))
    if component is not None and role in component.namespace_users:
        result.add(PurposeCapability("namespace", component.schema, "", "", "", "USAGE"))
    for schema, creators in ordinary_schema_creators(relations).items():
        if role in creators:
            result.add(PurposeCapability("namespace", schema, "", "", "", "CREATE"))
    # No source default privilege, database, tablespace, language, type, large
    # object or foreign-server privilege belongs to this fixed purpose role.
    # In particular the current uuid_generate_v7 EXECUTE follows its real
    # compiler row, rather than an ad-hoc lookup/two-table whitelist.
    # PUBLIC is an effective principal even for NOINHERIT roles. Compare its
    # managed-object grants separately; ambient PostgreSQL/extension capabilities
    # outside these exact source identities are not reclassified as direct role
    # privileges. Context-function alternatives already receive full source/body/
    # ACL validation, so none of their observations selects a grant profile here.
    public_result = set()
    context_keys = (
        {row.identity for group in component.context_functions for row in group}
        if component is not None
        else set()
    )
    public_functions = tuple(row for row in functions if row.identity not in context_keys)
    public_declarations = (
        (*relations, *component.context_relations) if component is not None else relations
    )
    for row in public_declarations:
        for permission in row.permissions:
            if permission.role == "PUBLIC":
                for verb in permission.verbs:
                    if permission.columns:
                        public_result.update(
                            PurposeCapability("column", row.schema, row.name, "", column, verb)
                            for column in permission.columns
                        )
                    else:
                        public_result.add(
                            PurposeCapability("relation", row.schema, row.name, "", "", verb)
                        )
    for row in public_functions:
        if "PUBLIC" in row.executors:
            public_result.add(
                PurposeCapability("function", row.schema, row.name, row.signature, "", "EXECUTE")
            )
            if component is None or row.schema != component.schema:
                public_result.add(PurposeCapability("namespace", row.schema, "", "", "", "USAGE"))
    if component is not None and "PUBLIC" in component.namespace_users:
        public_result.add(PurposeCapability("namespace", component.schema, "", "", "", "USAGE"))
    for row in relations:
        if any(permission.role == "PUBLIC" for permission in row.permissions):
            public_result.add(PurposeCapability("namespace", row.schema, "", "", "", "USAGE"))
    for schema, creators in ordinary_schema_creators(relations).items():
        if "PUBLIC" in creators:
            public_result.add(PurposeCapability("namespace", schema, "", "", "", "CREATE"))
    # Reachability is source-defined, not inferred from observed PUBLIC grants.
    # Ordinary wrappers in other target schemas can keep their real producer
    # PUBLIC EXECUTE without becoming accessible to this fixed purpose role.
    object_schemas = {
        row.schema
        for row in manifest.relations
        if any(permission.role == role for permission in row.permissions)
    } | {row.schema for row in functions if role in (row.owner, *row.executors)}
    object_schemas.update(
        row.schema for row in public_result if row.kind == "namespace" and row.verb == "USAGE"
    )
    if component is not None:
        object_schemas.add(component.schema)
    return PurposeAuthorityProfile(
        tuple(sorted(result)),
        tuple(row for row in functions if role in row.executors and row.owner != role),
        tuple(sorted(public_result)),
        tuple(sorted({(row.schema, row.name) for row in public_declarations})),
        tuple(sorted({row.identity for row in public_functions})),
        tuple(sorted(context_keys)),
        tuple(sorted({row.schema for row in (*relations, *functions)})),
        tuple(sorted(object_schemas)),
        tuple(
            sorted(
                {manifest.migration_owner, manifest.maintenance_owner}
                | {row.owner for row in (*relations, *functions)}
            )
        ),
    )
