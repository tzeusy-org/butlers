# What Is Butlers?

> **Purpose:** Introduce the Butlers framework, its architecture, and core concepts at a high level.
> **Audience:** Anyone evaluating or newly encountering the project.
> **Prerequisites:** None.

## Overview

Butlers is a personal AI agent framework. Each **butler** is a long-running MCP (Model Context Protocol) server daemon that handles recurring tasks, manages integrations, and acts on your behalf. Under the hood, butlers are powered by ephemeral LLM CLI sessions that reason and act through the butler's registered tools.

Think of it as a modular "Jarvis" system: you define specialized butlers for different life domains (health, relationships, general assistance), configure them with modules (email, Telegram, calendar), and let them run autonomously on schedules or respond to incoming messages.

![Butlers System Overview](./system-overview.svg)

## The Butler-as-Daemon Model

Every butler runs as a persistent async daemon. When nothing is happening, it sits idle, waiting. When a trigger arrives --- either an external MCP call or a cron-scheduled task --- the butler spins up an ephemeral LLM CLI instance, gives it access to the butler's tools via MCP, and lets it reason and act. Once the session completes, the butler logs what happened and returns to idle.

Each butler daemon comes with built-in core infrastructure:

- **State store** --- a key-value store backed by PostgreSQL JSONB, for persisting information between sessions.
- **Task scheduler** --- cron-driven scheduling that fires prompts on configurable cadences (morning briefings, inbox triage, periodic health checks).
- **LLM CLI spawner** --- the engine that generates locked-down MCP configs, invokes runtime CLIs (Claude Code, Codex, Gemini), and enforces concurrency limits.
- **Session log** --- an append-only record of every invocation: what triggered it, what tools were called, what the LLM produced, how long it took, and how many tokens it consumed.

## Modules

On top of core infrastructure, butlers gain capabilities through **modules** --- pluggable integration units that register MCP tools, manage their own database tables, and hook into the daemon lifecycle. Modules implement an abstract base class (`Module`) with methods for tool registration, migrations, startup, and shutdown.

Available modules include Email (IMAP/SMTP), Telegram (bot and user-client), Calendar (Google), Memory (tiered episodic/fact/rule storage), and more. Each butler declares which modules it needs in its `butler.toml` configuration file. Modules are resolved in topological order based on declared dependencies, so a module can safely depend on another module being initialized first.

## Connectors

While modules live *inside* butler daemons, **connectors** are standalone transport adapters that run as separate processes. They read events from external systems (Telegram updates, Gmail messages, Discord events), normalize them into a canonical ingestion format, and submit them to the Switchboard butler's ingestion API. Connectors handle their own checkpointing and crash recovery --- they are transport-only and never perform classification or routing.

## Switchboard Routing

The **Switchboard** is a special butler that acts as the single ingress point for the entire system. All external messages flow through it. When a message arrives (via a connector or direct MCP call), the Switchboard:

1. Assigns a canonical request context (request ID, timestamps, sender identity, source channel).
2. Uses an LLM runtime to classify the message and decide which domain butler(s) should handle it.
3. Fans out the work to the appropriate butler(s) via MCP.
4. Tracks the full request lifecycle through to completion.

This architecture means domain butlers never need to know about transport details. They receive well-structured, classified requests with identity preambles already attached.

## Dashboard

A web dashboard provides real-time monitoring and management. It consists of a FastAPI backend (the Dashboard API, port 41200) and a Vite-powered frontend (port 41173 in development). Through the dashboard you can view butler status, browse session logs, manage contacts and identity, configure OAuth credentials for LLM runtimes, and control module settings.

## Storage

Butlers share a single PostgreSQL database with per-butler schemas plus a `shared` schema for cross-butler data. The shared schema holds the identity tables (contacts, contact info, entities) that power sender recognition across all channels. Each butler's schema contains its state store, session log, scheduled tasks, and module-specific tables.

Butler configurations themselves are git-based directories under a `roster/` folder, containing a `butler.toml` (identity, port, schedules, modules), a `CLAUDE.md` (system prompt / personality), skills directories, and other personality files.

## What Butlers Is Not

Butlers is not a hosted SaaS product. It is a **user-federated** platform: each user owns and operates their own instance. You control the database, the credentials, and the LLM API keys. This design choice simplifies security (no multi-tenant isolation needed) and gives you full sovereignty over your data and agents.

## Related Pages

- [Project Goals](project-goals.md) --- motivation, design philosophy, and current status
- [Prerequisites](../getting_started/prerequisites.md) --- what you need installed before running Butlers
- [Butler Lifecycle](../concepts/butler-lifecycle.md) --- deep dive into the daemon startup and session cycle
- [Modules and Connectors](../concepts/modules-and-connectors.md) --- how modules and connectors work in detail
