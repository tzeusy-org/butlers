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

## Why Modules as the Extension Mechanism

Modules are the only way to add capabilities to a butler. A module implements
the `Module` abstract base class and provides:

- `register_tools()` --- adds MCP tools to the butler's server
- `migrations()` --- declares database migrations for module-specific tables
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
- **Single responsibility:** Connectors do one thing: read events from an
  external system, normalize them into a canonical envelope, and submit them to
  the Switchboard. They do not classify, route, or act.
- **Checkpointing:** Connectors manage their own cursors (last-read message
  ID, IMAP UID, etc.) and handle crash recovery independently.

**The constraint:** Butlers must never contain transport-specific code. If a
butler knows how to poll Telegram or parse a Gmail push notification, the
separation has been violated.

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
- Adding cross-schema access without an RFC, explicit guardrails, and reuse
  criteria.
