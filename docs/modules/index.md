# Modules

> **Scope:** Pluggable capability units that extend butler functionality.
> **Belongs here:** Module system overview, per-module profiles (purpose, config, tools, DB tables, dependencies).
> **Does NOT belong here:** Connector transport details, dashboard UI specifics.

- [Module System](module-system.md) — the Module ABC, lifecycle hooks, dependency resolution, registration

### Module Profiles
- [Memory](memory.md) — persistent knowledge: episodes, facts, rules, retrieval
- [Calendar](calendar.md) — Google Calendar integration, event management
- [Contacts](contacts.md) — contact sync, identity resolution
- [Approvals](approvals.md) — human-in-the-loop approval gates
- [Email](email.md) — IMAP/SMTP email integration
- [Telegram](telegram.md) — Telegram bot and user client integration
- [Mailbox](mailbox.md) — internal mailbox for butler messages
- [Metrics](metrics.md) — Prometheus metrics and storage
- [Pipeline](pipeline.md) — message processing pipeline
- [Knowledge Base](knowledge-base.md) — entity-predicate knowledge graph
