---
name: adding-connectors-and-modules
description: Guide for adding new external service connectors and modules to the Butlers framework. Covers the full integration pattern — account registry, module (MCP tools), connector (background ingestion), and dashboard API. Use when planning, speccing, or implementing a new external service integration (e.g., Steam, Spotify, Discord, WhatsApp). Triggers on "add a connector", "new module", "integrate with", "new external service", "connector spec", "module spec".
---

# Adding Connectors & Modules

A new external service integration in Butlers follows a four-component pattern. Not every integration needs all four, but consider each.

| Component | Purpose | Code location | Spec location |
|---|---|---|---|
| **Account registry** | `public.<service>_accounts` table + companion entities | Alembic migration | `openspec/specs/<service>-account-registry/` |
| **Module** | MCP tools for butler LLM sessions | `src/butlers/modules/<service>.py` | `openspec/specs/module-<service>/` |
| **Connector** | Background polling/push process → ingest.v1 → Switchboard | `src/butlers/connectors/<service>.py` | `openspec/specs/connector-<service>/` |
| **Dashboard** | REST API + UI for account management and analytics | `roster/*/api/` or shared API routes | `openspec/specs/dashboard-<service>/` |

## Decision: What Do You Need?

- **Read-only data query** → Module only (no connector)
- **Background activity ingestion** → Connector + module + account registry
- **User-facing account management** → Dashboard + account registry
- **Multi-account support** → Account registry (always entity-tied)

## Workflow

### Phase 0: Research

Before writing specs, gather:

1. **Auth model** — OAuth, API key, token, or other? Determines account registry and dashboard connect flow.
2. **Push vs poll** — Does the service have webhooks/push? Or polling only? Determines connector architecture.
3. **Read vs read-write** — Can you write data back? Determines module tool surface.
4. **Rate limits** — Documented? Undocumented? Affects connector poll intervals.
5. **Privacy model** — Is data always available, or dependent on user/target settings?

### Phase 1: Prerequisites (RFC Amendments)

Check these before writing any spec. See [references/rfc-checklist.md](references/rfc-checklist.md).

1. **RFC 0003** — Register new `SourceChannel`/`SourceProvider` enum values and the channel/provider pairing
2. **RFC 0004** — Register new `entity_info.type` values for credential storage
3. **Schema** — All cross-butler tables use `public.*` (not `shared.*`). Connector-owned tables go in `connectors.*` schema.

### Phase 2: Account Registry Spec

Follow the `public.google_accounts` / `public.steam_accounts` pattern. See [references/account-registry-pattern.md](references/account-registry-pattern.md).

Key requirements:
- Companion entity with role `'<service>_account'` in `public.entities`
- Credentials stored as secured `entity_info` (type `<service>_api_key` or `<service>_refresh_token`)
- `is_primary` with singleton partial unique index
- Status lifecycle: `active` / `suspended` / `revoked`
- Lookup by external ID, UUID, or primary (fallback)
- API key/token validation on connect

### Phase 3: Module Spec

Implement `Module` ABC from `src/butlers/modules/base.py`. See [references/module-pattern.md](references/module-pattern.md).

Key requirements:
- `register_tools()` — MCP tools for the service
- `config_schema` — Pydantic model for `[modules.<service>]` in `butler.toml`
- `on_startup()` — Resolve credentials from account registry, degrade gracefully if no account
- Default to owner's primary account when ID parameters are omitted
- Privacy-aware errors with actionable `hint` fields
- `tool_metadata()` marking credentials as sensitive

### Phase 4: Connector Spec

Implement the connector base contract from `openspec/specs/connector-base-spec/spec.md`. **This is the most complex component.** See [references/connector-base-obligations.md](references/connector-base-obligations.md) for the full checklist.

Critical obligations that are easy to miss:
1. Filtered event batch flush to `connectors.filtered_events`
2. Replay queue drain loop (10 per cycle, `FOR UPDATE SKIP LOCKED`)
3. Source filter gate via `IngestionPolicyEvaluator`
4. Heartbeat protocol (`connector.heartbeat.v1`)
5. Prometheus metrics (service-specific counters)
6. `control.idempotency_key`, `control.policy_tier`, `control.ingestion_tier` on all envelopes
7. Per-account error isolation and independent backoff

### Phase 5: Dashboard Spec

Follow `dashboard-google-accounts` / `dashboard-spotify-setup` patterns:
- Account CRUD endpoints (`GET/POST/DELETE /api/<service>/accounts`)
- Credential validation on connect
- Primary account management
- Connector health proxy
- Optional: analytics/activity endpoints

### Phase 6: Heart-and-Soul Alignment Check

Before finalizing, verify:
- [ ] All seven non-negotiable rules pass (vision.md)
- [ ] v1 scope — is this in v1 or post-v1? Note it in the proposal.
- [ ] Modules only add tools — never touch core infrastructure
- [ ] Connector handles transport — butlers never see transport details
- [ ] Inter-butler communication remains MCP-only via Switchboard

### Phase 7: OpenSpec Change

Use `/opsx:ff` to fast-forward artifact creation:
```
/opsx:ff <service>-integration
```

This creates: proposal → design → specs (one per capability) → tasks.

## Common Gotchas

- **`shared.*` is dead** — Use `public.*` for all cross-butler tables. Always use fully qualified `public.tablename` in SQL to avoid shadowing (e.g., `general.entities` shadows `public.entities` via search_path).
- **Connector base contract** — It's not enough to just poll and submit. You must also flush filtered events, drain the replay queue, send heartbeats, export metrics, and support source filters.
- **ingest.v1 is strict** — Every envelope needs `source.channel`, `source.provider`, `source.endpoint_identity`, `event.external_event_id`, `event.observed_at`, `sender.identity`, `payload.raw`, `payload.normalized_text`, and `control.*` fields.
- **Privacy is not an error** — If a service returns empty data due to privacy settings, skip event emission and advance the cursor. Do not retry.
- **First poll baseline** — For delta-detection connectors, the first poll establishes baseline state without emitting events. Otherwise you flood the system with "historical" events for existing data.
