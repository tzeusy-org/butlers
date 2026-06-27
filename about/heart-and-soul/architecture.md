# Architecture Philosophy

This document explains WHY the system is shaped the way it is. For implementation
details, database schemas, and API contracts, see the [architecture docs](../architecture/index.md).

## The Butler-as-Daemon Model

Each butler runs as a persistent async daemon, not a serverless function, not a
container spun up per request. This is deliberate.

**Why persistent daemons:**

- Butlers maintain cron schedules that must fire regardless of incoming traffic.
  A serverless model requires an external scheduler to invoke the function.
  Daemons own their own clocks.
- Startup is expensive: loading modules, running migration checks, registering
  MCP tools, and establishing database connections. Amortizing this across a
  long-running process is cheaper than paying it per invocation.
- State continuity matters. The daemon holds in-memory caches, connection pools,
  and module state that would be lost between serverless invocations.
- Debugging a persistent process with logs, health checks, and session history
  is simpler than debugging ephemeral invocations that leave no trace.

**The constraint:** The daemon itself must be deterministic. It does not reason,
classify, or decide. It manages lifecycle, enforces schedules, and registers
tools. Intelligence lives exclusively in the ephemeral LLM sessions the daemon
spawns. This separation keeps the daemon testable and predictable.

## Why MCP as the Universal Interface

The Model Context Protocol governs three relationships in Butlers:

1. **LLM-to-butler:** Ephemeral LLM sessions call the butler's MCP tools to
   read state, send messages, and interact with external services.
2. **Butler-to-butler:** The Switchboard dispatches work to domain butlers via
   MCP calls. Domain butlers never call each other directly.
3. **Client-to-butler:** The dashboard, connectors, and any future clients
   interact with butlers through their MCP endpoints.

**Why a single protocol for all three:**

- One serialization format, one tool registration mechanism, one error model.
  Developers learn it once.
- LLM runtimes already speak MCP natively. No translation layer needed between
  the LLM and the butler's capabilities.
- Inter-butler communication uses the same mechanism that LLM sessions use. A
  butler cannot tell whether a tool call came from an LLM session or from the
  Switchboard. This is a feature: it means the butler's tools are the complete
  interface, with no hidden backdoors.

**The constraint:** MCP is the ONLY inter-butler communication channel. Butlers
must not share database connections, import each other's modules, or communicate
through side channels. If two butlers need to coordinate, it flows through the
Switchboard.

**The exception mechanism:** When the MCP-compliant path (Switchboard fan-out)
would require multiple LLM sessions for work that involves zero LLM reasoning
--- purely deterministic SQL queries or data aggregation --- a read-only
cross-schema SQL view may be used instead, subject to RFC-documented guardrails
(RFC 0010). Each exception must be read-only at the database level, batch-
oriented, auditable via migration history, and cost-justified. Write operations
and interactive queries must always go through the Switchboard.

## Why Domain Specialization Over Monolith

A single agent that handles health, finance, relationships, and everything else
will inevitably suffer from:

- **Context pollution:** Health context leaks into finance sessions, consuming
  tokens and confusing the model.
- **Prompt bloat:** The system prompt grows without bound as capabilities are
  added. Every session pays the cost of every domain.
- **Scope creep:** Without clear boundaries, every feature belongs everywhere.
  Quality degrades as the agent becomes a generalist.
- **Personality incoherence:** A health companion and a financial advisor have
  different tones, different risk tolerances, and different definitions of
  "helpful."

Domain butlers solve this by giving each domain its own process, its own prompt,
its own tools, and its own manifesto. The health butler loads health tools and
runs with a health-oriented personality. The finance butler loads finance tools.
Neither pays for the other's context.

**The constraint:** Domain boundaries are enforced at the process level, not by
convention. A butler cannot access another butler's database schema or tools. The
Switchboard is the only bridge.

## The Staffer Archetype: Infrastructure Specialization

Not every long-running agent serves a user life domain. Some serve the ecosystem
itself --- routing messages, delivering notifications, or enforcing cross-cutting
policies. These are **staffers**.

A staffer shares the same runtime engine as a domain butler: the same
`ButlerDaemon` class, module system, scheduler, LLM spawner, and session
logging. The distinction is not in the engine but in the role and the permissions
model. A staffer's `butler.toml` declares `type = "staffer"`, which gates a small
set of type-aware behaviors:

- **Routing exclusion:** Staffers are never candidates for user-message
  classification by the Switchboard. When an incoming message is classified, only
  domain butlers are in the candidate set. Butler-to-staffer routing (e.g.,
  `notify()` routing through Switchboard to Messenger) is unaffected.
- **Briefing exclusion:** Staffers do not contribute to daily briefings. They
  serve the system, not the user's domains, so they have nothing to contribute to
  the user's situational summary.
- **Cross-butler access:** Staffers may declare explicit cross-butler access
  permissions in `butler.toml` under `[butler.permissions]`. This formalizes the
  connectivity that the Switchboard and Messenger already exercise. Domain butlers
  default to no cross-butler access.

Staffers use a `MANIFESTO.md` with infrastructure-contract framing rather than
user-relationship framing. The contract specifies the service's responsibilities,
SLAs, failure modes, dependency graph, and escalation procedures. The same scope
governance applies: a new capability proposed for a staffer must be evaluated
against the contract and may require a formal amendment.

The current staffers are the Switchboard (message routing and ingestion),
Messenger (outbound channel delivery), and QA (system-wide error patrol,
triage, and automated investigation). Future infrastructure agents --- log
aggregation, billing --- follow the same pattern without requiring engine
changes.

**Why a shared engine matters:** A separate `StafferDaemon` class would duplicate
the entire lifecycle management, module system, and tool composition logic. By
expressing the butler/staffer distinction through a single `type` field, the
system remains coherent as the roster grows. Adding a new staffer is identical to
adding a new domain butler: a `roster/{staffer-name}/` directory with `butler.toml`
(`type = "staffer"`), `MANIFESTO.md`, `CLAUDE.md`, and `AGENTS.md`.

## Why Modules as the Extension Mechanism

Modules are the only way to add capabilities to a butler. A module implements
the `Module` abstract base class and provides:

- `register_tools()` --- adds MCP tools to the butler's server
- `migration_revisions()` --- declares the Alembic branch label for module-specific
  migrations (or returns None when the module owns no tables)
- `on_startup()` / `on_shutdown()` --- lifecycle hooks

**Why this constraint matters:**

- It prevents capability sprawl. If a capability is not a module, it does not
  exist. There is no "just add a function to the butler" escape hatch.
- It enforces isolation. Each module owns its own tables. Module A cannot
  modify Module B's schema.
- It enables composition. A butler opts into exactly the modules it needs via
  `butler.toml`. The general butler has collections and calendar. The health
  butler has measurements, medications, and nutrition. Neither carries the
  other's weight.
- It makes dependency resolution explicit. Modules declare dependencies on
  other modules, resolved via topological sort at startup.

**The constraint:** Modules only add tools. They must never modify core
infrastructure --- the state store, the scheduler, the spawner, or the session
log. If a capability requires changes to core, it belongs in core.

Some modules serve coordination roles on the Switchboard rather than domain
roles on specialist butlers. The insight broker module (RFC 0011), for example,
runs within the Switchboard daemon and provides candidate submission, delivery
brokering, and anti-spam enforcement as MCP tools. It follows the same Module
ABC contract --- `register_tools()`, `migrations()`, lifecycle hooks --- but
its scope is cross-butler coordination, not domain specialization.

## Why Tool Surface Discipline Matters

Every tool registered on a butler's MCP server costs tokens when the LLM
discovers available tools at session start. At 90-157 tools, this overhead is
substantial: it consumes context window, increases latency, and measurably
degrades tool selection accuracy --- especially on smaller or cheaper models.

The target is 30-50 tools per butler. This is not arbitrary. It is the range
where LLM tool selection remains reliable across model tiers without burning a
significant fraction of the context window on tool definitions alone.

**How to stay within budget:**

- **Core tools are not unconditional.** The daemon registers core tools based on
  butler type and name. Session analytics tools belong on the dashboard butler,
  not on every butler. Ingest tools belong on the Switchboard, not on domain
  butlers. The pattern already exists for `ingest` and messenger tools; it should
  be the default, not the exception.
- **Modules expose tool groups, not monoliths.** A module with 15 tools should
  define logical groups (e.g., "core", "entity", "admin") so butlers can import
  the subset they need. When no groups are specified, all tools register for
  backwards compatibility.
- **Manifesto alignment is a filter.** If a tool does not serve the butler's
  manifesto, it should not be registered --- even if the module that provides it
  is enabled. Tool groups make this granular.

**The two-layer gating model:**

Tool registration is gated at two independent layers, each with its own
mechanism. Both must pass for a tool to appear on a butler's MCP surface.

1. **Core daemon tools** are gated by `butler_type` (STAFFER vs BUTLER) and
   `butler_name` (switchboard, messenger). Deadline, event-chain, and
   seasonal-period tools register only for domain butlers, not staffers. Ingest
   pipeline tools register only for the Switchboard. Notify delivery tools
   register only for the Messenger. Mechanically, `_register_core_tools()` is a
   thin dispatcher: it builds a `ToolContext` (carrying `butler_type`,
   `is_switchboard`, `is_messenger`) plus a group-aware `_core_tool(group)`
   factory, then delegates to `register_all_core_tools()` in
   `butlers.core_tools`. Each domain register function applies the type/name
   guards (e.g. `if butler_type != ButlerType.STAFFER: return` for the temporal
   group, `if not ctx.is_switchboard: return` for ingest). These guards are
   evaluated once at startup. Core tools additionally support a declarative
   `core_groups` layer: when `core_groups` is set on the DB-backed runtime
   config, only tools in the listed groups register, mirroring the module
   `groups` mechanism in layer 2 below. When `core_groups` is unset, all core
   groups register (backward compatible).

2. **Module tools** are filtered declaratively via `groups` config in
   `butler.toml`. Each module defines named tool groups (e.g., "measurements",
   "conditions", "reports" in the health module; "core", "climate", "scenes" in
   home assistant). A butler enables only the groups it needs. The gating is
   resolved at tool registration time through a `_tool(group)` helper inside each
   module's `register_tools()` function: when the group is enabled, `_tool()`
   returns `@mcp.tool()`; when disabled, it returns a no-op passthrough that
   defines the function but never registers it. This means zero re-indentation of
   existing tool functions --- the decorator swap is the only change.
   `ToolGroupMixin` adds an optional `groups` field to module configs, and
   `group_enabled()` resolves whether a group should register.

**The backwards-compatibility contract:** Omitting `groups` from a module's
config in `butler.toml` registers ALL of that module's tools. This preserves
existing behavior for every butler that has not yet opted into group filtering.
Group support can be adopted incrementally, one module at a time, without
touching butlers that do not need it.

**The ownership principle:** Domain modules used by their own specialist butler
--- health tools on the health butler, finance tools on the finance butler ---
keep all groups enabled. There is no reason to prune a domain module on its home
butler. Cross-cutting modules are where pruning matters: memory, calendar,
approvals, and similar modules that appear on multiple butlers. Each butler
enables only the groups relevant to its role, and the rest stay silent.

**The constraint:** Adding a tool to a butler's surface is not free. Every
registration must be justified by the butler's role. The question is not "could
this butler use this tool?" but "does this butler need this tool in most
sessions?"

## Why Connectors Are Separate from Butlers

Connectors are standalone processes that bridge external transport systems
(Telegram, Gmail, Discord) to the Butlers ingestion pipeline. They are not
modules. They do not run inside butler daemons.

**Why the separation:**

- **Transport diversity:** Telegram uses long-polling. Gmail uses push
  notifications or periodic IMAP checks. Discord uses websockets. Each transport
  has its own connection model, authentication, rate limits, and failure modes.
  Mixing these into butler daemons would couple domain logic to transport
  mechanics.
- **Independent lifecycle:** A Telegram connector can crash and restart without
  affecting any butler. A butler can restart without dropping Telegram
  connections.
- **Single responsibility:** Connectors read events from an external system,
  normalize them into a canonical envelope, apply structural cost gates (see
  below), and submit them to the Switchboard. They do not classify content,
  route to butlers, or take domain actions.
- **Checkpointing:** Connectors manage their own cursors (last-read message
  ID, IMAP UID, etc.) and handle crash recovery independently.

**The constraint:** Butlers must never contain transport-specific code. If a
butler knows how to poll Telegram or parse a Gmail push notification, the
separation has been violated.

## Why Connectors Are the Computational Cost Boundary

Connectors are the cheapest place to prevent cost explosions. Every event that
passes a connector enters the Switchboard pipeline --- ingestion, deduplication,
potential LLM classification, storage, and downstream signal extraction. Each
stage has cost: database writes, embedding generation, LLM tokens, and fact
creation fan-out. Once an event enters the pipeline, it is expensive to stop.

**The principle:** Connectors MUST apply computational cost gates before
submitting envelopes to the Switchboard. If a connector can determine that an
event will produce no useful downstream value, it must not submit that event.
This is not classification or routing --- connectors still do not decide which
butler handles a message. It is volume gating: preventing events that would
generate unbounded work from entering the pipeline at all.

**The canonical example:** Group chat interactions. A message in a 500-person
Telegram community channel would, without gating, create identity resolution
attempts for every sender, interaction facts for every resolved contact,
embedding generation for every fact, and Dunbar score inflation for hundreds
of peripheral contacts. The connector has access to the chat metadata
(participant count, chat type) that downstream components do not. It is the
only component that can cheaply distinguish a 3-person family group from a
500-person community channel.

**What connectors MUST gate:**

- **Participant count:** Chat connectors (Telegram, WhatsApp) MUST include
  participant count metadata in the envelope. Events from chats exceeding the
  configured threshold (default: 20 participants) MUST be excluded from
  interaction-relevant submission or downgraded to metadata-only tier.
- **Chat type:** Connectors SHOULD distinguish DMs, small groups, supergroups,
  and broadcast channels. This metadata enables downstream components to apply
  appropriate weighting without repeating the transport-specific API calls.

**What connectors MUST NOT gate:** Content-based filtering, semantic
classification, or routing decisions. Those remain the Switchboard's
responsibility. The connector gates on structural metadata (participant count,
chat type) that is available from the transport API without reading message
content.

**Why not gate at the Switchboard instead?** The Switchboard receives normalized
envelopes. By the time it sees a message, the transport-specific metadata
(Telethon `chat.participants_count`, whatsmeow group membership) is lost unless
the connector explicitly included it. Asking the Switchboard to query Telegram
for participant counts would violate the transport abstraction. The connector
already has the transport client; the cost of checking participant count is
negligible compared to the cost of processing the event through the full
pipeline.

## Why Single PostgreSQL with Schema Isolation

All butlers share a single PostgreSQL database. Each butler gets its own schema.
The `public` schema holds cross-butler identity tables (contacts, contact info)
and shared coordination tables (situational context signals, insight candidates,
insight delivery settings).

**Why a single database:**

- Operational simplicity. One connection string, one backup target, one
  monitoring endpoint. For a single-user system, running nine PostgreSQL
  instances would be absurd.
- Cross-butler queries are possible when genuinely needed (identity resolution,
  dashboard aggregation) without distributed transactions.
- Schema isolation provides logical separation without physical overhead.

**Why per-butler schemas (not per-butler databases or shared tables):**

- A butler cannot accidentally read or write another butler's data through
  normal operations. The schema boundary is the guardrail.
- Migrations are scoped to the butler that owns the schema. Adding a table to
  the health butler does not touch the finance butler's schema.
- The `public` schema is the explicit, controlled surface for cross-butler
  data. If it is not in `public`, it is private. Shared tables include
  identity data (contacts, contact info), situational context signals
  (RFC 0009), and insight delivery infrastructure (RFC 0011).

## The Core Loop

Every butler operation follows the same cycle:

```
trigger --> classify --> route --> spawn --> act --> log
```

1. **Trigger:** An event arrives --- external MCP call, cron tick, or connector
   submission.
2. **Classify:** The Switchboard determines which domain(s) the event belongs to
   (for external messages) or the scheduler determines which prompt to run (for
   cron triggers).
3. **Route:** The classified event is dispatched to the appropriate butler(s)
   via MCP.
4. **Spawn:** The receiving butler generates a locked-down MCP config and spawns
   an ephemeral LLM CLI session.
5. **Act:** The LLM session reasons, calls tools, reads and writes state, and
   produces output. Before acting, the session may check shared situational
   context (RFC 0009) to adapt its behavior to the user's current state.
6. **Log:** The butler records the session: trigger source, tools called, tokens
   consumed, duration, and outcome.

This cycle is the heartbeat of the system. Every feature, every module, every
connector ultimately feeds into or consumes from this loop. Changes that break
the loop's simplicity or add conditional branches to it require exceptional
justification.

Two cross-cutting pipelines augment the core loop without modifying it:

- **Situational context** (RFC 0009): A pull-based shared awareness layer
  (`public.user_context`) where butlers write TTL-bounded signals about the
  user's state and read them before acting. Context checking is opt-in --- it
  does not change the core loop, but enriches step 5 for butlers that use it.
- **Proactive insight delivery** (RFC 0011): A three-phase pipeline where
  butlers propose insight candidates via the Switchboard, a broker module
  deduplicates and budget-gates them, and winners are delivered as a digest.
  This pipeline runs on its own schedule alongside the core loop.

## Anti-Patterns

- Running multiple PostgreSQL instances for isolation that schema separation
  already provides.
- Adding "smart" logic to the daemon that should live in LLM sessions.
- Creating modules that modify core infrastructure instead of extending it.
- Building connectors that classify or route messages instead of just
  transporting them.
- Allowing butlers to import each other's code or share database connections.
- Adding a new protocol alongside MCP for "special" communication needs.
- Making the core loop conditional on module presence.
- Registering all module tools on every butler instead of gating by role and
  group.
- Adding cross-schema access without an RFC, explicit guardrails, and reuse
  criteria.
