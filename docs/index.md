# Butlers Documentation

> Your guide to the Butlers AI agent framework — from first run to production operation.

## Reading Path

New here? Follow this sequence:

| # | Section | What you'll learn |
|---|---------|-------------------|
| 1 | [Overview](overview/index.md) | What Butlers is and why it exists |
| 2 | [Getting Started](getting_started/index.md) | Prerequisites, setup, first launch |
| 3 | [Concepts](concepts/index.md) | Core mental model — butlers, modules, connectors, routing |
| 4 | [Architecture](architecture/index.md) | System design, daemon internals, database topology |
| 5 | [Runtime](runtime/index.md) | How the system behaves when running |
| 6 | [Butlers](butlers/index.md) | Per-butler role profiles |
| 7 | [Modules](modules/index.md) | Pluggable capability units |
| 8 | [Connectors](connectors/index.md) | External transport adapters |

Then explore by topic as needed:

## Topic Index

### System Fundamentals
- [Overview](overview/index.md) — what Butlers is, project goals, system shape
- [Concepts](concepts/index.md) — butler lifecycle, modules vs connectors, switchboard routing, MCP model, identity model
- [Architecture](architecture/index.md) — system topology, daemon design, routing, database schema, observability

### Runtime Behavior
- [Runtime](runtime/index.md) — spawner, scheduler, sessions, model routing, tool call capture

### Components
- [Butlers](butlers/index.md) — switchboard, general, relationship, health, messenger, finance, education, travel, home
- [Modules](modules/index.md) — memory, calendar, contacts, approvals, email, telegram, mailbox, metrics, pipeline
- [Connectors](connectors/index.md) — telegram bot, telegram user client, gmail, heartbeat, live listener

### Interfaces
- [Frontend](frontend/index.md) — dashboard UI, information architecture, API contracts
- [API and Protocols](api_and_protocols/index.md) — MCP tools, ingestion envelope, dashboard API, inter-butler communication

### Infrastructure
- [Data and Storage](data_and_storage/index.md) — schema topology, migrations, state store, blob storage, credential store
- [Identity and Secrets](identity_and_secrets/index.md) — owner identity, contacts, OAuth, CLI auth, environment variables
- [Operations](operations/index.md) — Docker deployment, environment config, Grafana monitoring, troubleshooting

### Quality and Planning
- [Testing](testing/index.md) — strategy, markers, E2E suite, benchmarks
- [Roadmap](roadmap/index.md) — project plan, OpenSpec overview

### Reference
- [Diagrams](diagrams/) — source files for all documentation diagrams
- [Archive](archive/) — superseded, draft, and historical documents
