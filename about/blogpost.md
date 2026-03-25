# Butlers: Nine Specialists Instead of One Generalist

A few weeks ago, while recovering from surgery and exploring vibe coding, I started building a personal digital butler. The idea isn't new --- thousands of people have the same itch, and projects like OpenClaw have shown there's real appetite for it. But I wanted to build my own, for reasons that shaped the architecture in ways I didn't expect.

Six things pushed me toward a custom build:

1. I wanted to learn vibe coding properly, inspired by Gas Town and Jeffrey Emanuel's work in agentic coding
2. My personal setup (tailnet, Home Assistant, specific integrations) needed custom wiring, not contributions to someone else's platform
3. I had a fundamentally different architectural idea than what I'd seen
4. End-to-end security and code control mattered more than ecosystem breadth
5. Security through customization --- a bespoke system has a different attack surface than the most-starred project on GitHub
6. I wanted an automated CRM that didn't require me to manually ingest data

What came out is Butlers --- a system of nine domain-specialized AI agents running as persistent daemons on my machine. Not one agent that does everything. Nine that each do one thing well.

This is possible now because LLMs have become competent enough to serve as universal intent-to-workflow translation layers. Machine output converts to intuitive English. The costs are affordable and dropping. Agent skills and MCP tooling have matured to the point where this isn't a research project --- it's usable infrastructure.

## What Is a Butler?

A butler is a secretary with both autonomous and interactive capabilities, interacting with customizable, modular digital life components. Wherever I have a digital presence with an accessible API, modules expose read/write capabilities to LLMs operating based on:

1. Preconfigured prompts and personality definitions
2. Inputs from other data sources (email, Telegram, calendar, Home Assistant)
3. User-defined scheduled workflows, reminders, and TODOs

This means data from arbitrary systems can meaningfully affect other systems, with LLMs and prompts as the translation layer between them.

A concrete example: a wedding invitation email arrives. The Gmail connector picks it up and submits it to the Switchboard. The Switchboard classifies it and fans out to multiple butlers. The Calendar module registers the event in Google Calendar. The Relationship butler documents the marriage, updates the participants' contact records, and notes it as a life event. I didn't touch anything. The system understood the email, extracted the structured data, and wired it to the right places.

## Why Not One Big Agent?

I started with the same instinct everyone has: one agent, keep adding tools. It breaks down fast for three reasons.

**Context pollution.** Your health history leaks into finance sessions. Your relationship notes consume tokens during calendar queries. Every session pays the cost of every domain, whether it's relevant or not. This is the first thing you notice when you start logging real data across multiple life domains --- it's not theoretical.

**Personality incoherence.** A health companion should be patient and non-judgmental. A financial advisor should be precise and risk-aware. A relationship tracker should be warm and recall-oriented. Cramming them into one system prompt produces a personality that's mediocre at everything.

**Context window constraints.** Domain specialization provides immediate "quick wins" by reducing the contextualization load --- prompts, personas, tooling --- per session. This is critical given that context windows are finite and expensive. A health session loads health tools and health personality. It doesn't carry the weight of education curricula or home automation schemas.

The solution requires an intelligent routing layer deciding which butler(s) receive which payload sections, plus context propagation maintaining critical details --- chat_id, message_id, email_id --- throughout the entire flow lifecycle.

## The Architecture: Three Orthogonal Dimensions

The system organizes along three dimensions:

1. **Roster** --- nine domain-specialized butlers
2. **Modules** --- pluggable tools and capabilities per butler
3. **Connectors** --- data ingestion from external systems

With two interaction modes: a chat medium (Telegram primarily) and a frontend dashboard for investigation and overviews.

### The Roster

- **Switchboard** --- the intelligent front door. Every message enters here, gets classified, and routes to the right specialist.
- **Health** --- measurements, medications, conditions, symptoms, nutrition, research. Tracks the full picture of your wellbeing longitudinally.
- **Relationship** --- a personal CRM built on the Dunbar model. Contacts, interactions, important dates, gifts, relationship health scores that decay without interaction.
- **Finance** --- financial signal tracking and awareness.
- **Education** --- curricula, spaced repetition, learning tracking, and knowledge management.
- **Travel** --- trip planning and context.
- **Home** --- deep wiring of the Home Assistant API for home automation awareness.
- **General** --- the catch-all. Freeform collections for everything that doesn't have a specialist.
- **Messenger** --- outbound notification delivery.

They communicate exclusively through MCP (Model Context Protocol). Butler A cannot import Butler B's code, access Butler B's database schema, or communicate through any side channel. The Switchboard is the only bridge. This isn't a convention --- it's enforced at the process level.

### Why MCP?

MCP serves as the universal protocol for three relationships:

1. **LLM-to-butler:** Ephemeral sessions call the butler's tools to read state, send messages, and interact with services.
2. **Butler-to-butler:** The Switchboard dispatches work to domain butlers. Domain butlers never call each other directly.
3. **Client-to-butler:** The dashboard and connectors interact through the same MCP endpoints.

A butler can't tell whether a tool call came from a Claude session or from the Switchboard dispatching a classified message. This symmetry is deliberate --- the butler's MCP tools are its complete interface, with no hidden backdoors.

MCP servers also enable constraint-based workflow design. Instead of exposing powerful tools like arbitrary CLI access, each butler gets precisely the tools its modules provide. Static, locally-run docstrings controlled entirely by the codebase. Composed modules eliminate tooling waste. Local-only configuration eliminates authentication concerns.

### The Daemon Model: Dumb Infrastructure, Smart Sessions

Each butler concretizes as a **persistent MCP server** with preconfigured tools that spawns **ephemeral LLM sessions** upon specific **triggers**.

The daemon itself is deterministic infrastructure. It manages lifecycle, runs migrations, registers tools, enforces cron schedules, and logs sessions. It does not reason, classify, or decide. It's testable, debuggable, and predictable.

Intelligence lives exclusively in the ephemeral LLM sessions the daemon spawns on demand. When a trigger fires --- an incoming message, a cron tick, a connector submission --- the daemon generates a locked-down MCP config, spawns a fresh session with only that butler's tools available, and lets the LLM work. The session runs, calls tools, reads and writes state, then exits. The daemon logs everything: tokens consumed, tools called, duration, cost.

For the LLM runtime, I had two options: wrap existing CLIs (Claude Code, Codex, OpenCode) or use application-specific SDKs (Claude Agent SDK). I went with CLI wrapping for three reasons:

1. **Piggyback active development.** CLIs automatically support emerging capabilities --- system prompts, tool orchestration, skills configuration --- without me maintaining SDK integrations.
2. **Ephemeral sessions don't need interactivity.** CLI invocation is a natural fit for the spawn-run-exit model.
3. **Avoids maintenance burden.** No library updates, deprecations, or cross-SDK incompatibilities to manage.

This also enables model agnosticism. The spawner has pluggable runtime adapters for Claude Code, Codex, Gemini CLI, and OpenCode. Swapping the model behind a butler is a config change, not a code change.

### Skills: Lazy-Loaded Context

Agent Skills introduce specialized butler capabilities that work naturally with the modular design. Each butler accesses both its own skills and shared skills.

Skills are extremely context-efficient. Instead of embedding entire workflows in scheduled prompts, skills can be invoked selectively. Only the YAML frontmatter consumes context at session start --- the full skill body is "lazy-loaded" when the LLM decides it's relevant. This means a butler can have access to a dozen complex workflows (memory taxonomy, health check-in protocols, relationship maintenance routines) without paying the token cost unless they're actually needed.

### Connectors: Transport Without Opinion

Connectors are standalone processes that bridge external event sources to the Switchboard. They normalize events into a canonical envelope format (`ingest.v1`) and submit via MCP. They do not classify or route --- that's the Switchboard's job.

The Telegram connector doesn't know about health or relationships. Gmail doesn't know about calendar. Each connector handles exactly one transport's connection model, authentication, rate limits, and failure modes.

This means the Telegram connector can crash and restart without affecting any butler. A butler can restart without dropping Telegram connections. Transport diversity doesn't infect domain logic.

### Modules: The Only Extension Mechanism

Capabilities are added through modules. A module implements an abstract base class with three hooks:

- `register_tools()` --- adds MCP tools
- `migrations()` --- declares database migrations
- `on_startup()` / `on_shutdown()` --- lifecycle hooks

That's it. Modules only add tools. They never modify core infrastructure. If a capability requires core changes, it belongs in core, not in a module.

A butler opts into exactly the modules it needs via `butler.toml`. The health butler has measurements, medications, and nutrition. The general butler has collections and calendar. Neither carries the other's weight. Dependencies between modules are declared explicitly and resolved via topological sort at startup.

### Manifestos: Identity as Architecture

Every butler has a `MANIFESTO.md` that defines its identity, purpose, and boundaries. This sounds like documentation. It isn't. It's a binding contract.

The Health Butler's manifesto says: *"Your health is not a snapshot --- it's a story told over weeks, months, and years."* That tells you the health tools must support longitudinal tracking, that trend analysis is core, and that one-off queries without history are insufficient.

The Relationship Butler's manifesto incorporates the Dunbar model --- concentric layers of social connection at 5, 15, 50, 150, 500, and 1500 contacts. Inner-circle relationships get more frequent check-ins. Relationship health scores decay without interaction. The architecture follows from the manifesto.

When two butlers could plausibly own a capability, the manifestos resolve the dispute. This has been the single most useful architectural decision in the project --- it turns "where should this go?" from a judgment call into a lookup.

## Memory: Inspired by the JVM

Most agent memory implementations are a flat key-value store or a single vector database. Butlers uses a three-tier model inspired by Java's JVM garbage collection design:

**Eden** (short-term): Raw observations from LLM sessions. Everything goes here first. High volume, unprocessed.

**Mid-Term** (consolidated): A cron job runs every six hours, consolidating Eden entries into structured, embedded facts. Vector search via pgvector operates here.

**Long-Term** (archival): Promoted from mid-term based on relevance and access frequency. Compressed, stable facts that persist across months.

Each butler that enables the memory module gets its own tiers within its database schema. The health butler's memory is about health. The relationship butler's memory is about people. No cross-contamination.

The consolidation job is the key piece. It's the difference between "the agent said something about my blood pressure once" and "the system knows my blood pressure trends over the past six months." Raw observations become structured knowledge through periodic processing, not through heroic single-session context windows. The goal is multi-year information retention that builds knowledge bases and a personalized "voice" over time.

## Telemetry: Non-Negotiable for Agentic Systems

I've come to believe that end-to-end telemetry is not optional for agentic system design. You need it for:

- **Propagation visibility.** Understanding how a user query flows across butlers and tool calls. OpenTelemetry traces from ingestion through classification, routing, and session execution. The spawner injects `TRACEPARENT` into LLM CLI subprocesses, so you get connected traces across the entire flow.
- **Error surfacing.** Explicit stack traces and error logging when LLMs mis-invoke MCP tools --- which they do, regularly.
- **Prompt injection detection.** External inputs (emails, newsletters) can contain inadvertent prompt injections. I discovered that Marginal Revolution newsletters contain "Add a comment to this post: {URL}" button text that the model was treating as instructions. Telemetry surfaced this immediately.
- **Cost tracking.** Full visibility into token usage, query history, and system load per butler, per session.

Grafana Alloy collects everything. Tempo stores traces. Prometheus scrapes metrics. The dashboard surfaces it all.

## Security: One User, Full Sovereignty

This is the non-negotiable rule that shapes every other decision: **one user, one instance, full sovereignty.**

I own the database, the credentials, the LLM API keys, and all data. There is no cloud service, no account, no subscription. The system runs exclusively within my private tailnet with personal data. If someone else wants Butlers, they run their own.

This simplifies security enormously. There's no multi-tenant isolation because there's no multi-tenancy. The threat model is: protect the owner's data from unauthorized access, and prevent agents from acting beyond their intended scope. That's it.

All nine butlers share a single PostgreSQL database with schema-level isolation. Credentials follow a DB-first model --- runtime secrets live in the database and are managed through the dashboard, not scattered across `.env` files.

Approval gates provide the last line of defense for sensitive operations --- sending messages on your behalf, modifying calendar events, deleting data. The gate is enforced at the MCP server level, not in the prompt. The LLM can't bypass it. Timeouts result in denial, not silent approval.

## Examples of It in Use

### Education Butler: Curricula + Spaced Repetition

The Education Butler generates full curricula via curriculum-planning skills, delivers lessons through Telegram, and grades my responses. It adjusts curriculum progress and focus areas based on my performance. I'm using it to study topics where I want structured, paced learning --- the butler handles the pedagogy, I just show up and answer questions.

### Home Butler: Home Assistant Integration

The Home Butler has deep read access to the Home Assistant API. It generates comprehensive home environment reports --- temperature, humidity, air quality, energy usage. The Health Butler can correlate bedroom temperature and humidity with sleep quality, or indoor air quality with respiratory symptoms. Environmental data becomes health data when the right butler can see it.

### Relationship Butler: Automated CRM

This was one of the original motivations. When I have coffee with someone, I tell Telegram about it. The Relationship butler logs the interaction, updates the contact's health score (Dunbar decay model), extracts any facts mentioned, and notes follow-up items. No manual data entry into a CRM. No forgetting to update a spreadsheet. The butler remembers so I can focus on being present.

### Cross-Butler Workflows

The wedding invitation example captures the real power: a single email triggers calendar events, relationship updates, and memory storage across multiple butlers. I didn't configure this workflow explicitly. The Switchboard classified the content and fanned out to the relevant specialists. Each butler did what its manifesto says it should do.

## Current Thoughts

The barrier to entry for application development has genuinely vanished. What limits progress now is concretizing ideas and understanding fundamentals. Coming from a software engineering and SRE background, I'm aware there are significant knowledge gaps that probably produced suboptimal architectural decisions in places. An experienced distributed systems engineer would handle some of this differently.

But the system already delivers meaningful daily value at alpha stage. It handles my daily information flow, tracks health data, maintains relationship context, and routes messages intelligently. It's been running for weeks without major intervention.

The honest assessment: it's early, it's vibe-coded, but the intent is a system that runs continuously, handles real data, and is trusted with real decisions. The bar is reliability, not novelty.

## What I'd Do Differently

**Start with fewer butlers.** Nine was ambitious for v1. Three or four would have validated the architecture faster. The Switchboard, Health, Relationship, and General cover 80% of daily use.

**The module system came too late.** Early butlers had capabilities baked directly into their daemon code. Extracting those into modules was painful. If I were starting over, the module system would exist before any domain butler.

**Connector testing is hard.** Each connector has its own transport model, authentication flow, and failure mode. Integration testing across Telegram, Gmail, and Discord simultaneously is a logistics problem as much as a technical one.

## Next Milestones

1. **Model agnosticism.** Move toward a local-first design with no external dependencies via OpenCode and Ollama support. The runtime adapter system already supports this --- it's a matter of testing and tuning.
2. **Performance.** Complex workflows currently take around 60 seconds end-to-end. Faster models (thinking-mode variants, smaller specialists for simple routing) could make the UX feel instant for common interactions.
3. **WhatsApp connector.** Adding a Go sidecar using whatsmeow for WhatsApp bridge support --- already in progress.

## Try It

There's no hosted demo --- the system runs exclusively within my private tailnet with personal data. But the code is open. Clone the repo and run `./scripts/dev.sh`. You'll need Docker, a PostgreSQL instance, and at least one LLM API key.

The measure of this system isn't feature count. It's the amount of mental labor it reliably absorbs. By that measure, it's already worth it.
