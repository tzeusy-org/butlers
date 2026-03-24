# Butlers

> A personal AI agent system where specialized daemons handle the recurring mental labor of daily life — health tracking, relationship context, financial signals, schedule management — so you can stop thinking about it.

## The Idea

Most of the tasks you do every day are recurring, structured, and low-creativity but still require judgment: triaging email, summarizing meetings, tracking health measurements, remembering who you talked to about what, managing schedules. They are a natural fit for AI agents backed by persistent state and integrations.

Butlers is a system that does this. Not one giant agent that tries to handle everything, but a team of specialized agents — called butlers — each owning a domain of your life. A health butler that tracks your measurements, medications, and symptoms. A relationship butler that remembers your contacts, interactions, and life events. A finance butler watching your financial signals. A Switchboard that routes incoming messages from Telegram, Gmail, and Discord to the right specialist.

You own the instance. You own the data. You own the credentials. There is no cloud service, no account, no subscription. Just infrastructure that works for one person.

## What This Is Not

**Not a chatbot.** Butlers act autonomously on cron schedules — morning briefings, inbox triage, health check-ins, memory consolidation. You may never send a message directly and still get value. Conversation is one input channel, not the primary interface.

**Not a SaaS product.** There is no multi-tenant architecture, no shared database, no user accounts. Every instance belongs to exactly one person. If someone else wants Butlers, they run their own.

**Not a monolithic agent.** There is no single "do everything" agent. The health butler knows health. The relationship butler knows relationships. The Switchboard knows routing. No butler tries to be all of them.

**Not a framework.** Butlers is the product. It is not a library, not a toolkit, not a platform for third-party developers. It exists to serve one user's life.

## How It Works

The system has four layers: connectors that read from the outside world, a Switchboard that routes messages, domain butlers that act on them, and a dashboard that lets you see what's happening.

```
                       External World
          Gmail    Telegram    Discord    Microphone
            |          |          |           |
            v          v          v           v
    ┌─────────────────────────────────────────────┐
    │            Connectors (transport)            │
    │  Normalize events into a standard envelope   │
    └──────────────────┬──────────────────────────┘
                       │ ingest.v1 envelope
                       v
    ┌─────────────────────────────────────────────┐
    │              Switchboard                     │
    │  Who sent this? → What domain? → Route it   │
    └──┬────┬────┬────┬────┬────┬────┬────┬───────┘
       │    │    │    │    │    │    │    │  route.v1
       v    v    v    v    v    v    v    v
    ┌─────┬─────┬─────┬─────┬─────┬─────┬─────┬─────┐
    │Gen  │Rel  │Hlth │Edu  │Fin  │Trvl │Home │Msg  │
    │eral │ship │     │     │ance │     │     │engr │
    └─────┴─────┴─────┴─────┴─────┴─────┴─────┴─────┘
      Domain Butlers (persistent daemons, each with
      own personality, tools, memory, and database)
                       │
                       v
    ┌─────────────────────────────────────────────┐
    │              PostgreSQL                      │
    │  Per-butler schemas + shared identity tables │
    └─────────────────────────────────────────────┘
```

### The cycle

Every butler operation follows the same loop:

1. **Trigger** — An event arrives: a Telegram message, a Gmail thread, or a cron schedule firing.
2. **Classify** — The Switchboard figures out which domain the message belongs to. For email threads, it remembers previous routing decisions so replies go to the same butler without re-classifying.
3. **Route** — The classified event is dispatched to the right butler via MCP (Model Context Protocol).
4. **Spawn** — The receiving butler creates a locked-down environment and spins up a temporary AI session — an ephemeral LLM instance with access only to that butler's tools.
5. **Act** — The AI session reads state, calls tools, sends messages, stores data, and produces output.
6. **Log** — Everything is recorded: what triggered it, what tools were called, how many tokens it used, how long it took.

The butler daemon itself is deterministic infrastructure. It manages state, enforces schedules, and registers tools. The intelligence lives exclusively in the ephemeral AI sessions it spawns. This separation keeps the daemon testable and predictable while giving the AI sessions full reasoning power.

### Modules and connectors

Butlers gain capabilities through **modules** — pluggable units that add tools without touching core infrastructure. The email module adds send/search/read tools. The memory module adds tiered storage with vector search. The calendar module adds Google Calendar integration. Each butler opts into exactly the modules it needs.

**Connectors** are separate processes that bridge external services to the system. The Gmail connector reads your inbox. The Telegram connector listens for messages. The Discord connector watches channels. Connectors normalize everything into a standard envelope format and hand it to the Switchboard. They never classify or route — they just transport.

### Identity

When a message arrives, the Switchboard resolves who sent it. A Telegram chat ID, an email address, or a Discord handle maps to a canonical contact record with roles. The owner gets elevated trust. Unknown senders get temporary identities pending disambiguation. Every routed message carries an identity preamble so the receiving butler knows who it's talking to.

## What V1 Delivers

### The butler fleet

Nine specialized butlers running concurrently:

| Butler | Domain |
|--------|--------|
| **Switchboard** | Central ingress routing and message classification |
| **General** | Freeform data, collections, catch-all assistance |
| **Health** | Measurements, medications, conditions, symptoms, nutrition, research |
| **Relationship** | Contact management, interaction tracking, life events |
| **Finance** | Financial signal tracking and awareness |
| **Education** | Learning tracking and knowledge management |
| **Travel** | Trip planning and travel context |
| **Home** | Home automation awareness and environmental context |
| **Messenger** | Outbound notification delivery |

### Core capabilities

- **Tiered memory** — Observations flow from short-term (Eden) through consolidation to mid-term and long-term storage, with vector search across all tiers
- **Approval gates** — Sensitive tool calls require human confirmation before executing
- **Cross-channel identity** — One contact, recognized across Telegram, Gmail, and Discord
- **Scheduled autonomy** — Morning briefings, inbox triage, health check-ins, memory consolidation — all on cron
- **Self-healing** — Crash fingerprinting and automated recovery sessions
- **Full observability** — Distributed tracing and metrics via OpenTelemetry, viewable in Grafana
- **Web dashboard** — Real-time visibility into butler status, sessions, contacts, approvals, memory, and ingestion flow

### What's deferred

Multi-user support, mobile app, voice interface, self-hosting installer, plugin marketplace, end-to-end encryption. These may become v2 goals, but no v1 work is designed to "prepare for" them at the cost of simplicity.

## Core Principles

1. **One user, one instance, full sovereignty.** You own the database, the credentials, the API keys, and all data. No shared infrastructure, no multi-tenancy.

2. **Modules only add tools.** A module registers capabilities. It never modifies the state store, the scheduler, the spawner, or the session log. If something requires core changes, it belongs in core.

3. **Inter-butler communication flows through the Switchboard only.** Butlers don't share memory, call each other's functions, or access each other's database schemas. The Switchboard is the only bridge.

4. **The daemon is infrastructure; intelligence is in sessions.** The daemon manages lifecycle. The AI sessions do the thinking. Mixing reasoning into the daemon is a defect.

5. **Identity lives in git.** A butler's personality, schedule, module selection, and manifesto are git-tracked files under `roster/`. Runtime state is in the database. Identity is in the repository.

6. **Every butler has a manifesto.** The manifesto defines what the butler cares about, what it promises, and what it refuses. Features must align with it. A tool that contradicts the manifesto doesn't ship.

7. **Butlers never know how a message arrived.** Connectors handle transport. Butlers receive classified, structured requests. If a butler contains Telegram polling logic or Gmail API calls, something is wrong.

## Navigating the Documentation

This project's knowledge is organized into four pillars, each answering a different question:

| Pillar | Location | What You'll Find |
|--------|----------|-----------------|
| **Heart and Soul** | [`docs/heart-and-soul/`](heart-and-soul/) | Vision, principles, scope boundaries — the WHY |
| **Law and Lore** | [`docs/law-and-lore/`](law-and-lore/) | Numbered RFCs defining technical contracts — the HOW |
| **Spec and Spine** | [`openspec/`](../openspec/) | Detailed feature requirements with testable scenarios — the WHAT |
| **Lay and Land** | [`docs/lay-and-land/`](lay-and-land/) | Component maps, data flow diagrams, deployment topology — the WHERE |

**Start with** [`heart-and-soul/vision.md`](heart-and-soul/vision.md) for the full thesis and non-negotiable rules.

**Read** [`law-and-lore/`](law-and-lore/) when you need to understand a technical design decision — each RFC covers a subsystem (daemon lifecycle, MCP tools, routing, identity, observability, database schema, dashboard).

**Check** [`openspec/`](../openspec/) for the exact requirements before implementing a feature.

**Consult** [`lay-and-land/`](lay-and-land/) when you need to find where something lives, how data flows, or what depends on what.

For the technical documentation index with getting-started guides, module references, and operational runbooks, see [`docs/index.md`](index.md).

## Tech Stack

Python 3.12+, FastMCP, Claude Agent SDK, PostgreSQL with pgvector, Docker, asyncio, OpenTelemetry, Grafana (Alloy + Tempo + Prometheus), Vite + React (dashboard frontend).

## Current Status

Early development. The system runs, handles real data, and is used daily — but it is not production-hardened. See [`heart-and-soul/v1.md`](heart-and-soul/v1.md) for the explicit scope boundary and success criteria.
