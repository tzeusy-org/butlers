# MCP Model

> **Purpose:** Explain how each butler exposes tools via MCP and how ephemeral LLM sessions interact with them.
> **Audience:** Developers building modules, extending core tools, or understanding the runtime tool surface.
> **Prerequisites:** [Butler Lifecycle](butler-lifecycle.md), [Trigger Flow](trigger-flow.md).

## Overview

Every butler is a long-running FastMCP server. Its tool surface is assembled from two layers: core tools (always present) and module tools (opt-in per butler). When a trigger fires, the spawner creates an ephemeral LLM CLI session that connects exclusively to its own butler's MCP endpoint. The LLM can only call tools registered on that butler --- it cannot reach other butlers directly.

## FastMCP Server

At startup, the butler daemon creates a `FastMCP` instance and binds it to an SSE (Server-Sent Events) HTTP server on the butler's configured port. The server remains running for the lifetime of the daemon process. All tool registrations happen during daemon initialization, before the server begins accepting connections.

The startup sequence for tool registration is:

1. Create the `FastMCP` instance with the butler's name.
2. Register core tools (status, trigger, route.execute, and others).
3. Resolve module dependency order via topological sort.
4. Call `register_tools(mcp, config, db)` on each enabled module in dependency order.
5. Apply approval wrappers to configured tools.
6. Finalize an immutable value catalog from the resulting public FastMCP definitions.
7. Start the SSE server.

## Core Tools

Every butler registers these core tools regardless of its module configuration:

- **`status()`** --- Returns butler identity, loaded modules, health, and uptime. This is the primary health-check endpoint used by the dashboard and monitoring.
- **`trigger(prompt, context?)`** --- Spawns a new LLM session with the given prompt. This is how external MCP clients (or other butlers via the Switchboard) invoke a butler.
- **`route.execute(...)`** --- Accepts routed requests from the Switchboard. Handles the full route envelope (schema version, request context, input, subrequest, source metadata, trace context) and dispatches to the spawner or the durable route inbox.

Core tools are wrapped with OpenTelemetry spans (`butler.tool.<name>`) and tool-call logging for session attribution.

## Module Tools

Modules add domain-specific tools by implementing the `Module` abstract base class from `src/butlers/modules/base.py`. The key method is `register_tools(mcp, config, db)`, where the module calls `mcp.tool()` to register its handlers. Examples of module tools include email send/search/read, Telegram messaging, calendar event management, memory store/search, and contact lookup/update.

Modules declare their dependencies via the `dependencies` property. The daemon resolves these using topological sort so that dependent modules are initialized after their prerequisites. A module only adds tools --- it never touches core infrastructure (scheduler, spawner, session log).

## Tool Call Logging Proxy

Module tool registrations pass through a `_ToolCallLoggingMCP` proxy rather than the raw FastMCP instance. This proxy intercepts every `mcp.tool()` call and wraps the handler with:

1. **OpenTelemetry span creation** --- a `butler.tool.<name>` span with `butler.name` attribute.
2. **Tool call capture** --- records the tool name, module name, input payload, outcome, and result in the session's tool call buffer. This is how the session log gets ground-truth tool execution data.
3. **Error handling** --- catches and logs exceptions from tool handlers without crashing the MCP server.

## Skills Infrastructure

Each butler can have a skills directory at `roster/<butler>/.agents/skills/`. Skills are directories containing a `SKILL.md` file that describes a capability the LLM can use. The skills infrastructure (`src/butlers/core/skills.py`) provides:

- **`read_system_prompt(config_dir, butler_name)`** --- Reads `CLAUDE.md` from the butler's config directory, resolves `<!-- @include path.md -->` directives relative to the roster directory, and appends shared snippets (`BUTLER_SKILLS.md`, `MCP_LOGGING.md`).
- **`get_skills_dir(config_dir)`** --- Returns the path to `.agents/skills/` if it exists.
- **`list_valid_skills(skills_dir)`** --- Lists skill directories with valid kebab-case names, warning and skipping invalid ones.
- **`resolve_skill_identity(config_dir, skill_name)`** --- Resolves a skill to its canonical
  `.agents/skills/<name>/SKILL.md` identity and content digest. A `.claude/skills` compatibility
  alias is recorded only when it resolves to that same physical source; missing or unreadable
  canonical sources fail explicitly.
- **`read_agents_md` / `write_agents_md` / `append_agents_md`** --- Read/write access to `AGENTS.md`, the runtime agent notes file that LLM sessions can use for persistent inter-session memory.

Skill names must follow kebab-case: start with a lowercase letter, allow lowercase letters, digits, and hyphens between segments. The pattern is `^[a-z][a-z0-9]*(-[a-z0-9]+)*$`.

## Ephemeral MCP Config

When the spawner invokes an LLM session, it generates a temporary MCP configuration that points exclusively at the butler's own MCP endpoint. The config includes the butler's MCP URL (SSE endpoint) with a `runtime_session_id` query parameter for tool call attribution, and nothing else. The LLM is sandboxed to its own butler's tools, maintaining the architectural boundary where inter-butler communication flows exclusively through the Switchboard.

## Tool Sensitivity Metadata

The `ToolMeta` dataclass allows modules to declare per-argument sensitivity information via `arg_sensitivities`. This is used by the approvals module to determine which tool calls require human approval before execution. Arguments not explicitly listed fall back to a heuristic-based sensitivity classifier.

## Registered Definition Catalog

The daemon owns one immutable catalog per startup generation. It is finalized after approval
wrapping, so its model-visible input schemas and descriptions describe the same final definitions
served by FastMCP. Catalog entries record canonical name, module, group, namespace,
LLM-presentability, eager/deferred posture, sensitivity declarations, immutable definition values,
and stable digests. The checked-in inventory is validated against the executable core,
built-in module, and roster-module provider union. Configuration-specific module tools may also
supply complete declarations through the existing `ToolMeta` channel. The canonical catalog
combines those declarations for the actual registered surface; executable gates cover both the
provider union and configured roster variants, including conditional Calendar responses.

An unclassified legacy tool remains presentable and eager for compatibility, while the catalog
marks classification incomplete. Classification never adds a handler or grants call authority.
Infrastructure-only entries remain in canonical FastMCP `tools/list` and remain callable by their
existing authenticated callers even though their metadata marks them unsuitable for future LLM
presentation.

The adapter work described by RFC 0027 is not implemented in this slice. No runtime host consumes
the catalog yet, and there is no host allowlist, search corpus, deferred schema loading, provider
admission, or native Tool Search activation. Spawned sessions therefore retain the existing
canonical MCP behavior until that separately reviewed adapter work lands.

## Verification

To confirm the MCP model described here matches the running system:

```bash
# 1. Butler's SSE endpoint accepts connections on the expected port
curl -s --max-time 2 -N http://localhost:41101/sse 2>&1 | head -3
# Expected: "data: ..." SSE stream, not ECONNREFUSED

# 2. Core tools are registered and respond
# Call the status tool via an MCP client, or inspect the dashboard:
curl -s http://localhost:41200/api/butlers/general/status | python3 -m json.tool
# Expected: "name", "modules", "uptime", "health" fields present

# 3. Module tools appear in tool listings
# Use an MCP client to list tools on the general butler's MCP server.
# Expected: core tools (status, trigger, route.execute) plus module tools
# (e.g., memory_store, memory_search if memory module is loaded)

# 4. Tool call logging proxy captured calls in session records
# After triggering a session, inspect tool_calls in the session record:
curl -s http://localhost:41200/api/butlers/general/sessions | python3 -m json.tool
# Expected: tool_calls array with tool_name, module_name, outcome, duration_ms fields

# 5. Ephemeral MCP config points only at this butler
# Check that a spawned LLM session cannot call tools on another butler.
# In session output, any attempt to reach another MCP server should fail
# (the ephemeral config has no other server entries).
```

## Implementation Notes

- RFC 0002 Amendment 1 and RFC 0027 separate the full registered MCP catalog from the initially
  loaded working set: the 30-50 tool target constrains initial presentation, not registered
  handlers. Reconcile older roster tool ceilings against that before deleting tools or raising
  limits.

## Related Pages

- [Trigger Flow](trigger-flow.md) --- how triggers create sessions that connect to the MCP server
- [Modules and Connectors](modules-and-connectors.md) --- the module lifecycle and dependency resolution
- [Tool Call Capture](../runtime/tool-call-capture.md) --- how tool executions are recorded for session logs
- [LLM CLI Spawner](../runtime/spawner.md) --- how ephemeral MCP configs are generated

## Private fact report admission

The source daemon freezes canonical accepted-row attribution before classification or routing.
A constructor-owned source registry selects the fixed Relationship resolver and locks the
public entity lifetime witness. A registered invocation supplies `X-Butlers-Fact-Invocation`;
a routed hop supplies `X-Butlers-Fact-Source`, verified online at the configured source daemon.
These ephemeral values bind the target, process incarnation, invocation and expiry. They never
appear in tool arguments, prompt context, URL query parameters or durable receipts. A runtime
session locator, copied entity UUID or model-supplied `verified` flag cannot mint admission.
All supported MCP runtime adapters carry the private headers. Guards clear private context on
completion, failure and cancellation; accepted-row recovery preserves the original report and
requires a current receiver processing claim. Restored-history admission is not established
by these ephemeral registries.

Relationship's public fact tool has no authority, author, `src` or `verified` selector. Internal
no-request writers preserve their existing SYSTEM policy. Actual owner HTTP requests use the
adopted OwnerAuth request-admission point: a later revocation may follow an already admitted
operation. This is not custody currentness or cross-connection commit atomicity. Approval
execution first binds the original stored tool/argument digest and executor task, then privately
normalizes obsolete keywords; fact commit and terminal acknowledgment remain separate outcomes.


## Authorization declarations (RFC 0038 D4)

The existing immutable tool catalog carries authorization metadata for future
broker evaluation. Current registrations, canonical `tools/list`, handler calls,
approval wrappers and argument sensitivities keep their existing behavior. The
metadata is dark: no adapter, broker or domain enforcement consumes it yet.

Declare the provider union through `_declare` in
`src/butlers/core/tool_presentation_inventory.py`, and conditional module tools through
complete `ToolMeta` declarations. The final catalog binds
`owner_domain` to the registering daemon's configuration and freezes that
roster's existing parsed approval policy after wrapping. Module `ToolMeta`
continues to supply explicit argument sensitivity booleans; an explicit false
still overrides the approval heuristic. A supplied authorization value that
conflicts with the inventory is refused rather than silently replaced.

| Field | Declaration |
| --- | --- |
| `owner_domain` | Actual daemon owning the handler and data; never a model argument or module label. |
| `effect_class` | `read`, `record`, `communicate`, `transact`, `actuate` or `administer`. |
| `output_trust` | `trusted`, `untrusted` or `stamped`; all current declarations conservatively use `untrusted`. |
| `output_sensitivity` | Whole-payload `ordinary` or `sensitive`; current declarations use `sensitive`. |
| `resource_params` | Visible argument names selecting resources, excluding message content and display attributes. |
| `selection_bound` | One explicit true or false per `resource_params` name. True only where a spec requires the scope be exactly what the owner picked (REQ-capability-authorization-018): such a capability is neither listed nor callable without an owner-surface selection record. Every current param is false; a missing, extra or non-boolean entry fails the catalog check. Declarative only: no runtime path reads it yet. |
| `safety_args` | Existing sensitive visible arguments an approval must pin. |
| `approval_policy` | Immutable snapshot of the existing actual configured gate, including disabled/absent state. |
| `idempotency` | `none` or `key_required`; Calendar responses require their native request/command identity. |
| `owner_authority` | Whether a write steers future sessions, such as an owner profile, preference or recovery policy. |
| `recipient_params` | Frozen named selectors, unresolved inherited parties and the actual conditional owner default. |

Recipient selectors and resources can overlap. Calendar attendees identify
parties; `calendar_id` selects a calendar. Email `to` and `thread_id` select
destinations; subject and body are content. HA resources are selected by
`target`; `target` and `data` are safety-critical recipient-bearing arguments.
Domain and service identify the existing service operation. All arguments
outside resource and recipient selectors remain content-bearing by default;
an fnmatch pattern on a content argument counts as free text. An inherited
thread, shared folder or implicit service target remains undeterminable even
when a named selector resolves to the owner.

The recipient carrier's condition text describes a native branch, not an
executable permission. Future broker evaluation must resolve each named party
uniquely to the live, unmerged, undeleted owner entity, and independently resolve
inherited parties. It may use the owner default only for the actual absent-party
Telegram send/insight branch of `notify`; reminder scheduling forwards context
and generates a send call, while reply/react and email have no blanket owner
fallback. Unknown or inherited parties cannot be treated as owner communication.

Drive currently registers seven tools and has no share handler. Existing file,
folder and move writes can inherit shared-parent members, so their metadata is
`communicate` with undeterminable recipients. Owner-decision tools and the
private-custody namespace are never eligible for LLM presentation. `transact` is
never grantable, including unexposed or park-only goals. Eligibility is an
unconsumed value property, not authority to execute a call.

`read_stores`, `write_stores` and the same inventory's `STORE_WRITERS` retain
conservative native provenance. The behavior gate rejects a trusted read over a
model-record, native, external or unknown writer, and checks that registered
record writers declare their stores. Dynamic SQL, views, stored-function
boundaries and missing lexical writer edges never establish trust. Current
unknown provenance stays untrusted; declaring `stamped` needs a real item-stamp
producer in a later implementation.

Run the catalog gate for every actual roster and the existing synthetic
provider/group/Boolean variants after approval wrapping. Keep every old
registration and case, including response-disabled Calendar, and exercise
response-enabled Calendar with its real owning gate and executor. Calendar's
`_command_id`/`_command_digest` are private canonical bindings excluded from the
model schema; `_write` is a sensitivity marker, not a visible parameter. Schema
and description digests retain their definitions, while the generation digest
also reflects authorization and policy identity. Repeated unchanged builds
must agree and later mutations must not alter an already frozen generation.

## Final-registry fleet publication (RFC 0038 D4)

`core_275` adds the private, default-OFF definition repository. It stores only
immutable tool descriptors, definition/authorization digests and boot/generation
identities, never handlers, call arguments, credentials or model authority. This
is shared public control-plane metadata, not direct access to a butler's private
schema. Canonical FastMCP registration and `tools/list` remain complete.

The daemon's final-startup hook publishes its actual post-approval `ToolCatalog`.
Publication is authenticated by the acquired connection's non-owner,
non-superuser, non-BYPASSRLS `butler_<domain>_rw` role. There is no caller-verified
flag or model-facing publication tool. Enabled RLS restricts non-owner writes to
that role's domain even after bootstrap regrants public DML. The existing database
owner stably owns all five catalog tables, independent of the migration executor.
RLS is non-FORCE so ordinary full `pg_dump` and restore retain every catalog row;
no backup exclusion, security-definer or BYPASSRLS role is added. Runtime roles
may neither own nor inherit/assume that trusted maintenance owner, and may not
be superusers or bypass RLS. Publication checks the actual five table identities,
stable database ownership, enabled/non-FORCE flags and absence of runtime membership
in that owner; a missing table or changed posture refuses before publication. Immutable triggers remain mandatory. History is append-only;
current registrations advance monotonically and cannot resurrect a retired or
expired lease. A changed finalization republishes the real replacement.

The governed fleet is chronicler, concierge, education, finance, general, health,
home, lifestyle, messenger, qa, relationship, switchboard and travel. Calendar
is explicitly excluded because it is a module, not a standalone roster daemon.
An absent/extra member, missing publication or failed module is not a healthy
empty fleet. Membership/exclusion changes require a corresponding explicit
migration and reader contract change. Runtime publishers cannot edit membership
or activation. The singleton control starts disabled; this change does not
provision or activate a runtime profile.

Each publisher has a 90-second server-clock lease renewed every 30 seconds.
Freshness is a bounded lease, not an instantaneous process-liveness claim. A
failed renewal stops renewal; retirement failure leaves the reader to refuse
when the existing lease expires. Shutdown retires only that boot/revision under
one retained five-second cleanup bound. A stale process cannot renew or retire a
replacement. A lost commit reply is unavailable until exact instance/revision
readback; it is not permission for a blind second publication. Old immutable
history remains historical after replacement, restart or retirement. An identical
complete restart retains the content digest and changes the generation identity.

`PublishedCatalogProvider` is the concrete default of `GoalAdmission`. Its
same-connection read requires the existing Switchboard transaction and verified
role. One SQL/MVCC snapshot reads activation, exact membership and all current
registrations, then reconstructs every immutable descriptor and recomputes
schema, description, authorization and fleet digests. No network or second pool
runs under the admission goal locks. There is no local-catalog, stale-cache or
empty-fleet fallback. The reduced admission projection uses exact
`<domain>__<canonical-name>` IDs and never converts conditional recipient prose
into executable default-owner authority.

The M2 broker adapter consumes `snapshot()`/`require_registration()` from this
same reader: retain full immutable D4 fields, the shared fleet content digest,
registration UUIDs and fleet generation digest; revalidate current registration
before listing/calling. The reader does not perform grant/resource/recipient,
approval, exposure, selection or per-call enforcement. Actual broker integration
remains its own required delivery, without making M1 depend on M2.

Migration rollback refuses to discard any publication history. With empty
history, shared tables remain until the last recorded schema installation leaves
275; installation tracking follows actual upgrades, not numeric revision order.
Role/bootstrap/race/rollback/committed-readback and real admission integration
require the named PostgreSQL contracts. Software doubles and collection do not
prove those outcomes or activation readiness.

A current catalog publisher replaces only its own expected current revision.
A new boot may acquire the domain pointer once; an instance already present in
immutable publication history cannot restart that acquisition after being
superseded. An unconfirmed publication blocks another publish until the exact
attempted revision is read back as current. Retirement and expiry do not permit
an established instance to resurrect itself; restart uses a new boot identity.

Catalog retirement keeps its original five-second cleanup deadline. The daemon
retains its shutdown task across repeated cancellation, attempts the existing
MCP/socket/scheduler/module/database cleanup after retirement failure, and then
propagates the retirement error or caller cancellation. A failed retirement
retains its owner slot; continuing resource cleanup does not claim retirement.


## Pre-switch notification urgency

The current registered `notify` schema has no `priority` argument. Legacy
Python callers may still supply its old label for compatibility, but it cannot
confer an exception. The adopted [D7 notification contract](../../openspec/changes/generalist-lead-focused-delegation/specs/core-notify/spec.md)
requires owner-started and third-party model work to remain routine under
quiet/sleep holds. Explicitly naming the owner's address or entity does not
escape that gate. Recipient resolution, non-owner approval, dossiers and
reply/react targeting retain their separate checks.

The shared current evaluator admits only a verified current owner clause or
an unchanged definition stamped by genuine owner opt-in at creation. The Home
safety sentinel has a separate code-only deterministic producer after its
source/watch/debounce checks; there is no tool argument selecting that producer.
Accepted source identity and claimed scheduler occurrences travel in private
invocation context, not message text, caller metadata or a diagnostic session
locator. Broker candidate scores remain ranking inputs. `urgent_only`, a daily
fallback deadline, unavailable storage and retries cannot manufacture a cause.
Each selected candidate is checked again immediately before dispatch; mixed
routine work cannot ride an authorized candidate's exception. This read cannot
recall an already started external effect or prove future broker activation.

Cause receipts contain a fixed classification plus an opaque reference; they
never copy message content, bearer material or a sender address. A missing,
expired, revoked, edited or unreadable cause remains routine. This is the
pre-switch boundary; RFC0038 first-switch delivery remains separate work.

## Informational confirm admission

`notify(intent="confirm")` accepts 2-4 unique `{value,label}` options, an expiry
of 30-86400 seconds (default 600), and an optional `on_timeout_value` matching an
offered value. The answer is information and grants no approval authority.
The Telegram path returns `confirm_runtime_unavailable` unless trusted host
composition has explicitly installed its authenticated producer. A disabled
path creates neither a prompt nor an approval presentation.

An installed path retains notify permission, recipient posture, identifier and
non-owner approval checks, including the complete options in any parked call.
It submits only normalized content and diagnostic runtime-session correlation
to the producer. The owning source must reload approved effect and recipient
authority; caller content, request context and a session locator cannot supply
goal or sink authority. Messenger applies attention policy when delivering the
stored prompt. Prompt acceptance returns `status="ok"` and `confirm_id`; it does
not claim an answer or domain application. An unknown acceptance response is
`confirm_acceptance_unconfirmed` and must reconcile the same source-owned key.

Email confirms render the question and numbered option labels as an ordinary
send through the existing recipient, approval and attention checks. They have
no callback capture, pending-confirm row or resolvable `confirm_id`.
