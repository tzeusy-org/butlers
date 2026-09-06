# RFC 0002: MCP Tool Surface and Modules

**Status:** Accepted
**Date:** 2026-03-24

## Summary

Every butler is a long-running FastMCP HTTP server whose tool surface is assembled from two layers: daemon-owned core tools selected by group/type/name gates plus direct infrastructure registrations, and module tools opted into per butler. The canonical MCP transport is streamable HTTP at `/mcp`; legacy clients retain SSE compatibility at `/sse` plus `/messages`. Modules implement the `Module` abstract base class and are resolved in topological dependency order. Core and module registrations use different wrappers with explicit ownership of spans, module-state gating, logging, and session-attributed tool-call capture. Ephemeral LLM sessions connect exclusively to their own butler's canonical MCP endpoint via generated runtime configuration.

## Motivation

The tool surface defines the contract between a butler and the LLM instances it spawns. Separating core tools from module tools ensures every butler has a baseline capability set while allowing domain-specific extension without touching core infrastructure. The logging proxy provides ground-truth observability for every tool invocation without requiring modules to instrument themselves. Ephemeral MCP config generation enforces the architectural boundary where inter-butler communication flows exclusively through the Switchboard (see RFC 0003).

## Design

### FastMCP HTTP Server

At startup, the daemon creates one `FastMCP` instance, registers core tools in RFC 0001 phase 13, registers module tools and gates in phase 14, and starts a unified HTTP server on the butler's configured port in phase 15. `ButlerDaemon._build_mcp_http_app()` exposes canonical streamable HTTP at `/mcp` and mounts the same registry's legacy SSE routes at `/sse` plus `/messages`. The server remains running for the daemon's lifetime, and all tool registrations complete before either transport accepts calls.

### Core Tools

The daemon owns one core registration dispatcher independent of module
configuration. The merged-tree inventory contains 79 unique core tool
registrations: 71 are assigned to one of 14 `core_groups`, two are universal
direct infrastructure registrations, and six are Messenger-only direct
registrations. The exhaustive names and their registration gates are recorded
under [Core Tool Gating via `core_groups`](#core-tool-gating-via-core_groups).
No single butler necessarily receives all 79: `core_groups`, type gates, and
name gates reduce the registered set before the server starts.

Core registrations pass through `_ToolCallLoggingMCP`, which owns structured
call logging and session-attributed capture. Core handlers retain ownership of
their explicit `tool_span` decorators or manually constructed spans; the core
wrapper does not synthesize a second span. Direct infrastructure registrations
retain the same canonical logging/capture path even though they bypass the
group decorator.

### Module ABC

Modules add domain-specific tools by implementing the `Module` abstract base class (`src/butlers/modules/base.py`):

```python
class Module(abc.ABC):
    @property
    def name(self) -> str: ...              # Unique module identifier
    @property
    def config_schema(self) -> type[BaseModel]: ...  # Pydantic config model
    @property
    def dependencies(self) -> list[str]: ...  # Names of prerequisite modules

    async def register_tools(self, mcp, config, db, butler_name) -> None: ...
    def migration_revisions(self) -> str | None: ...
    async def on_startup(self, config, db, credential_store=None) -> None: ...
    async def on_shutdown(self) -> None: ...
    def tool_metadata(self) -> dict[str, ToolMeta]: ...
```

Key constraints:

- Modules MUST only add tools via `register_tools()`. They MUST NOT touch core infrastructure (scheduler, spawner, session log).
- Modules declare dependencies via the `dependencies` property. The daemon resolves these using topological sort, detecting cycles at startup (RFC 0001, phase 3).
- `on_startup()` receives an optional `CredentialStore` for DB-first credential resolution.
- `migration_revisions()` returns the Alembic branch label for the module's migration chain, or `None` if the module has no tables (see RFC 0006).

### Module Registry

The `ModuleRegistry` (`src/butlers/modules/registry.py`) maps module names to their implementing classes. A `default_registry()` function returns the built-in registry. Butler TOML config references modules by name; the registry resolves names to instances during phase 3.

### Registration Wrappers

Core tool registrations pass through `_ToolCallLoggingMCP`. It logs calls,
records bounded session-attributed inputs and fingerprints, captures outcomes,
and emits structured failure logs. Span ownership stays with the core handler;
the wrapper does not add an automatic span.

Module tool registrations pass through `_SpanWrappingMCP`. In addition to the
same logging, capture, and failure reporting, it creates the
`butler.tool.<name>` span, enforces live module enabled/disabled state at call
time, records the tool-to-module map, and fails closed if a non-Messenger module
attempts to register channel-egress ownership. Both wrappers decorate the
handler registered on the one canonical FastMCP registry; neither creates a
second invocation path.

### Tool Sensitivity Metadata

The `ToolMeta` dataclass allows modules to declare per-argument sensitivity:

```python
@dataclass
class ToolMeta:
    arg_sensitivities: dict[str, bool] = field(default_factory=dict)
```

Modules return a `dict[str, ToolMeta]` from `tool_metadata()`. The approvals module (RFC 0001, phase 14) uses this metadata to determine which tool calls require human approval. Arguments not explicitly listed fall back to a heuristic-based sensitivity classifier.

### Skills Infrastructure

Each butler can have a skills directory at `roster/<butler>/.agents/skills/`. Skills are directories containing a `SKILL.md` file describing a capability the LLM can use.

The skills subsystem (`src/butlers/core/skills.py`) provides:

- **`read_system_prompt(config_dir, butler_name)`** -- Reads `CLAUDE.md` from the butler's config directory, resolves `<!-- @include path.md -->` directives relative to the roster directory, appends shared snippets (`BUTLER_SKILLS.md`, `MCP_LOGGING.md`).
- **`get_skills_dir(config_dir)`** -- Returns path to `.agents/skills/` if it exists.
- **`list_valid_skills(skills_dir)`** -- Lists skill directories with valid kebab-case names (pattern: `^[a-z][a-z0-9]*(-[a-z0-9]+)*$`), warning and skipping invalid names.
- **`read_agents_md` / `write_agents_md` / `append_agents_md`** -- Read/write access to `AGENTS.md`, the runtime agent notes file for persistent inter-session memory.

### Ephemeral MCP Config Generation

When the Spawner (RFC 0001) invokes an LLM session, it generates temporary runtime MCP configuration containing:

- The butler's canonical streamable-HTTP `/mcp` URL with a `runtime_session_id` query parameter for tool-call attribution.
- No other MCP servers.

The `/sse` and `/messages` routes remain available only for legacy/internal
clients that have not migrated to streamable HTTP; they are not the generated
runtime-session default.

The `runtime_session_id` query parameter allows the tool call logging proxy to attribute tool invocations to the correct session record, even when multiple sessions run concurrently (if `max_concurrent_sessions > 1`).

The LLM is sandboxed to its own butler's tools. It cannot reach other butlers directly. Inter-butler communication flows exclusively through the Switchboard (see RFC 0003).

### Approval Gates

During phase 14, the daemon applies approval gates to configured tools. The `apply_approval_gates()` function wraps designated tool handlers with an approval check that:

1. Evaluates the tool call against standing approval rules.
2. If no rule matches, creates a pending approval action and blocks execution.
3. Returns the approval decision (approved/rejected/expired) to the caller.

Tool sensitivity metadata from `tool_metadata()` informs which arguments are safety-critical for approval rule matching.

### Tool Budget Discipline

Under eager presentation, every LLM-presentable registered definition costs
tokens at discovery time. At high tool counts (90-157), this overhead degrades
model performance --- especially on smaller or cheaper models --- by consuming
context window and reducing tool-selection accuracy. RFC 0027 therefore sets a
target of **30-50 full definitions initially loaded per session** while keeping
registration-time group and manifesto boundaries authoritative.

#### Core Tool Gating via `core_groups`

Core tools are organized into named **groups** and gated at registration time
by the `core_groups` allowlist from the per-schema `runtime_config` table. When
`core_groups` is NULL, all groups are registered (backward compatibility). When
set, only tools belonging to the listed groups are registered on the MCP server.

The complete merged-tree group inventory is:

| Group | Count | Tools | Additional registration gate |
|-------|------:|-------|------------------------------|
| `infra` | 11 | `status`, `trigger`, `tick`, `correct`, `memory_access`, `memory_catalog_fetch`, `conversation_reply`, `conversation_recall`, `conversation_thread_read`, `shutdown`, `chronicler_day_close_refresh` | `chronicler_day_close_refresh` requires `butler_name == "chronicler"`; the other ten have no type/name gate. |
| `state` | 4 | `state_get`, `state_set`, `state_delete`, `state_list` | None. |
| `scheduling` | 6 | `schedule_list`, `schedule_create`, `schedule_update`, `schedule_delete`, `schedule_trigger`, `schedule_costs` | `schedule_trigger` and `schedule_costs` require a non-staffer; the other four do not. |
| `sessions` | 5 | `sessions_list`, `sessions_get`, `sessions_summary`, `sessions_daily`, `top_sessions` | All five require a non-staffer. |
| `notifications` | 2 | `remind`, `notify` | `notify` requires a non-staffer; `remind` does not. |
| `media` | 1 | `get_attachment` | None. |
| `graph` | 2 | `entity_graph_walk`, `entity_graph_path` | None; both are group-gated but available to every butler type. |
| `temporal` | 13 | `deadline_create`, `deadline_update`, `deadline_list`, `deadline_delete`, `event_chain_create`, `event_chain_update`, `event_chain_list`, `event_chain_delete`, `seasonal_period_create`, `seasonal_period_update`, `seasonal_period_list`, `seasonal_period_delete`, `seasonal_period_create_preset` | All thirteen require a non-staffer. |
| `module_mgmt` | 2 | `module.states`, `module.set_enabled` | None. |
| `switchboard_routing` | 6 | `ingest`, `route_to_butler`, `answer_question`, `cannot_answer`, `file_bug_report`, `connector.heartbeat` | All six require `butler_name == "switchboard"`. |
| `switchboard_backfill` | 2 | `backfill.poll`, `backfill.progress` | Both require `butler_name == "switchboard"`. |
| `delegation` | 4 | `delegate_ask`, `delegate_receive`, `delegate_answer`, `delegate_wake` | All four require a non-staffer. |
| `domain_events` | 6 | `publish_event`, `subscribe_to_event`, `unsubscribe_from_event`, `list_my_subscriptions`, `receive_domain_event`, `report_event_reaction` | All six require a non-staffer. |
| `fleet_cases` | 7 | `find_open_case`, `open_case`, `contribute_case_evidence`, `propose_case_posture`, `close_case`, `record_case_link`, `read_case` | None; all seven are group-gated but registered for every butler type, including Switchboard. Call-time forwarding and write authority remain separate handler concerns. |

The eight direct registrations are outside `KNOWN_CORE_GROUPS` and therefore
do not become selectable merely by adding a group name:

| Direct registration | Count | Tools | Gate |
|---------------------|------:|-------|------|
| Universal infrastructure | 2 | `route.execute`, `cancel_session` | Always registered, regardless of `core_groups`, type, or name. |
| Messenger infrastructure | 6 | `delivery_preferences_set`, `delivery_preferences_get`, `deferred_notifications_list`, `deferred_notification_cancel`, `scheduling_preferences_set`, `scheduling_preferences_get` | Registered only when `butler_name == "messenger"`, independently of `core_groups`. |

**Name gates.** `switchboard_routing` and `switchboard_backfill` remain inert on
every non-Switchboard daemon even when configured. The one group-local name
gate is `infra`'s `chronicler_day_close_refresh`. Messenger's six tools are
direct name-gated registrations rather than a fifteenth group.

**Type gates.** The complete non-staffer-only set is the five `sessions` tools,
all thirteen `temporal` tools, all four `delegation` tools, all six
`domain_events` tools, `notify`, `schedule_trigger`, and `schedule_costs`.
The remaining group tools have no type gate. `fleet_cases` deliberately remains
available on staffers because Switchboard owns its write path.

**Universal direct registrations.** Every daemon needs `route.execute` for
Switchboard-routed delivery and `cancel_session` for dashboard cancellation.
Both remain on canonical FastMCP `tools/list` but are infrastructure-only in the
RFC 0027 LLM-presentation inventory.

**Implementation.** The daemon reads `core_groups` from the effective
`RuntimeConfig` (resolved from the `runtime_config` DB table via
`RuntimeConfigAccessor`) and passes it to `_register_core_tools()`. A
group-aware decorator `_core_tool(group)` replaces the prior post-registration
prune pass. The former tier catalog and the `_tools_to_remove` pruning section
are removed. Registration correctness is derived from the dispatcher,
decorators, effective gates, and behavior tests rather than from a second
hand-maintained catalog alias.

#### Module Tool Groups

Modules MAY define **tool groups** --- named subsets of the tools they provide.
When a butler enables a module, it MAY specify which groups to register:

```toml
[modules.memory]
groups = ["core", "entity"]
```

Behavior:

- When `groups` is absent or empty, `register_tools()` registers **all** tools
  (backwards compatible).
- When `groups` is present, `register_tools()` registers only tools belonging to
  at least one listed group.
- Each module defines its own group taxonomy and documents it in the module's
  docstring or dedicated docs. Group names are module-scoped; `"core"` in the
  memory module is independent of `"core"` in the calendar module.

The `config_schema` Pydantic model for a module SHOULD include an optional
`groups: list[str] | None` field. The default is `None` (register all).

**Implementation.** `ToolGroupMixin` (`src/butlers/modules/base.py`) is a
Pydantic mixin that provides the `groups: list[str] | None` field. Module config
schemas inherit from it:

```python
class MyModuleConfig(ToolGroupMixin, BaseModel):
    some_setting: str = "default"
```

The companion utility `group_enabled(config, group) -> bool` returns `True` when
`config.groups` is `None` or empty (backwards-compatible all-enabled), or when
`group` appears in the list. Inside `register_tools()`, modules use the
`_tool(group)` decorator pattern:

```python
def _tool(group: str):
    if group_enabled(config, group):
        return mcp.tool()
    return lambda fn: fn   # no-op — function defined but not registered
```

Modules currently implementing tool groups:

| Module | Groups | Location |
|--------|--------|----------|
| memory | 5 (`core`, `entity`, `feedback`, `admin`, `preferences`) | `src/butlers/modules/memory/` |
| calendar | 3 (`core`, `butler_events`, `attendees`) | `src/butlers/modules/calendar.py` |
| relationship | 8 (`contacts`, `entity`, `interactions`, `management`, `notes`, `relationships`, `social`, `tracking`) | `roster/relationship/modules/` |
| finance | 8 (`analytics`, `bills`, `budgets`, `bulk`, `core`, `facts`, `intelligence`, `subscriptions`) | `roster/finance/modules/` |
| education | 7 (`analytics`, `curriculum`, `diagnostics`, `mastery`, `mind_maps`, `spaced_repetition`, `teaching`) | `roster/education/modules/` |
| health | 7 (`conditions`, `measurements`, `medications`, `nutrition`, `reports`, `research`, `symptoms`) | `roster/health/modules/` |
| home_assistant | 3 (`core`, `history`, `maintenance`) | `roster/home/modules/` |
| approvals | 3 (`actions`, `rules`, `promotions`) | `src/butlers/modules/approvals/` |

**Ownership principle.** A specialist butler keeps all groups of its own domain
module enabled (or omits `groups` entirely). Pruning applies to cross-cutting
modules shared across butlers --- e.g., a finance butler enabling the memory
module with only `groups = ["core"]` to avoid registering entity or admin tools
it will never use.

#### Auditing

Daemon startup logging (RFC 0005) SHOULD emit the total registered tool count
per butler. A warning SHOULD fire when the count exceeds 50.

**Codex adapter retry mechanism.** The current Codex adapter enters its MCP
discovery retry path only when MCP servers were configured, no non-command MCP
tool call was parsed, and stderr contains an explicit closed transport,
connection, startup, or protocol-failure marker. Zero MCP calls alone is not a
failure signal. Plain-text and command-only completions without such a marker
are accepted, and an invocation with no MCP servers is never retried for MCP
discovery.

The retry delays are exactly 2 seconds and 5 seconds
(`_MCP_RETRY_DELAYS = (2.0, 5.0)`), for at most three subprocess attempts
including the initial call. The loop stops as soon as an MCP call appears or a
later result no longer matches the closed failure predicate. If all three
attempts retain the predicate, the adapter raises `MCPToolDiscoveryError` with
the retained partial result/call/usage evidence.

The adapter records the following diagnostics in the session `process_log`:

| Key | Type | Meaning |
|-----|------|---------|
| `mcp_connection_failed` | `bool` | `True` after persistent closed-signal discovery failure; the existing no-MCP configuration path also records `True` without retrying. |
| `retry_attempted` | `bool` | `True` when at least one 2/5-second discovery retry ran. |
| `retry_succeeded` | `bool | None` | `True` when a retry produced an MCP call, `False` when all retries retained the failure, and `None` when a retry produced an accepted no-tool result. |
| `attempt_count` | `int` | Total subprocess attempts, including the initial call. |
| `result_source` | `first | retry` | Which attempt supplies the retained result evidence. |

Session monitoring dashboards and alerting rules SHOULD key on
`retry_attempted = True AND retry_succeeded = False` to surface persistent MCP
connectivity issues; `mcp_connection_failed` alone also covers the intentional
no-MCP configuration case and is not proof that a retry ran.

#### Streamable-HTTP Disconnect Log Filter

The streamable-HTTP transport (`mcp.server.streamable_http`, tracked at MCP
`1.26.0`) logs every failure of its internal `standalone_sse_writer` task at
ERROR level with message `"Error in standalone SSE writer"`. When the cause is
a client-initiated SSE disconnect — raised as `anyio.ClosedResourceError` or
`anyio.BrokenResourceError` — the resulting traceback is noise that pollutes
QA error dashboards without indicating a real server fault.

`src/butlers/mcp_patches.py::apply_streamable_http_disconnect_patch` installs
a narrow `logging.Filter` on `mcp.server.streamable_http.logger`. The filter
matches records whose `msg` is exactly the upstream writer-error string AND
whose `exc_info` names one of the two disconnect exception types. Matching
records are rewritten in-place to DEBUG level with `exc_info` cleared; all
other records pass through untouched. This avoids re-vendoring upstream
method bodies, so an MCP version bump cannot silently degrade the handler:
if upstream changes the log message or introduces new disconnect paths, the
filter simply becomes a no-op for those paths. The patch is idempotent and
applied once per process from `ButlerDaemon._build_mcp_http_app`.

## Integration

- **RFC 0001:** Tool registration occurs during daemon startup phases 13-14.
- **RFC 0003:** `route.execute` is a core tool that accepts Switchboard-routed envelopes.
- **RFC 0005:** Core and module wrappers preserve bounded call logging/capture; module spans are wrapper-owned while core span ownership remains with each handler.
- **RFC 0006:** Module migrations are discovered and executed based on `migration_revisions()`.
- **RFC 0011:** The insight broker module on the Switchboard registers `propose_insight_candidate` as a module tool. The `notify` core tool is extended with `intent='insight'` for insight delivery.

## Alternatives Considered

**Direct tool registration without proxy.** Rejected because per-tool instrumentation would require every module to manually add OTel spans and tool call capture, leading to inconsistent observability and duplicated boilerplate.

**Peer-to-peer MCP between butlers.** Rejected in favor of Switchboard-mediated routing. Direct connections would create O(n^2) configuration complexity and eliminate the central audit/routing/identity resolution point.

**Dynamic tool registration after server start.** Rejected because the canonical FastMCP HTTP server finalizes its tool surface before accepting streamable-HTTP or legacy-SSE calls. All tools MUST be registered before the server begins accepting connections.

## Accepted Amendment 1 (2026-08-30) — LLM Presentation and Native Deferred Discovery

**Status:** Owner-approved on 2026-08-30 with RFC 0027; effective in the
canonical contract when the paired OpenSpec/RFC change is merged.

RFC 0027 changes only how registered tools are presented to LLM
runtimes:

- The registered/callable set remains governed by core groups, module groups,
  type/name gates, module state, and startup success.
- A deterministic LLM-presentable projection may omit infrastructure-only
  definitions without removing their canonical handlers from the complete MCP
  endpoint. The omission is not a server authorization boundary.
- The 30-50 target becomes an initially loaded working-set target rather than
  a hard ceiling on registered handlers. Manifesto and group pruning remain
  mandatory because they encode ownership, not just token cost.
- Verified runtime tuples may use native deferred search. An unadmitted native
  tuple uses a separately verified eager-capable profile/candidate when one is
  available; otherwise it is ineligible for tool-bearing work. Every permitted
  mode preserves unchanged typed MCP calls.
- Tool descriptors are finalized only after approval wrapping, and invocation
  always returns to the final wrapped FastMCP registry.
- Skills remain guidance-only and cannot register or present tools.

## Accepted Amendment 2 (2026-08-31) — Adapter-Owned Search Corpus and Complete MCP Listing

**Status:** Owner-selected Option B in `bu-g5fha`; effective in the canonical
contract when the paired RFC/OpenSpec amendment merges.

Amendment 2 supersedes Amendment 1 only on where the searchable LLM corpus is
bounded, how runtime-native search is rendered, and how
opaque runtime-host MCP pagination is treated:

- FastMCP `tools/list` remains the complete registered protocol surface over
  streamable HTTP and SSE for every client.
- The post-approval catalog retains immutable definitions, names, and digests,
  never handler callables.
- Each runtime attempt receives a plan-digest-bound canonical-name search
  corpus; the adapter renders it through supported public host configuration
  and, for a verified native tuple, exposes a small initial summary while the
  host searches and loads full typed definitions on demand.
- The allowlist bounds search eligibility. It is not the source of material
  token savings; eager-only hosts still serialize every allowed definition.
- Runtime-host MCP enumeration and pagination remain internal to that
  invocation and are not represented as Butlers-owned cursors. Conformance must
  prove hidden definitions, schemas, and counts never reach model-visible
  input.
- Model-visible omission is not call-time authorization. Direct calls continue
  through the complete canonical MCP endpoint and final wrapped handler.
- A tuple that cannot prevent its host from independently serializing the
  complete list is ineligible for tool-bearing work. The complete list is not a
  presentation fallback.
- Private FastMCP hooks, monkeypatches, duplicate filtered servers, proxies,
  and JSON-RPC/SSE frame rewriting remain prohibited.

Registration, module groups, approval wrapping, skills, schemas, logging,
attribution, and MCP-only communication are unchanged.
