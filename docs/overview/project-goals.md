# Project Goals

> **Purpose:** Explain the motivation behind Butlers, its design philosophy, and current project status.
> **Audience:** Contributors, evaluators, and anyone curious about the project's direction.
> **Prerequisites:** [What Is Butlers?](what-is-butlers.md)

## Overview

Butlers exists because its creator wanted a personal "Jarvis" --- a system that offloads the majority of mental toil tasks while retaining the benefits of having kept them in mind. As LLMs have grown more capable, the feasibility of building such a system has moved from science fiction to weekend project territory. Butlers is the first serious attempt to materialize that vision into a running, extensible microservice architecture.

## Motivation

The core insight is that many daily tasks are *recurring*, *structured*, and *low-creativity* but still require judgment: triaging email, summarizing meetings, tracking health measurements, remembering relationship context, managing schedules. These tasks are a natural fit for LLM agents backed by persistent state and integrations.

Rather than building one monolithic agent that tries to do everything, Butlers takes a domain-driven approach: specialized butlers handle specific life domains (health, relationships, general assistance, education), each with its own personality, tools, and memory. A central Switchboard routes incoming messages to the right specialist, much like a hotel concierge directing guests to the right staff member.

## Design Philosophy

### Modular by default

Every capability is a module. Email, Telegram, Calendar, Memory --- all are opt-in plugins that a butler either has or does not have. This keeps individual butlers focused and prevents capability sprawl. If a butler does not need email, it does not load the email module, does not run email migrations, and does not register email tools.

### MCP as the universal interface

Butlers communicate through the Model Context Protocol. Each butler is a FastMCP server. Ephemeral LLM instances connect to their butler's MCP endpoint and call tools to read state, send messages, query databases, and interact with external services. Inter-butler communication also flows through MCP, routed by the Switchboard. This means the same protocol governs LLM-to-butler, butler-to-butler, and client-to-butler interactions.

### User-federated

Butlers is designed as a self-hosted, single-user platform. Each user owns their instance, their database, their credentials, and their data. There is no multi-tenant architecture, no shared infrastructure, and no cloud dependency beyond the LLM API providers. This simplifies security: since you own the database, encryption at rest adds minimal value over the access controls you already maintain.

### Git-based configuration

Butler identities, personalities, schedules, and module configurations live in git-tracked directories. A butler's `CLAUDE.md` defines its system prompt. Its `MANIFESTO.md` articulates its purpose and value proposition. Its `butler.toml` declares ports, schedules, and enabled modules. Skills live in `.agents/skills/` directories. This makes butler configuration reviewable, versionable, and diffable.

### Agentic runtime, not agentic daemon

The daemon itself is deterministic infrastructure: it manages state, runs migrations, enforces schedules, and registers tools. The *intelligence* lives in the ephemeral LLM sessions that the daemon spawns. This separation means the daemon is testable, debuggable, and predictable, while the LLM sessions get the full power of reasoning and tool use.

## Current Status

Butlers is in **early development**. The project is, by its creator's own admission, "entirely vibe coded" --- born from experimentation with Claude Code, Codex, and the beads issue tracking system. It is far from production-ready.

What works today:

- Multi-butler daemon infrastructure with full lifecycle management
- Switchboard routing with LLM-based message classification
- Module system with Email, Telegram, Calendar, Memory, and more
- Connectors for Telegram (bot and user-client), Gmail, and Discord
- Web dashboard for monitoring and management
- Cron-based scheduling with deterministic and LLM-dispatched modes
- Session logging with token usage tracking
- Identity resolution across channels
- E2E test suite with benchmark scoring

What is still evolving:

- Memory subsystem (Eden/Mid-Term/Long-Term tiered architecture)
- Approval gates for safety-critical tool calls
- Self-healing module for crash recovery
- Production deployment hardening
- Documentation (you are reading part of this effort)

## Related Pages

- [What Is Butlers?](what-is-butlers.md) --- system overview
- [Prerequisites](../getting_started/prerequisites.md) --- getting set up
- [Dev Environment](../getting_started/dev-environment.md) --- running the full dev stack
