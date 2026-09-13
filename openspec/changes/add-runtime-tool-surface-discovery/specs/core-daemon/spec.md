## MODIFIED Requirements

### Requirement: Core Tool Surface
Every butler daemon SHALL register core MCP tools from the effective `core_groups` allowlist and the butler's type/name. Git-owned `[butler.runtime_seed].core_groups` defines the declared capability surface. A DB `runtime_config.core_groups` value may narrow that declaration only when `core_groups_narrowing_reason` is non-empty; an unreasoned stale value is reconciled to Git at startup. When the effective `core_groups` is NULL, all groups are enabled (backward compat). When set, only tools in the listed groups are registered.

This requirement **supersedes** the tier-based system (UNIVERSAL/DOMAIN/MESSENGER/SWITCHBOARD constants and the `_tools_to_remove` post-registration pruning) documented in RFC 0002 §Tool Budget Discipline. The tier constants (`UNIVERSAL_CORE_TOOL_NAMES`, `DOMAIN_CORE_TOOL_NAMES`, `MESSENGER_CORE_TOOL_NAMES`) are removed. RFC 0002 §Tool Budget Discipline requires amendment to reflect the `core_groups` mechanism. The target removal SHALL also include `CHRONICLER_CORE_TOOL_NAMES`, the combined `CORE_TOOL_NAMES` catalog, and every same-repository compatibility alias for those catalogs; actual registration behavior and a mechanically derived inventory SHALL replace circular catalog assertions.

- The complete merged-tree inventory contains 79 unique registrations: 71 tools in 14 groups plus eight direct registrations.
- `infra` (11): status, trigger, tick, correct, memory_access, memory_catalog_fetch, conversation_reply, conversation_recall, conversation_thread_read, shutdown, chronicler_day_close_refresh. Only chronicler_day_close_refresh is name-gated, to chronicler.
- `state` (4): state_get, state_set, state_delete, state_list. No type/name gate.
- `scheduling` (6): schedule_list, schedule_create, schedule_update, schedule_delete, schedule_trigger, schedule_costs. Only schedule_trigger and schedule_costs are non-staffer-only.
- `sessions` (5): sessions_list, sessions_get, sessions_summary, sessions_daily, top_sessions. All five are non-staffer-only.
- `notifications`: notify, remind. The group has 2 tools; only notify is non-staffer-only.
- `media` (1): get_attachment. No type/name gate.
- `graph` (2): entity_graph_walk, entity_graph_path. No type/name gate; both remain subject to the graph group allowlist.
- `temporal`: deadline_*, event_chain_*, seasonal_period_*. The complete 13-tool inventory is deadline_create, deadline_update, deadline_list, deadline_delete, event_chain_create, event_chain_update, event_chain_list, event_chain_delete, seasonal_period_create, seasonal_period_update, seasonal_period_list, seasonal_period_delete, seasonal_period_create_preset. All thirteen are non-staffer-only.
- `module_mgmt` (2): module.states, module.set_enabled. No type/name gate.
- `switchboard_routing`: ingest, route_to_butler, connector.heartbeat (name-gated: switchboard only). The complete 6-tool inventory also includes answer_question, cannot_answer, and file_bug_report; all six are name-gated to switchboard.
- `switchboard_backfill`: backfill.poll, backfill.progress (name-gated: switchboard only). The group has 2 tools and both are name-gated to switchboard.
- `delegation` (4): delegate_ask, delegate_receive, delegate_answer, delegate_wake. All four are non-staffer-only.
- `domain_events` (6): publish_event, subscribe_to_event, unsubscribe_from_event, list_my_subscriptions, receive_domain_event, report_event_reaction. All six are non-staffer-only.
- `fleet_cases` (7): find_open_case, open_case, contribute_case_evidence, propose_case_posture, close_case, record_case_link, read_case. No type/name registration gate; all seven register on staffers including Switchboard, while call-time forwarding and write authority remain separate handler checks.
- Direct universal registrations (2): route.execute and cancel_session. Both bypass core_groups and are registered for every type/name.
- Direct Messenger registrations (6): delivery_preferences_set, delivery_preferences_get, deferred_notifications_list, deferred_notification_cancel, scheduling_preferences_set, scheduling_preferences_get. They bypass core_groups but register only when `butler_name == "messenger"`; they do not constitute a fifteenth group.
- Name-gated tools (messenger-only, switchboard-only) are gated by butler name as an additional check — `core_groups` controls which groups are *eligible*, but `switchboard_routing` and `switchboard_backfill` tools are ONLY registered when `butler_name == "switchboard"`, regardless of core_groups. Similarly, `delivery_preferences_*` and `deferred_notification_*` tools are ONLY registered when `butler_name == "messenger"`. This prevents a domain butler from accidentally gaining switchboard routing powers by adding `switchboard_routing` to its core_groups. The merged-tree refinement is that all six Messenger tools are direct registrations outside core_groups, including the two scheduling_preferences tools named above.
- **`route.execute` special handling:** `route.execute` is registered on the MCP server for all butlers regardless of `core_groups` because the Switchboard calls it server-to-server. Per RFC 0002, `route.execute` is an infrastructure endpoint, not an LLM-facing tool. LLM-visibility filtering (hiding `route.execute` from the LLM's tool list while keeping the MCP handler callable) is deferred to a future change — the current `core_groups` mechanism is single-tier (registered or not) and does not support "registered but hidden from LLM."
- **`cancel_session` special handling:** `cancel_session` is likewise registered for all butlers regardless of `core_groups` or butler type because the dashboard calls it server-to-server to stop an in-flight runtime. It is an infrastructure endpoint, not an LLM-facing tool.
- RFC 0027 supersedes the historical visibility deferral in the preceding bullets. Core tool registration SHALL remain group/type/name gated exactly as above, while a separate adapter-rendered LLM-presentation layer SHALL hide infrastructure-only handlers from model context/native search without removing them from canonical FastMCP `tools/list` or the handlers needed by infrastructure callers. The presentation layer is not a new caller-authentication boundary and does not replace existing handler validation. `route.execute` and `cancel_session` are mandatory infrastructure-only classifications; the complete inventory is governed by `core-tool-discovery`.

ID: REQ-core-daemon-002
Source: RFC 0002 §Core Tools, §Tool Budget Discipline (superseded by this change), Doctrine Rule #5 (operational tuning is DB-persisted)
Scope: v1-mandatory

#### Scenario: core_groups filters tool registration
- **WHEN** a non-Messenger domain butler daemon's Git declaration includes additional groups but runtime_config stores `core_groups = ['infra', 'notifications']` with a non-empty narrowing reason
- **THEN** only tools in the `infra` and `notifications` groups SHALL be registered on the MCP server (plus `route.execute` and `cancel_session`, which are always registered)
- **AND** tools in other groups (state, scheduling, sessions, media, temporal) SHALL NOT be registered
- **AND** tools in the additional current groups (`graph`, `module_mgmt`, `switchboard_routing`, `switchboard_backfill`, `delegation`, `domain_events`, `fleet_cases`) SHALL NOT be registered
- **AND** the type/name gates inside enabled groups still apply

#### Scenario: Unreasoned runtime row cannot hide a Git capability
- **WHEN** a butler daemon's runtime_config row omits one or more Git-declared groups and has no narrowing reason
- **THEN** the Git declaration SHALL become the effective group set before tool registration
- **AND** the stored row and audit log SHALL be reconciled idempotently

#### Scenario: NULL core_groups enables all tools
- **WHEN** a butler daemon starts with `core_groups = NULL` in runtime_config
- **THEN** all core tool groups SHALL be registered (backward compatibility)
- **AND** in this scenario "all core tool groups" SHALL mean all fourteen groups are eligible before the independent type/name gates filter them
- **AND** direct universal and Messenger-only registrations SHALL retain their independent gates

#### Scenario: route.execute always registered
- **WHEN** any butler daemon starts, regardless of core_groups value
- **THEN** `route.execute` SHALL be registered on the MCP server
- **AND** it SHALL be callable by the Switchboard for routed message delivery
- **AND** it SHALL remain present in canonical `tools/list` but absent from the adapter-rendered model presentation
- **AND** `cancel_session` SHALL likewise remain registered for dashboard server-to-server cancellation and absent from LLM presentation

#### Scenario: Switchboard-only tools name-gated
- **WHEN** a non-switchboard butler has `switchboard_routing` in its core_groups
- **THEN** `ingest`, `route_to_butler`, and `connector.heartbeat` SHALL NOT be registered
- **AND** `answer_question`, `cannot_answer`, and `file_bug_report` SHALL NOT be registered
- **AND** the daemon SHALL log a warning about the ineffective group

#### Scenario: Messenger-only tools name-gated
- **WHEN** a non-messenger butler has core_groups that would include messenger tools
- **THEN** `delivery_preferences_set`, `delivery_preferences_get`, `deferred_notifications_list`, `deferred_notification_cancel` SHALL NOT be registered
- **AND** `scheduling_preferences_set` and `scheduling_preferences_get` SHALL NOT be registered
- **AND** because all six are direct registrations, the same outcome SHALL hold for every other core_groups value
- **AND** a Messenger daemon SHALL register those six direct tools independently of core_groups

#### Scenario: Domain tools excluded from staffers via core_groups
- **WHEN** a staffer starts and its core_groups does not include `temporal`
- **THEN** deadline, event_chain, and seasonal_period tools SHALL NOT be registered
- **AND** when a staffer instead includes `sessions`, `temporal`, `delegation`, `domain_events`, `notifications`, and `scheduling`, the independent type gates SHALL still exclude all five session tools, all thirteen temporal tools, all four delegation tools, all six domain-event tools, `notify`, `schedule_trigger`, and `schedule_costs`
- **AND** `remind` plus schedule list/create/update/delete SHALL remain eligible because those tools have no type gate

#### Scenario: Infrastructure visibility is independently projected

- **WHEN** a core handler is registered for infrastructure use but excluded from LLM presentation
- **THEN** the existing infrastructure caller retains the canonical handler and schema
- **AND** the adapter artifact omits its name and schema before model serialization/search without claiming new call-time authorization

### Requirement: Config loading parses runtime_seed section
The daemon config loader SHALL parse `[butler.runtime_seed]` from the toml and return a `RuntimeSeedConfig` dataclass. The old `[butler.runtime]` and `[butler.seed_configs]` sections SHALL NOT be rejected with a clear error; they SHALL be accepted and ignored. The dataclass is operational-only: retired `model`, `runtime_type`, `args`, and `session_timeout_s` keys inside `[butler.runtime_seed]` SHALL be rejected, while the obsolete top-level `[runtime]` section SHALL be rejected with deletion guidance. The target dataclass SHALL add `tool_exposure_policy` as one further operational field without restoring any retired runtime-selection field.

ID: REQ-core-daemon-003
Source: RFC 0001 §Startup Phases (phase 1 — config load), Doctrine Rule #5
Scope: v1-mandatory

#### Scenario: Parse runtime_seed section
- **WHEN** `load_config()` reads a toml with `[butler.runtime_seed]`
- **THEN** a `RuntimeSeedConfig` SHALL NOT be returned with fields: core_groups (tuple[str,...] | None), model (str | None), runtime_type (str, default "codex"), args (tuple[str,...], default ()), max_concurrent_sessions (int, default 3), max_queued_sessions (int, default 10), session_timeout_s (int, default 900), liveness_ttl_seconds (int, default 300), route_contract_min (int, default 1), route_contract_max (int, default 1)
- **AND** it SHALL instead contain exactly these current fields: `core_groups` (`tuple[str, ...] | None`, default `None`), `catalog_read_sensitivity` (`normal | internal | confidential`, default `normal`), `max_concurrent_sessions` (`int`, default `3`), `max_queued_sessions` (`int`, default `10`), `liveness_ttl_seconds` (`int`, default `300`), `route_contract_min` (`int`, default `1`), and `route_contract_max` (`int`, default `1`)
- **AND** the target dataclass SHALL add `tool_exposure_policy` with default `eager_filtered` and accepted values `eager_filtered` or `auto`
- **AND** `model`, `runtime_type`, `args`, and `session_timeout_s` SHALL NOT be dataclass fields and SHALL raise `ConfigError` when supplied inside `[butler.runtime_seed]`

#### Scenario: Reject old [butler.runtime] section
- **WHEN** `load_config()` reads a toml with `[butler.runtime]`
- **THEN** a `ConfigError` SHALL NOT be raised with message directing the user to rename to `[butler.runtime_seed]`; the obsolete nested section SHALL be accepted and ignored
- **AND** the nested section SHALL NOT alter the returned `RuntimeSeedConfig` or runtime selection

#### Scenario: Reject old [butler.seed_configs] section
- **WHEN** `load_config()` reads a toml with `[butler.seed_configs]`
- **THEN** a `ConfigError` SHALL NOT be raised with message directing the user to merge into `[butler.runtime_seed]`; the obsolete nested section SHALL be accepted and ignored
- **AND** the nested section SHALL NOT alter the returned `RuntimeSeedConfig`

#### Scenario: Obsolete top-level [runtime] section is rejected
- **WHEN** `load_config()` reads a toml with a top-level `[runtime]` section
- **THEN** a `ConfigError` SHALL state that the section is no longer supported and direct the operator to delete it
- **AND** the error SHALL NOT direct the operator to move runtime selection into `[butler.runtime_seed]`

#### Scenario: Missing runtime_seed section uses defaults
- **WHEN** `load_config()` reads a toml with no `[butler.runtime_seed]` section
- **THEN** a `RuntimeSeedConfig` with all default values SHALL be returned (backward compat for minimal tomls)
- **AND** the default tool exposure policy SHALL be `eager_filtered`
