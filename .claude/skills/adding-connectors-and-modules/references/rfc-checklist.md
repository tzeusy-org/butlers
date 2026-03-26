# RFC Amendment Checklist

Before writing specs for a new integration, check each RFC for required amendments.

## RFC 0003 — Switchboard Routing and Ingestion

**File:** `about/law-and-lore/rfcs/0003-switchboard-routing-and-ingestion.md`

Check:
- [ ] Is your `source.channel` value in the `SourceChannel` enum? (e.g., `telegram`, `email`, `gaming`)
- [ ] Is your `source.provider` value in the `SourceProvider` enum? (e.g., `gmail`, `steam`, `spotify`)
- [ ] Is the channel/provider pairing in the validation matrix?

If any are missing, create a task to amend RFC 0003 before implementation.

**Existing channel/provider pairs:** `telegram/telegram`, `email/gmail`, `email/imap`, `api/internal`, `mcp/internal`, `voice/live-listener`, `google_calendar/google_calendar`, `dashboard/internal`, `owntracks/owntracks`

## RFC 0004 — Identity and Contact Resolution

**File:** `about/law-and-lore/rfcs/0004-identity-and-contact-resolution.md`

Check:
- [ ] Do you need new `entity_info.type` values for credential storage? (e.g., `steam_api_key`, `spotify_refresh_token`)
- [ ] Do companion entities need a new role? (e.g., `steam_account`, `spotify_account`)

## RFC 0002 — MCP Tool Surface and Modules

**File:** `about/law-and-lore/rfcs/0002-mcp-tool-surface-and-modules.md`

Check:
- [ ] Does your module follow the `Module` ABC contract?
- [ ] Are new tool names following the `<service>_<action>` convention?

## RFC 0006 — Database Schema and Isolation

**File:** `about/law-and-lore/rfcs/0006-database-schema-and-isolation.md`

Check:
- [ ] Cross-butler tables go in `public` schema (not `shared` — it's been migrated)
- [ ] Connector-owned tables (cursors, history) go in `connectors` schema
- [ ] Butler-specific tables go in the butler's own schema
- [ ] Always use fully qualified `public.tablename` in SQL — never rely on search_path for cross-butler tables
