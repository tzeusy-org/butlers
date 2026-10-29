## Why

The owner decided on 2026-10-04 (bu-lsxqb0.4, option A; applied to doctrine in PR #4343) that
Discord is deferred and the draft connector retires (`about/heart-and-soul/v1.md`). The code,
the canonical `connector-discord` spec and the dashboard catalog rows still shipped, so the
dashboard offered a connector that doctrine says does not exist. A return of Discord goes
through a new proposal (RFC 0018, ideas ledger).

## What Changes

- Remove the `connector-discord` capability: both requirements ("[AS-BUILT] Shipped Bot-Token
  Gateway Connector", "[TARGET-STATE] Discord Connector Scope") and the spec directory.
- Remove `src/butlers/connectors/discord_user.py`, its console script, its test file, the
  `discord_user` row in the connector catalog, the Discord OAuth scope manifest and connector
  auth entries, the bearer-scrub fallback labels, the Switchboard `discord` channel/provider
  pair and the `discord` channel default.
- Keep stored-data readers: the chronicler channel label map, the relationship
  `prefers_channel` vocabulary and migration 022, and historical `discord` rows stay readable.

No migration, data change or deployment change: `discord_user` is in neither Helm nor compose.
Restoration is by PR revert.
