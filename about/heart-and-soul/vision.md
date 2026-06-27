# Vision

## What Butlers Is

Butlers is a personal AI agent system where specialized, long-running daemons
handle the recurring mental labor of daily life. Each butler owns a life domain
--- health, relationships, finance, education, travel, home --- and acts
autonomously on schedules and in response to incoming messages. A central
Switchboard routes everything to the right specialist, brokers proactive
insights from domain butlers, and maintains shared situational awareness so
butlers adapt to the user's current context. You own the instance, the data,
the credentials, and the agents. There is no cloud service, no account, no
subscription. Just infrastructure that works for one person.

The system distinguishes two types of agents:

- **Butlers** are domain specialists. Each butler owns a life domain, has a
  manifesto defining its scope and personality, and serves the user directly
  through scheduled tasks and message handling. Domain butlers are the primary
  interface between the user and the system.

- **Staffers** are infrastructure specialists. They serve the ecosystem rather
  than the user directly. Staffers handle cross-cutting concerns --- message
  routing, outbound delivery --- that must exist for domain butlers to function.
  A staffer's identity is defined by an infrastructure contract (its
  `MANIFESTO.md`) specifying responsibilities, SLAs, failure modes, and
  escalation procedures. Staffers are excluded from user-message routing and
  from briefing contribution, because their domain is the system, not the user's
  life.

## What Butlers Is Not

**Not a SaaS or hosted product.** There is no multi-tenant architecture, no
shared database, no user accounts. Every instance belongs to exactly one person.
If someone else wants Butlers, they run their own.

**Not a chatbot.** Butlers act autonomously on cron schedules --- morning
briefings, inbox triage, health check-ins, memory consolidation. A user may
never send a message directly and still get value. Conversation is one input
channel, not the primary interface.

**Not a monolithic agent.** There is no single "do everything" agent. Domain
specialization is fundamental. The health butler knows health. The relationship
butler knows relationships. The Switchboard knows routing. No butler tries to be
all of them.

**Not a framework for building other products.** Butlers is the product. It is
not a library, not a toolkit, not a platform for third-party developers. It
exists to serve one user's life, not to be packaged and resold.

**Not an experiment.** It is vibe-coded and early, but the intent is a system
that runs continuously, handles real data, and is trusted with real decisions.
The bar is reliability, not novelty.

## Non-Negotiable Rules

These are the load-bearing constraints. Violating any of them means the change
does not ship.

1. **User-federated: one user, one instance, full sovereignty.** The user owns
   the database, the credentials, the LLM API keys, and all data. There is no
   shared infrastructure. Design decisions must never assume or enable
   multi-tenancy.

2. **Modules only add tools --- they never touch core infrastructure.** A module
   registers MCP tools, declares database migrations, and hooks into the daemon
   lifecycle. It must not modify the state store, the scheduler, the spawner, or
   the session log. If a capability requires core changes, it belongs in core,
   not in a module.

3. **Inter-butler communication is MCP-only through the Switchboard.** Butlers
   must not share memory, call each other's functions, or access each other's
   database schemas. The Switchboard is the only sanctioned channel. This
   constraint is structural, not aspirational. Narrowly scoped exceptions
   (read-only SQL views for deterministic batch aggregation) may be documented
   via RFC when the MCP-compliant alternative incurs unjustifiable LLM session
   cost for zero-reasoning work. Each exception requires explicit guardrails
   and reuse criteria (see RFC 0010).

4. **The daemon is deterministic infrastructure; intelligence is in ephemeral LLM
   sessions.** The daemon manages state, runs migrations, enforces schedules, and
   registers tools. It must be testable, debuggable, and predictable. The LLM
   sessions spawned by the daemon are where reasoning and judgment happen. Mixing
   LLM logic into the daemon is a defect.

5. **Git-based config is the source of truth for butler identity; operational
   tuning lives in the database.** A butler's personality, schedule, module
   selection, and manifesto live in git-tracked files under `roster/`. These
   define *who the butler is* --- its name, purpose, domain, and governing
   document. Operational tuning lives in the database, not git. Concurrency
   limits and tool surface configuration (`core_groups`) live in a per-schema
   `runtime_config` table, seeded from git on first boot and managed via the
   dashboard thereafter. Model selection and session timeouts are resolved per
   complexity tier from the shared `public.model_catalog` table and edited via
   the dashboard Models tab (see `src/butlers/core/model_routing.py`). The
   distinction: identity answers
   "what is this butler?"; operational tuning answers "how should it behave
   right now?" If someone changes a butler's name, manifesto, or module list,
   that is an identity change and belongs in git. If someone changes which
   model it uses or how many concurrent sessions it runs, that is operational
   tuning and belongs in the database.

6. **Every agent has a governing document that controls its scope.** For
   butlers, this is a manifesto: it defines what the butler cares about, what
   it promises, what it refuses, and the conceptual frameworks it uses to
   structure and prioritize knowledge within its domain. For staffers, this is
   an infrastructure contract: it defines the service's responsibilities, SLAs,
   failure modes, dependency graph, and escalation procedures. In both cases the
   document is not decoration --- it is binding. Features, tools, and UX
   decisions must be deeply aligned with the governing document. A capability
   that contradicts it must not be added without a formal amendment.

7. **Transport is connector responsibility; butlers never know about transport
   details.** Connectors normalize external events into a canonical ingestion
   format and submit them to the Switchboard. Butlers receive classified,
   structured requests. A butler must never contain Telegram polling logic, Gmail
   API calls, or Discord websocket handling. If a butler knows how a message
   arrived, something is wrong.

## What Success Looks Like

Butlers succeeds when it runs for weeks without intervention, handles the
owner's daily information flow, and the owner trusts it with progressively more
autonomy. The measure is not feature count --- it is the amount of mental labor
the system reliably absorbs.

Concrete markers:

- The owner sends a message from any channel and the right butler handles it
  without manual routing.
- Scheduled tasks fire on time, produce useful output, and recover from
  transient failures.
- The owner's health data, relationship context, financial signals, and calendar
  are maintained without manual entry.
- Butlers surface timely, relevant insights without being asked --- and
  automatically reduce frequency when the owner disengages.
- The system is boring. It works. The owner stops thinking about it.

## Anti-Patterns

- Adding a "general purpose" mode that bypasses domain specialization.
- Building admin features for managing multiple users.
- Embedding transport-specific logic inside butler code.
- Making the daemon "smart" instead of keeping intelligence in sessions.
- Treating manifestos as optional documentation rather than binding contracts.
- Designing features that require an always-on internet connection to function
  at the daemon level (LLM calls are the exception, not the rule).
