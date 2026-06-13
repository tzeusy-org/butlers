// ---------------------------------------------------------------------------
// Mock inventory data for /secrets passport — used in tests + MSW handlers [bu-qu8v8]
//
// Mirrors the design-canvas sample data from (secrets passport redesign, graduated) secrets-data.jsx.
// Production will replace with `GET /api/secrets/inventory`.
// ---------------------------------------------------------------------------

import type {
  InventoryResponse,
  UserCredential,
  SystemCredential,
  CliCredential,
  Identity,
  ProviderInfo,
} from "./types.ts";

export const MOCK_PROVIDERS: Record<string, ProviderInfo> = {
  google:        { id: "google",        label: "Google",         glyph: "G", kind: "oauth",   authority: "accounts.google.com",  brief: "Calendar, Gmail, Drive read.",     cadence: "on demand · refreshes hourly" },
  spotify:       { id: "spotify",       label: "Spotify",        glyph: "S", kind: "oauth",   authority: "accounts.spotify.com", brief: "Recent listens.",                  cadence: "poll · 15m" },
  homeassistant: { id: "homeassistant", label: "Home Assistant", glyph: "H", kind: "token",   authority: "home.lim.local",       brief: "Smart-home state, sensors.",        cadence: "poll · 30s" },
  whatsapp:      { id: "whatsapp",      label: "WhatsApp",       glyph: "W", kind: "oauth",   authority: "wa.bridge",            brief: "Inbound messages.",                 cadence: "webhook + poll · 5m" },
  owntracks:     { id: "owntracks",     label: "OwnTracks",      glyph: "O", kind: "webhook", authority: "self-hosted",          brief: "Location pings via MQTT.",          cadence: "event-driven" },
  steam:         { id: "steam",         label: "Steam",          glyph: "V", kind: "apikey",  authority: "steamcommunity.com",   brief: "Library, playtime.",                cadence: "poll · 6h" },
  telegram_bot:  { id: "telegram_bot",  label: "Telegram Bot",   glyph: "T", kind: "token",   authority: "api.telegram.org",     brief: "Bot inbound + outbound.",           cadence: "webhook + poll · 30s" },
  anthropic:     { id: "anthropic",     label: "Anthropic",      glyph: "A", kind: "apikey",  authority: "api.anthropic.com",    brief: "Claude model calls.",               cadence: "on demand" },
};

export const MOCK_IDENTITIES: Identity[] = [
  { id: "tze", label: "Tze", role: "owner",  pronoun: "you", hue: "oklch(0.78 0.13 30)" },
  { id: "wei", label: "Wei", role: "member", pronoun: null,  hue: "oklch(0.78 0.13 200)" },
];

export const MOCK_USER_CREDENTIALS: UserCredential[] = [
  {
    provider: "google", identity: "tze", state: "ok",
    fingerprint: "sha256:7a3f9e2c",
    issued: "2026-02-14", expires: null,
    lastVerified: "14:21 today", lastUsed: "14:18 today",
    scopesRequired: ["calendar.readonly", "gmail.readonly", "drive.metadata.readonly"],
    scopesGranted:  ["calendar.readonly", "gmail.readonly", "drive.metadata.readonly"],
    feeds: ["calendar", "chronicler"],
    breaks: [
      { butler: "calendar",     feature: "calendar events read", severity: "high"   },
      { butler: "relationship", feature: "gmail thread scan",    severity: "high"   },
      { butler: "chronicler",   feature: "drive recent index",   severity: "medium" },
    ],
    test: { ok: true, code: 200, latencyMs: 42, at: "14:21 today" },
    audit: [
      { ts: "2026-05-23 14:21", actor: "system", action: "verified",  note: "200 OK · 42ms" },
      { ts: "2026-05-21 09:04", actor: "tze",    action: "rotated",   note: "refresh-token rolled" },
      { ts: "2026-02-14 18:30", actor: "tze",    action: "connected", note: "oauth dance · 3 scopes granted" },
    ],
  },
  {
    provider: "spotify", identity: "tze", state: "expired",
    fingerprint: "sha256:d4e1b8a0",
    issued: "2025-11-03", expires: "2026-05-20",
    lastVerified: "2 days ago", lastUsed: "2 days ago",
    scopesRequired: ["user-read-recently-played"],
    scopesGranted:  ["user-read-recently-played"],
    feeds: ["chronicler"],
    failureTail: "401 invalid_grant · refresh-token expired",
    breaks: [
      { butler: "chronicler", feature: "spotify · daily listens", severity: "medium" },
    ],
    test: { ok: false, code: 401, latencyMs: 134, at: "2 days ago", message: "refresh-token expired" },
    audit: [
      { ts: "2026-05-21 06:08", actor: "system", action: "failed",    note: "401 · refresh failed · marked expired" },
      { ts: "2025-11-03 22:14", actor: "tze",    action: "connected", note: "oauth dance · 1 scope" },
    ],
  },
  {
    provider: "homeassistant", identity: "tze", state: "expiring",
    fingerprint: "sha256:0c2a47f5",
    issued: "2025-05-27", expires: "2026-05-27",
    lastVerified: "14:00 today", lastUsed: "14:00 today",
    scopesRequired: ["states.read", "events.fire"],
    scopesGranted:  ["states.read", "events.fire"],
    feeds: ["household", "calendar"],
    breaks: [
      { butler: "household", feature: "rooms · presence",   severity: "high"   },
      { butler: "household", feature: "thermostat · climate", severity: "medium" },
    ],
    test: { ok: true, code: 200, latencyMs: 18, at: "14:00 today" },
    audit: [
      { ts: "2026-05-23 14:00", actor: "system", action: "verified",  note: "200 OK · 18ms" },
      { ts: "2026-05-22 09:00", actor: "system", action: "warned",    note: "token expires in 5 days" },
    ],
  },
  {
    provider: "whatsapp", identity: "tze", state: "scope_mismatch",
    fingerprint: "sha256:91e7c4b2",
    issued: "2026-04-08", expires: null,
    lastVerified: "13:58 today", lastUsed: "13:55 today",
    scopesRequired: ["messages.read", "messages.send", "contacts.read"],
    scopesGranted:  ["messages.read", "messages.send"],
    feeds: ["relationship"],
    breaks: [
      { butler: "relationship", feature: "contact disambiguation", severity: "high"   },
      { butler: "relationship", feature: "group lookup",           severity: "medium" },
    ],
    test: { ok: true, code: 200, latencyMs: 73, at: "13:58 today", message: "scope set incomplete" },
    audit: [
      { ts: "2026-05-19 11:12", actor: "system", action: "warned",    note: "contacts.read newly required" },
      { ts: "2026-04-08 17:20", actor: "tze",    action: "connected", note: "oauth dance · 2 scopes granted" },
    ],
  },
  {
    provider: "owntracks", identity: "tze", state: "ok",
    fingerprint: "sha256:b3d9106c",
    issued: "2025-08-12", expires: null,
    lastVerified: "14:19 today", lastUsed: "14:19 today",
    scopesRequired: ["webhook.post"],
    scopesGranted:  ["webhook.post"],
    feeds: ["chronicler", "household"],
    webhook: "https://butlers.tze/ingest/owntracks",
    breaks: [
      { butler: "chronicler", feature: "location stream",    severity: "medium" },
      { butler: "household",  feature: "arrivals · departures", severity: "medium" },
    ],
    test: { ok: true, code: 200, latencyMs: 8, at: "14:19 today" },
    audit: [
      { ts: "2026-05-23 14:19", actor: "system", action: "verified",  note: "200 OK · 8ms" },
      { ts: "2025-08-12 20:00", actor: "tze",    action: "connected", note: "webhook token issued" },
    ],
  },
  {
    provider: "steam", identity: "tze", state: "never_set",
    fingerprint: null, issued: null, expires: null,
    lastVerified: null, lastUsed: null,
    scopesRequired: ["publisher.read"], scopesGranted: [],
    feeds: ["chronicler"], breaks: [], test: null, audit: [],
  },
  // Household member — Wei
  {
    provider: "google", identity: "wei", state: "ok",
    fingerprint: "sha256:2f8e0a17",
    issued: "2026-03-02", expires: null,
    lastVerified: "13:51 today", lastUsed: "13:51 today",
    scopesRequired: ["calendar.readonly"], scopesGranted: ["calendar.readonly"],
    feeds: ["calendar"],
    breaks: [
      { butler: "calendar", feature: "wei · busy/free", severity: "medium" },
    ],
    test: { ok: true, code: 200, latencyMs: 51, at: "13:51 today" },
    audit: [
      { ts: "2026-05-23 13:51", actor: "system", action: "verified", note: "200 OK · 51ms" },
    ],
  },
];

export const MOCK_SYSTEM_CREDENTIALS: SystemCredential[] = [
  {
    key: "BUTLER_TELEGRAM_TOKEN", category: "telegram", rowState: "shared",
    fingerprint: "sha256:5e9c1f2a",
    description: "Bot API token for system-wide Telegram I/O.",
    source: "shared", target: "shared", lastVerified: "14:20 today",
    usedBy: ["switchboard", "relationship", "qa"],
    breaks: [
      { butler: "switchboard", feature: "inbound telegram",  severity: "high" },
      { butler: "switchboard", feature: "outbound replies",  severity: "high" },
    ],
    test: { ok: true, code: 200, latencyMs: 41, at: "14:20 today" },
    audit: [
      { ts: "2026-05-23 14:20", actor: "system", action: "verified", note: "getMe · 41ms" },
      { ts: "2025-09-14 11:05", actor: "tze",    action: "rotated",  note: "token replaced" },
    ],
  },
  {
    key: "ANTHROPIC_API_KEY", category: "core", rowState: "shared",
    fingerprint: "sha256:c4a872f0",
    description: "Claude API key. Used by every butler that talks to a model.",
    source: "shared", target: "shared", lastVerified: "14:14 today",
    usedBy: ["*"],
    breaks: [{ butler: "*", feature: "all model calls", severity: "high" }],
    test: { ok: true, code: 200, latencyMs: 220, at: "14:14 today" },
    audit: [
      { ts: "2026-05-23 14:14", actor: "system", action: "verified", note: "1-token probe · 220ms" },
      { ts: "2026-05-01 10:00", actor: "tze",    action: "rotated",  note: "monthly rotation" },
    ],
  },
  {
    key: "OWNTRACKS_WEBHOOK_TOKEN", category: "home_assistant", rowState: "missing",
    fingerprint: null,
    description: "Bearer token OwnTracks uses to authenticate webhook posts.",
    source: "", target: "shared", lastVerified: null,
    usedBy: ["chronicler"], breaks: [], test: null, audit: [],
  },
  {
    key: "GMAIL_SENDER_ADDRESS", category: "email", rowState: "shared",
    fingerprint: null,
    description: "Sender address used by butlers when emailing on the owner's behalf.",
    source: "shared", target: "shared", lastVerified: "14:01 today",
    usedBy: ["relationship"],
    plainValue: "tze@lim.house",
    breaks: [], test: null,
    audit: [
      { ts: "2026-04-22 16:00", actor: "tze", action: "set", note: "changed from butlers@…" },
    ],
  },
];

export const MOCK_CLI_CREDENTIALS: CliCredential[] = [
  {
    id: "claude-cli", label: "Claude Code", state: "ok",
    fingerprint: "sha256:11a47cd2",
    issued: "2026-02-10", expires: null, lastUsed: "14:15 today",
    scopesGranted: ["repo.write", "session.run"], scopesRequired: ["repo.write", "session.run"],
    test: { ok: true, code: 200, latencyMs: 95, at: "14:15 today" },
  },
  {
    id: "codex-cli", label: "Codex CLI", state: "expiring",
    fingerprint: "sha256:9f0a3b71",
    issued: "2025-11-29", expires: "2026-05-29", lastUsed: "4d ago",
    scopesGranted: ["repo.write"], scopesRequired: ["repo.write"],
    test: { ok: true, code: 200, latencyMs: 110, at: "4d ago" },
  },
  {
    id: "gemini-cli", label: "Gemini CLI", state: "never_set",
    fingerprint: null, issued: null, expires: null, lastUsed: null,
    scopesGranted: [], scopesRequired: ["repo.write"],
    test: null,
  },
];

export const MOCK_INVENTORY: InventoryResponse = {
  user: MOCK_USER_CREDENTIALS,
  system: MOCK_SYSTEM_CREDENTIALS,
  cli: MOCK_CLI_CREDENTIALS,
  identities: MOCK_IDENTITIES,
  providers: MOCK_PROVIDERS,
};
