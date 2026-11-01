## MODIFIED Requirements

### Requirement: Agent Type Awareness
The daemon SHALL read the `type` field from `butler.toml` config and apply type-specific behaviors at well-defined decision points. The daemon engine remains unified — there is no separate `StafferDaemon` class.

Source: RFC 0038 D1, D2 and D7
Scope: v1-mandatory

#### Scenario: Config parsing includes type
- **WHEN** the daemon loads `butler.toml`
- **THEN** it parses `[butler].type` as a `ButlerType` enum (`BUTLER` or `STAFFER`)
- **AND** defaults to `ButlerType.BUTLER` if the field is absent
- **AND** the `ButlerConfig` dataclass exposes `config.type` for downstream decision points

#### Scenario: Config parsing includes permissions
- **WHEN** the daemon loads `butler.toml` with a `[butler.permissions]` section
- **THEN** it parses `cross_butler_access` as a list of strings
- **AND** defaults to an empty list if the section or field is absent
- **AND** the `ButlerConfig` dataclass exposes `config.permissions.cross_butler_access`

#### Scenario: Staffer-specific startup behaviors
- **WHEN** the daemon starts with `config.type == ButlerType.STAFFER`
- **THEN** it proceeds through the same lifecycle phases as a butler
- **AND** during schedule sync, it skips registration of any `daily_briefing_contribution` schedule entries
- **AND** during switchboard registration, it includes `type = "staffer"` in the registration payload so the control plane's registry and capability catalog can tell staffers from domains

#### Scenario: Butler-specific startup behaviors
- **WHEN** the daemon starts with `config.type == ButlerType.BUTLER`
- **THEN** it proceeds through the full lifecycle with none of the staffer-specific skips
- **AND** it registers the core tools that are withheld from staffers (for example session queries, schedule mutation, and temporal tools) when their groups are enabled
- **AND** it reports `type = "butler"` to the switchboard registry

## REMOVED Requirements

### Requirement: Core Tool Surface
**Reason**: RFC 0038 D7 deletes `trigger`, `route.execute`, the generic `route` tool, `route_to_butler`, `cancel_session` and the delegation tools at cutover, so the always-registered `route.execute` rule and its scenario SHALL no longer hold; the requirement is restated without them.

**Migration**: See "Core Tool Groups Without Session-Starting Tools" (`REQ-core-daemon-005`) in this spec. Goal invocations reach tools only through broker grants (`REQ-capability-authorization-002`, `REQ-capability-authorization-004`); owner Stop replaces `cancel_session` (`REQ-capability-authorization-011`).

### Requirement: Delegation Core Tool Inventory And Admission Boundary
**Reason**: RFC 0038 D7 deletes the `delegate_*` tools and the cross-butler delegation ledger at cutover; no daemon SHALL register a delegation group.

**Migration**: A lead calls another domain's capability directly through the broker or delegates a depth-1 child with `goal.delegate` (`REQ-goal-orchestration-001`, `REQ-goal-orchestration-003`, `REQ-capability-authorization-005`).

### Requirement: Delegation Inventory Does Not Activate Runtime Configuration
**Reason**: The delegation inventory this requirement constrained no longer exists after RFC 0038 D7; no runtime configuration SHALL reference a delegation group.

**Migration**: None; see the removal of "Delegation Core Tool Inventory And Admission Boundary" above.

## ADDED Requirements

### Requirement: Assertion Verification Keys Load at Startup and Fail Closed
Each domain daemon SHALL load the broker's assertion verification keys at startup from non-secret deployment configuration that lists the current and next key identifiers, and SHALL hold no assertion signing key. A daemon without valid verification keys SHALL refuse every model-reachable tool call and SHALL NOT fall back to unsigned requests or a shared secret. Key rotation SHALL take effect by publishing the next public key and restarting domains before the signer switches; a key SHALL stop verifying once removed from the configuration.

ID: REQ-core-daemon-004
Source: RFC 0038 D7 (Signing key)
Scope: v1-mandatory

#### Scenario: Missing verification keys fail closed
- **WHEN** a domain daemon starts without valid verification keys
- **THEN** every model-reachable tool call it receives is refused before its handler runs
- **AND** its service-plane tools keep serving their existing callers

#### Scenario: Rotation overlaps current and next keys
- **WHEN** the next public key has been published and domains have restarted, and the signer then switches to it
- **THEN** assertions signed under either the current or the next key identifier verify
- **AND** after the old key is removed from the configuration, an assertion signed under it is refused

#### Scenario: Domains never hold the signing key
- **WHEN** a domain daemon's configuration and process environment are inspected
- **THEN** they contain verification keys only and no `GOAL_BROKER_SIGNING_KEY` material

### Requirement: Core Tool Groups Without Session-Starting Tools
Every butler daemon registers core MCP tools based on the `core_groups` allowlist from `runtime_config` (DB) and the butler's type/name. The daemon SHALL treat that stored allowlist as a projection, not independent capability authority: Git-owned `[butler.runtime_seed].core_groups` defines the declared surface, and a DB value may narrow it only when `core_groups_narrowing_reason` is non-empty. An unreasoned stale value is reconciled to Git at startup. When the effective `core_groups` is NULL, all groups are enabled (backward compat). When set, only tools in the listed groups are registered.
No daemon SHALL register the session-starting and proxy tools that RFC 0038 removes: `trigger`, the session-dispatch `route.execute`, the generic `route` tool, `route_to_butler`, `cancel_session`, and the delegation tools. Messenger's `notify.v1` delivery termination, which today shares the `route.execute` name, survives as a service-plane endpoint that only Switchboard `deliver()` calls (RFC 0038 D7). Registration on a daemon's canonical MCP endpoint SHALL NOT make a tool reachable by a goal invocation: only LLM-presentable tools with complete authorization metadata are grantable through the broker, and service-plane tools (`ingest`, `tick`, `connector.heartbeat`, backfill and callback tools) SHALL never be grantable.
This requirement supersedes the tier-based system (UNIVERSAL/DOMAIN/MESSENGER/SWITCHBOARD constants and the `_tools_to_remove` post-registration pruning) documented in RFC 0002 §Tool Budget Discipline. The tier constants (`UNIVERSAL_CORE_TOOL_NAMES`, `DOMAIN_CORE_TOOL_NAMES`, `MESSENGER_CORE_TOOL_NAMES`) are removed.
Name-gated tools (messenger-only, switchboard-only) are gated by butler name as an additional check — `core_groups` controls which groups are *eligible*, but `switchboard_routing` and `switchboard_backfill` tools are ONLY registered when `butler_name == "switchboard"`, regardless of core_groups. Similarly, `delivery_preferences_*` and `deferred_notification_*` tools are ONLY registered when `butler_name == "messenger"`. This prevents a domain butler from accidentally gaining switchboard ingest powers by adding `switchboard_routing` to its core_groups.
Tool groups:
- `infra`: status, tick, correct
- `state`: state_get, state_set, state_delete, state_list
- `scheduling`: schedule_list, schedule_create, schedule_update, schedule_delete, schedule_trigger, schedule_costs
- `sessions`: sessions_list, sessions_get, sessions_summary, sessions_daily, top_sessions
- `notifications`: notify, remind
- `media`: attachment_view, get_attachment
- `temporal`: deadline_*, event_chain_*, seasonal_period_*
- `module_mgmt`: module.states, module.set_enabled
- `switchboard_routing`: ingest, connector.heartbeat (name-gated: switchboard only)
- `switchboard_backfill`: backfill.poll, backfill.progress (name-gated: switchboard only)

ID: REQ-core-daemon-005
Source: RFC 0038 D4 and D7; RFC 0002 §Core Tools, §Tool Budget Discipline (superseded); Doctrine Rule #5 (operational tuning is DB-persisted)
Scope: v1-mandatory

#### Scenario: core_groups filters tool registration
- **WHEN** a butler daemon starts with `core_groups = ['infra', 'notifications']` in runtime_config
- **AND** that set is a strict subset of its Git declaration carrying a non-empty narrowing reason
- **THEN** only tools in the `infra` and `notifications` groups SHALL be registered on the MCP server
- **AND** tools in other groups (state, scheduling, sessions, media, temporal) SHALL NOT be registered

#### Scenario: Unreasoned runtime row cannot hide a Git capability
- **WHEN** a butler daemon's runtime_config row omits one or more Git-declared groups and has no narrowing reason
- **THEN** the Git declaration SHALL become the effective group set before tool registration
- **AND** the stored row and audit log SHALL be reconciled idempotently

#### Scenario: NULL core_groups enables all tools
- **WHEN** a butler daemon starts with `core_groups = NULL` in runtime_config
- **THEN** all core tool groups SHALL be registered (backward compatibility)

#### Scenario: Session-starting tools are never registered
- **WHEN** any daemon starts, regardless of its core_groups value or type
- **THEN** `trigger`, the session-dispatch `route.execute`, `route`, `route_to_butler`, `cancel_session` and the delegation tools SHALL NOT be registered on its MCP server
- **AND** reasoning work requested through any remaining core tool reaches a session only through goal admission
- **AND** Messenger's service-plane delivery endpoint, if registered, is not LLM-presentable and accepts calls only from Switchboard `deliver()`

#### Scenario: Service-plane tools are never grantable
- **WHEN** the capability catalog is assembled from a daemon's registry
- **THEN** `ingest`, `tick`, `connector.heartbeat` and the backfill tools are not LLM-presentable and appear in no goal invocation's grant
- **AND** they keep serving their existing service-plane callers

#### Scenario: Switchboard-only tools name-gated
- **WHEN** a non-switchboard butler has `switchboard_routing` in its core_groups
- **THEN** `ingest` and `connector.heartbeat` SHALL NOT be registered
- **AND** the daemon SHALL log a warning about the ineffective group

#### Scenario: Messenger-only tools name-gated
- **WHEN** a non-messenger butler has core_groups that would include messenger tools
- **THEN** `delivery_preferences_set`, `delivery_preferences_get`, `deferred_notifications_list`, `deferred_notification_cancel` SHALL NOT be registered

#### Scenario: Domain tools excluded from staffers via core_groups
- **WHEN** a staffer starts and its core_groups does not include `temporal`
- **THEN** deadline, event_chain, and seasonal_period tools SHALL NOT be registered
