# Your AI Agent Doesn't Need to Be One Agent

Most people building personal AI systems make the same mistake: they build one agent and keep making it bigger.

It starts innocently. You want an AI assistant that handles your email. Then you add calendar. Then health tracking. Then your contacts. Then financial awareness. Each feature is a new system prompt paragraph, a new set of tools, a wider context window. Six months in, you have a 15,000-token system prompt, a model that confuses your blood pressure with your bank balance, and a vague sense that something went structurally wrong.

Something did. You built a monolith.

I've been building Butlers for the past few months --- a personal AI agent system that takes the opposite approach. Instead of one agent that does everything, it's nine specialized daemons, each owning a life domain, connected by a protocol they all speak natively. This post is about why that architecture exists, what I got wrong along the way, and what I think matters if you're building something similar.

## The Problem With One Big Agent

A single agent handling health, relationships, finance, education, travel, and home automation will inevitably hit three walls:

**Context pollution.** Your health history leaks into finance sessions. Your relationship notes consume tokens during calendar queries. Every session pays the cost of every domain, whether it's relevant or not. This isn't a theoretical concern --- it's the first thing you notice when you start logging real data across multiple life domains.

**Personality incoherence.** A health companion should be patient and non-judgmental. A financial advisor should be precise and risk-aware. A relationship tracker should be warm and recall-oriented. These are different modes of interaction. Cramming them into one system prompt produces a personality that's mediocre at everything and excellent at nothing.

**Scope creep without guardrails.** When there's no boundary, every feature belongs everywhere. Where does "remind me to call Mom about her doctor's appointment" live? Health? Relationships? Calendar? In a monolith, the answer is "wherever the prompt happens to route it today." In a specialized system, the Relationship butler owns it, because the manifesto says so.

## Nine Daemons, One Protocol

Butlers runs nine domain-specialized butlers as persistent async daemons:

- **Switchboard** --- the central router. Every message enters here.
- **Health** --- measurements, medications, conditions, symptoms, nutrition.
- **Relationship** --- contacts, interactions, important dates, gifts, the Dunbar model.
- **Finance** --- financial signals and awareness.
- **Education** --- learning tracking and knowledge management.
- **Travel** --- trip planning and context.
- **Home** --- home automation awareness via Home Assistant.
- **General** --- the catch-all. Freeform collections for everything that doesn't have a specialist.
- **Messenger** --- outbound notification delivery.

They communicate exclusively through MCP (Model Context Protocol). This isn't a convenience choice --- it's a structural constraint. Butler A cannot import Butler B's code, access Butler B's database schema, or communicate through any side channel. The Switchboard is the only bridge.

Why MCP specifically? Because it's the same protocol that LLM sessions use to call tools. A butler can't tell whether a tool call came from a Claude session or from the Switchboard dispatching a classified message. This symmetry means the butler's MCP tools are its complete interface --- no hidden backdoors, no special internal APIs.

## Intelligence and Infrastructure Are Different Things

Here's the opinion that shaped everything: **the daemon should be dumb.**

Each butler daemon is deterministic infrastructure. It manages lifecycle, runs migrations, registers tools, enforces cron schedules, and logs sessions. It does not reason, classify, or decide. It's testable, debuggable, and predictable.

Intelligence lives exclusively in ephemeral LLM sessions that the daemon spawns on demand. When a trigger fires --- an incoming message, a cron tick, a connector submission --- the daemon generates a locked-down MCP config, spawns a fresh Claude Code (or Codex, or Gemini) session with only that butler's tools available, and lets the LLM do its thing. The session runs, calls tools, reads and writes state, then exits. The daemon logs what happened: tokens consumed, tools called, duration, cost.

This separation matters more than it sounds. When the daemon is stateless and predictable, you can test it with normal unit tests. When intelligence is confined to ephemeral sessions, you can swap models, adjust prompts, and change behavior without touching infrastructure code. The two concerns evolve independently.

The alternative --- mixing LLM logic into the daemon, making the infrastructure "smart" --- is a defect in this architecture. I've caught myself doing it twice and reverted both times.

## The Switchboard: Routing as a First-Class Problem

When you message the system --- from Telegram, Gmail, Discord, wherever --- the Switchboard receives it first. It uses LLM-based classification to determine which butler should handle it, then dispatches via MCP.

"I've been feeling tired lately" goes to Health. "Coffee with Alex tomorrow" goes to Relationships. "What's on my shopping list?" goes to General. The user never has to think about routing. They just talk.

This works because the Switchboard knows each butler's capabilities and can match intent against domain. Thread affinity means follow-up messages in an existing conversation stick to the same butler without re-classification. Fanout support means a message that genuinely spans domains can reach multiple butlers.

The key insight: **connectors and routing are separate concerns.** The Telegram connector doesn't know about health or relationships. It normalizes Telegram events into a canonical envelope format (`ingest.v1`) and submits them to the Switchboard. Gmail does the same with email. Discord with websocket events. Each connector is a standalone process that handles exactly one transport's connection model, authentication, rate limits, and failure modes.

This means the Telegram connector can crash and restart without affecting any butler. A butler can restart without dropping Telegram connections. Transport diversity doesn't infect domain logic.

## Manifestos Are Not Documentation

Every butler has a `MANIFESTO.md` that defines its identity, purpose, and boundaries. This sounds like documentation. It isn't. It's a binding contract.

The Health Butler's manifesto says: *"Your health is not a snapshot --- it's a story told over weeks, months, and years."* That's not marketing. It tells you that the health tools must support longitudinal tracking, that trend analysis is core, and that one-off queries without history are insufficient.

The Relationship Butler's manifesto incorporates the Dunbar model --- concentric layers of social connection at 5, 15, 50, 150, 500, and 1500 contacts. This isn't a nice-to-have feature. It's the foundational model for how the butler prioritizes attention. Inner-circle relationships get more frequent check-ins. Relationship health scores decay without interaction. The architecture follows from the manifesto.

When two butlers could plausibly own a capability, the manifestos resolve the dispute. When a proposed feature contradicts a manifesto, the feature doesn't ship --- or the manifesto gets updated first, with full consideration of the implications.

I've found this to be the single most useful architectural decision in the project. It turns "where should this go?" from a judgment call into a lookup.

## Modules: The Only Extension Mechanism

Capabilities are added through modules. A module implements an abstract base class with three hooks:

- `register_tools()` --- adds MCP tools.
- `migrations()` --- declares database migrations.
- `on_startup()` / `on_shutdown()` --- lifecycle hooks.

That's it. Modules only add tools. They never modify core infrastructure --- the state store, the scheduler, the spawner, or the session log. If a capability requires core changes, it belongs in core, not in a module.

This sounds restrictive. It is. That's the point.

It prevents capability sprawl --- if it's not a module, it doesn't exist. It enforces isolation --- each module owns its own tables, can't touch another module's schema. It enables composition --- a butler opts into exactly the modules it needs via `butler.toml`. The health butler has measurements, medications, and nutrition. The general butler has collections and calendar. Neither carries the other's weight.

Dependencies between modules are declared explicitly and resolved via topological sort at startup. No circular deps, no implicit ordering, no surprises.

## Memory: Three Tiers, Not One

The memory subsystem is worth calling out because most agent memory implementations are a flat key-value store or a single vector database. Butlers uses a three-tier model:

**Eden** (short-term): Raw observations from LLM sessions. Everything goes here first. High volume, unprocessed.

**Mid-Term** (consolidated): A cron job runs every six hours, consolidating Eden entries into structured, embedded facts. Vector search via pgvector operates here.

**Long-Term** (archival): Promoted from mid-term based on relevance and access frequency. Compressed, stable facts that persist across months.

Each butler that enables the memory module gets its own tiers within its database schema. Memory is per-butler. The health butler's memory is about health. The relationship butler's memory is about people. No cross-contamination.

The consolidation job is the key piece. It's the difference between "the agent said something about my blood pressure once" and "the system knows my blood pressure trends over the past six months." Raw observations become structured knowledge through periodic processing, not through heroic single-session context windows.

## User-Federated: One User, One Instance

This is the non-negotiable rule that shapes every other decision: **one user, one instance, full sovereignty.**

You own the database, the credentials, the LLM API keys, and all data. There is no cloud service, no account, no subscription. If someone else wants Butlers, they run their own.

This simplifies security enormously. There's no multi-tenant isolation because there's no multi-tenancy. The threat model is: protect the owner's data from unauthorized access, and prevent agents from acting beyond their intended scope. That's it.

All nine butlers share a single PostgreSQL database with schema-level isolation. One connection string, one backup target, one monitoring endpoint. Running nine separate databases for a single-user system would be absurd.

Credentials follow a DB-first model --- the `CredentialStore` checks a database table first, falling back to environment variables only for infrastructure bootstrap (database connection params, OTEL endpoint). Runtime secrets --- API keys, OAuth tokens --- live in the database and are managed through the dashboard. No `.env` files with 47 API keys.

## The Boring Parts That Matter

**Observability**: OpenTelemetry traces from ingestion through classification, routing, and session execution. The spawner injects `TRACEPARENT` into LLM CLI subprocesses, so you get connected traces across the entire flow. Grafana Alloy collects; Tempo stores traces; Prometheus scrapes metrics.

**Self-healing**: When an LLM session crashes, the system fingerprints the error, tracks recurrence, and can dispatch a healing session using a dedicated model tier. This sounds like magic. In practice it's pattern matching and retry logic. But it means the system recovers from transient failures without waking you up.

**Approval gates**: Sensitive operations --- sending messages on your behalf, modifying calendar events, deleting data --- require explicit owner confirmation. The gate is enforced at the MCP server level, not in the prompt. The LLM can't bypass it. Timeouts result in denial, not silent approval.

**Dashboard**: FastAPI backend with a Vite frontend. Real-time butler status, session logs, contact and identity views, ingestion monitoring, credential configuration. It's not pretty yet, but it's functional.

## What I'd Do Differently

**Start with fewer butlers.** Nine was ambitious for v1. Three or four would have validated the architecture faster with less surface area. The Switchboard, Health, Relationship, and General cover 80% of daily use.

**The module system came too late.** Early butlers had capabilities baked directly into their daemon code. Extracting those into modules was painful. If I were starting over, the module system would exist before any domain butler.

**Connector testing is hard.** Each connector has its own transport model, authentication flow, and failure mode. Integration testing across Telegram, Gmail, and Discord simultaneously is a logistics problem as much as a technical one. I still don't have a great answer here.

## What Matters

If you're building a personal AI system, here's what I think actually matters:

1. **Separate intelligence from infrastructure.** Make the daemon predictable. Let the LLM be creative. Don't mix them.

2. **Domain boundaries should be process boundaries.** If two agents can't access each other's database, you'll never have context leakage. Convention-based isolation doesn't hold.

3. **Give every agent an identity document.** Call it a manifesto, a constitution, whatever. Write down what it cares about, what it refuses, and what frameworks it uses. Then enforce it.

4. **Memory needs structure, not just storage.** A flat vector store is not memory. Consolidation, tiering, and periodic processing turn observations into knowledge.

5. **You are the only user.** If you're building for yourself, embrace it. Single-user systems are simpler, more secure, and more honest about their constraints. Multi-tenancy is a different product.

Butlers runs on my machine, handles my daily information flow, and absorbs the mental labor I used to spend on tracking, remembering, and routing. It's not finished. It might never be. But it works, and it works because it's nine specialists collaborating through a protocol, not one generalist drowning in context.

The measure isn't feature count. It's the amount of mental labor the system reliably absorbs. By that measure, it's already worth it.
