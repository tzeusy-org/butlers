# Inter-Butler Communication

> **Purpose:** Explain how butlers communicate with each other through the Switchboard, and why direct database access between butlers is forbidden.
> **Audience:** Butler developers, system architects, anyone designing cross-butler workflows.
> **Prerequisites:** [MCP Tools](mcp-tools.md), understanding of the Switchboard role.

## Overview

Butlers in the Butlers framework communicate exclusively via MCP (Model Context Protocol) through the Switchboard butler. There is no direct database access, shared memory, or peer-to-peer messaging between butlers. This constraint is a deliberate architectural decision that enforces isolation, simplifies reasoning about state, and makes the system auditable.

## The Isolation Principle

Each butler operates within its own PostgreSQL schema. The database isolation model is schema-based within a single PostgreSQL instance:

- **Per-butler schema:** Each butler (e.g., `switchboard`, `general`, `relationship`, `health`) has its own schema containing all butler-specific tables.
- **Shared schema:** The `shared` schema contains cross-butler identity tables (`shared.contacts`, `shared.contact_info`, `shared.entities`, `shared.entity_info`, `shared.google_accounts`) and infrastructure tables (`shared.model_catalog`, `shared.token_limits`, `shared.token_usage_ledger`).
- **No cross-schema queries:** A butler's DB connection is scoped to its own schema plus `shared`. It cannot see or write to another butler's tables.

## Communication Flow

All inter-butler communication follows this pattern:

```
Butler A  --[MCP tool call]--> Switchboard  --[route/forward]--> Butler B
```

### Inbound Flow (External Events)

1. A **connector** (Telegram, Gmail, etc.) submits an `ingest.v1` envelope to the Switchboard via MCP.
2. The Switchboard classifies the message, resolves the sender's identity, and determines which specialist butler should handle it.
3. The Switchboard routes the message to the target butler via an MCP tool call, prepending a structured identity preamble.
4. The target butler processes the message and returns a response.

### Butler-to-Butler Requests

When a butler needs another butler's capabilities:

1. Butler A invokes an MCP tool that targets the Switchboard.
2. The Switchboard validates the request and routes it to Butler B.
3. Butler B processes the request using its own tools and data.
4. The response flows back through the Switchboard to Butler A.

## The Switchboard's Role

The Switchboard butler is the sole router and mediator:

- **Butler registry:** Maintains a registry of all active butlers with their MCP endpoints, capabilities, domains, and liveness status.
- **Routing:** Uses an LLM-based triage classifier to determine which butler should handle incoming messages.
- **Decomposition:** Can split complex requests across multiple butlers.
- **Request context:** Assigns canonical `request_id` values and tracks the full request lifecycle.
- **Identity preamble:** Resolves sender identity via `resolve_contact_by_channel()` and prepends `[Source: Owner (contact_id: <uuid>), via telegram]` to routed messages.

## What Butlers Cannot Do

To maintain isolation, butlers are explicitly prohibited from:

- **Reading another butler's database tables** -- even if they share the same PostgreSQL server.
- **Calling another butler's MCP tools directly** -- all cross-butler calls go through the Switchboard.
- **Writing to another butler's state store** -- each butler's KV store is schema-isolated.
- **Sharing in-memory state** -- each butler runs as an independent MCP server process.

## Why This Architecture

**Auditability:** Every cross-butler interaction passes through the Switchboard, which logs the full request lifecycle.

**Testability:** Each butler can be tested in isolation with mock MCP clients. No shared state means no test pollution.

**Scalability:** Butlers can be deployed independently. Adding a new butler requires only registering it with the Switchboard.

**Security:** Schema isolation prevents a compromised or buggy butler from corrupting another butler's data.

**Operational simplicity:** Debugging requires only one butler's logs plus the Switchboard routing log.

## Notification Flow

Outbound notifications (Telegram messages, email replies) use the `notify.v1` envelope protocol:

1. Any butler constructs a `notify.v1` payload with channel, recipient, and content.
2. The notification is sent via the Switchboard.
3. The Switchboard routes to the **messenger** butler, which owns delivery execution.
4. The messenger butler dispatches through the appropriate channel adapter.

## The `shared` Schema Exception

The `shared` schema is the only cross-cutting data surface:

- **`shared.contacts`** -- Canonical contact registry (one row per known person/actor, with `roles` array and optional `entity_id` FK).
- **`shared.contact_info`** -- Per-channel identifiers linked to contacts, with UNIQUE on `(type, value)`.

All butlers can read from `shared` for identity resolution. Writes are controlled by specific modules (primarily the contacts module in the relationship butler).

## Related Pages

- [MCP Tools](mcp-tools.md) -- Tool registration and naming
- [Ingestion Envelope](ingestion-envelope.md) -- How events enter the system
- [Dashboard API](dashboard-api.md) -- REST API that exposes butler status and routing logs
