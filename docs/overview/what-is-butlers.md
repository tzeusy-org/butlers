# What Is Butlers?

> **Purpose:** Walk a newcomer through how one request moves through Butlers.
> **Audience:** Anyone evaluating or newly encountering the project.
> **Prerequisites:** The one-page synthesis at the top of the [documentation index](../index.md).

## Overview

The [documentation index](../index.md#butlers-in-one-page) states what Butlers is, who it serves,
and how the pieces fit. This page follows a single trigger through that shape and names the
invariant each piece holds. Goals and non-goals are defined in
[Vision](../../about/heart-and-soul/vision.md).

![Butlers System Overview](./system-overview.svg)

## The Butler-as-Daemon Model

Every butler is a persistent async daemon that sits idle until a trigger arrives: an external MCP
call or a cron-scheduled task. It then spawns an ephemeral LLM CLI session (Claude Code, Codex,
Gemini) whose MCP config points only at that butler, lets it reason and act through the butler's
tools, logs the session, and returns to idle.

The daemon's core — state store (PostgreSQL JSONB), scheduler, spawner, and append-only session
log — is deterministic. The spawner owns runtime selection and concurrency limits; the session log
records trigger, tool calls, output, duration, and token usage for every invocation.

## Modules

Modules are how a butler gains capabilities: each implements `src/butlers/modules/base.py`
`Module`, registers MCP tools, owns its own tables and migrations, and hooks into startup and
shutdown. A butler opts into modules in its `butler.toml`; dependencies resolve in topological
order. Modules never modify core infrastructure. See [Modules](../modules/index.md).

## Connectors

Connectors run as separate processes. They read an external source, normalise each event into the
canonical ingestion envelope, and submit it to the Switchboard, owning their own checkpoints and
crash recovery. They are transport-only: classification and routing are never their job. See
[Connectors](../connectors/index.md).

## Switchboard Routing

The Switchboard is the single ingress. For each request it assigns a canonical request context
(request ID, timestamps, sender identity, source channel), classifies it with an LLM runtime,
dispatches to one or more domain butlers over MCP, and tracks the request to completion. Domain
butlers therefore never see transport details; they receive classified requests with an identity
preamble attached. See [Switchboard Routing](../concepts/switchboard-routing.md).

## Dashboard

The dashboard is a FastAPI backend (the Dashboard API) serving a React frontend, behind owner
authentication. It is where the owner watches butler status and sessions, manages identity,
stores secrets and OAuth credentials, and tunes module settings. Ports are defined in the
[deployment port map](../../about/lay-and-land/deployment.md#port-assignments).

## Storage

All butlers share one PostgreSQL database: one schema per butler for its state store, session log,
schedules, and module tables, plus `public` for cross-butler data. Identity lives there as
`public.entities` with channel handles recorded as `relationship.entity_facts`, which is what lets
every channel recognise the same sender; see [Identity Model](../concepts/identity-model.md).
Butler configuration itself is git-tracked under `roster/{butler}/` (`butler.toml`, `MANIFESTO.md`,
system prompt, skills).

## Related Pages

- [Project Goals](project-goals.md) --- motivation, design philosophy, and current status
- [Prerequisites](../getting_started/prerequisites.md) --- what you need installed before running Butlers
- [Butler Lifecycle](../concepts/butler-lifecycle.md) --- deep dive into the daemon startup and session cycle
- [Modules and Connectors](../concepts/modules-and-connectors.md) --- how modules and connectors work in detail
