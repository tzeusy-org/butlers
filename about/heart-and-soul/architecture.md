# Architecture Philosophy

> **Adopted target doctrine (2026-10-14).** The owner adopted the generalist-lead
> direction and depth-one focused delegation. Existing runtime contracts remain
> binding until explicitly superseded through a verified cutover. See the [amendment](../legends-and-lore/decisions/2026-10-14-doctrine-focused-delegation.md).

This document explains WHY the system is shaped the way it is. For implementation
details, database schemas, and API contracts, see the [architecture docs](../../docs/architecture/index.md).

## Current Implementation and Transition

The generalist-lead model in this document is adopted target doctrine. Existing daemon,
registration, and routing contracts below describe the current execution
baseline and remain binding during transition. Adoption of a direction alone
must not widen any running session's tools, database access, or spawn authority.
The [amendment](../legends-and-lore/decisions/2026-10-14-doctrine-focused-delegation.md)
records the affected contracts and pending specification work.

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

**The boundary:** MCP remains the cross-domain capability interface, brokered
through Switchboard. Domain services do not share database connections or import
each other's modules. The target lead may call authorized domain capabilities
through this boundary without triggering a specialist LLM. Existing routes do
not acquire that new authority until the successor contract is implemented.

**The exception mechanism:** When the MCP-compliant path (Switchboard fan-out)
would require multiple LLM sessions for work that involves zero LLM reasoning
--- purely deterministic SQL queries or data aggregation --- a read-only
cross-schema SQL view may be used instead, subject to RFC-documented guardrails
(RFC 0010). Each exception must be read-only at the database level, batch-
oriented, auditable via migration history, and cost-justified. Write operations
and interactive queries must always go through the Switchboard. The two named
on-demand carve-outs in vision.md (the QA scheduler's policy projection and the
Concierge's system-plane views, RFC 0030) satisfy this through Switchboard-routed
tool calls or a fixed no-argument projection over fixed views, not ad hoc reads.

## Domain Responsibilities and Generalist Reasoning

Domain ownership is durable; reasoning allocation is replaceable. Keep domain
manifestos, data custody, capability contracts, and proactive obligations while
allowing a lead to reason across them. Do not require another reasoning session
for a deterministic lookup or action that the lead is authorized to request.

MCP tool search and deferred loading reduce initial prompt cost. They do not
retrieve all relevant personal facts, teach domain procedures, or authorize
calls. A lead therefore retrieves capabilities, domain guidance, and relevant
memory separately, preserving provenance and explicit unknowns. The planner
must be able to discover related domains instead of treating its first search
results as the complete solution space.

A focused child is useful when it provides depth, parallel exploration,
context isolation, distinct model competence, or independent verification.
A dedicated prompt defines its question, success conditions, supplied evidence,
allowed scope, and return contract. It need not correspond to exactly one
roster entry: a focus can be a bounded cross-domain subproblem. Retrieval and
permissions must support the work actually assigned.

### One lead, focused children, depth one

The lead is depth 0. It may create zero or more depth-1 children, within a
finite per-goal budget and configured concurrency limit. Children may call
permitted deterministic tools and return evidence, proposals, or action
receipts. They cannot create reasoning sessions, delegate through Switchboard,
ask another child to act, or disguise delegation as a new independent goal.
A child reports the need for further work to the lead, which may dispatch a
sibling under the same goal and remaining budget. Root retries and resumptions
retain lineage, authority, cancellation, and budget accounting.

This bounds hierarchy depth, not the number of rounds. The runtime also bounds
total work so a sequence of siblings cannot become unbounded recursion.
The lead may resume or follow up with a child within those bounds.
Multiple independently admitted goals may run concurrently; one lead per goal
is an accountability rule, not a global execution lock.

Children return structured findings with source references, uncertainty,
unresolved constraints, and receipts for attempted effects. The lead reconciles
conflicts and owns the final outcome. Domain services enforce action rules,
idempotency, approval, and concurrency semantics even when several goals act
on the same resource. A child's success claim is not an execution receipt.

Read and write permissions are explicit per invocation. Focused children are
not inherently read-only, but a lead cannot delegate authority it lacks or
self-approve a child's action. The exact authorization mechanism and wire
contract require a successor RFC before implementation.

### Durable care without a permanent reasoning agent

Schedules, subscriptions, and open obligations remain owned by domains and
survive session completion. An event can initiate a lead with relevant domain
context; simple work can finish without children. The absence of a running
model never means the absence of responsibility. Proactive results retain the
existing owner attention, deduplication, and delivery policies.

### A revisable architecture, not a capability forecast

Depth one is the initial architectural bound requested by the owner, not a
claim that deeper collaboration can never help. Reconsider it only through an
explicit amendment backed by outcome evidence. Keep model selection per
invocation and preserve the ability to use distinct specialist models without
requiring them. Evaluate useful completed obligations, cross-domain coherence,
owner corrections, latency, total cost including retries, and safe failure.
Cheaper inference benefits both generalists and collaboration; it does not
make one topology universally superior.

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

The staffers are the entries in `roster/` whose `butler.toml` declares
`type = "staffer"` (for example Switchboard for routing and ingestion, and
Messenger for outbound delivery). Future infrastructure agents --- log
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
ABC contract --- `register_tools()`, `migration_revisions()`, lifecycle hooks --- but
its scope is cross-butler coordination, not domain specialization.

## Why Tool Surface Discipline Matters

Under RFC 0027, initial-context cost comes from tool definitions loaded into
model context at session start, not from every handler registered on the
canonical MCP server. Under eager discovery those sets coincide: every
registered definition enters initial context, consuming context window, increasing latency, and degrading tool selection,
especially on smaller or cheaper models. Under verified native deferred
discovery, the registered and searchable sets may be larger while full typed
definitions load only on demand.

The current 30-50 initially loaded tool guideline is an operational heuristic,
not a permanent limit on the system's capability catalog or a model-independent
reliability guarantee. Measure retrieval recall, task success, context cost,
latency, and cache behavior for supported model/runtime combinations.
Registration-time groups and manifesto alignment still govern service ownership.
A successor lead's searchable scope needs its own execution authorization
contract; RFC 0027 alone cannot broaden it.

**How to stay within budget:**

- **Core tools are not unconditional.** The daemon registers core tools based on
  butler type and name. Session analytics tools should be opt-in, not on every
  butler. Ingest tools belong on the Switchboard, not on domain
  butlers. The pattern already exists for `ingest` and messenger tools; it should
  be the default, not the exception.
- **Modules expose tool groups, not monoliths.** A module with 15 tools should
  define logical groups (e.g., "core", "entity", "admin") so butlers can import
  the subset they need. When no groups are specified, all tools register.
- **Manifesto alignment is a filter.** If a tool does not serve the butler's
  manifesto, it should not be registered --- even if the module that provides it
  is enabled. Tool groups make this granular.

**The two-layer gating model:** tool registration is gated at two independent
layers, and both must pass for a tool to appear on a butler's MCP surface.

1. **Core daemon tools** are gated by butler type and name (for example,
   temporal tools register only for domain butlers, ingest tools only for the
   Switchboard, notify delivery only for the Messenger), plus an optional
   declarative `core_groups` list in runtime config.
2. **Module tools** are gated by the `groups` list in each module's
   `butler.toml` config.

For both layers, omitting the group list registers every tool. The guards are
evaluated once at startup. RFC 0002 and RFC 0027 own the contract.

**The ownership principle:** Domain modules used by their own specialist butler
--- health tools on the health butler, finance tools on the finance butler ---
keep all groups enabled. There is no reason to prune a domain module on its home
butler. Cross-cutting modules are where pruning matters: memory, calendar,
approvals, and similar modules that appear on multiple butlers. Each butler
enables only the groups relevant to its role, and the rest stay silent.

**The constraint:** Every registered tool needs a domain or infrastructure
owner and a justified purpose. Frequency of use informs eager versus deferred
loading, not whether a legitimate rare capability deserves to exist.

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
The `public` schema holds the cross-butler entity registry (`public.entities`)
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
  the entity registry, situational context signals
  (RFC 0009), and insight delivery infrastructure (RFC 0011).

## The Core Loop

The adopted target goal loop is:

```text
admit event/obligation -> establish goal and authority -> reason and discover
                    -> act and optionally delegate to depth-1 children
                    -> reconcile evidence/effects -> record outcome
```

Admission, permissions, lineage, budgets, cancellation, and durable outcomes are
runtime responsibilities. Planning and choosing a focus are model decisions.
Persist the goal and evidence outside model context so recovery does not depend
on one transcript or an irreplaceable lead process. A recovered lead assumes
one fenced ownership slot; it does not create a second authority for the goal.
Stopping a goal fences new child creation and new effects across its children;
already committed actions remain recorded rather than described as undone.

The current implementation follows this cycle until explicitly superseded:

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

This is the existing routing baseline, not a requirement to retain a separate
classification and specialist reasoning pass for every future goal.

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
