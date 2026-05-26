/**
 * Fetches the aggregated secrets inventory for the /secrets passport page.
 *
 * Wraps GET /api/secrets/inventory?identity=<uuid> with TanStack Query.
 * The `identity` parameter filters user credentials to a specific entity;
 * when omitted the owner entity is used (projection-lens semantics).
 *
 * The raw API response is adapted to the InventoryResponse shape expected by
 * DirectionPassport. Static provider metadata (labels, glyphs, authority) is
 * sourced from the frontend PROVIDER_CATALOG; the backend does not return it.
 *
 * Spec anchor: openspec/changes/redesign-secrets-passport/specs/dashboard-api
 * §Inventory endpoint shape
 *
 * [bu-nrgk9]
 */

import { useQuery } from "@tanstack/react-query";

import { getSecretsInventory } from "@/api/client.ts";
import type {
  SecretsCliRaw,
  SecretsSystemRaw,
  SecretsUserRaw,
} from "@/api/types.ts";
import type {
  InventoryResponse,
  UserCredential,
  SystemCredential,
  CliCredential,
  Identity,
  CredentialState,
  TestResult,
} from "@/components/secrets/passport/types.ts";

// ---------------------------------------------------------------------------
// Static provider catalog
//
// The inventory endpoint does not return provider metadata (labels, glyphs,
// authority, etc.). This catalog maps provider slugs to their display info.
// Providers not in the catalog fall back to the raw slug as label.
// ---------------------------------------------------------------------------

export const PROVIDER_CATALOG: InventoryResponse["providers"] = {
  google:        { id: "google",        label: "Google",         glyph: "G", kind: "oauth",   authority: "accounts.google.com",  brief: "Calendar, Gmail, Drive read.",    cadence: "on demand · refreshes hourly" },
  spotify:       { id: "spotify",       label: "Spotify",        glyph: "S", kind: "oauth",   authority: "accounts.spotify.com", brief: "Recent listens.",                  cadence: "poll · 15m" },
  homeassistant: { id: "homeassistant", label: "Home Assistant", glyph: "H", kind: "token",   authority: "home.lim.local",       brief: "Smart-home state, sensors.",       cadence: "poll · 30s" },
  whatsapp:      { id: "whatsapp",      label: "WhatsApp",       glyph: "W", kind: "oauth",   authority: "wa.bridge",            brief: "Inbound messages.",                cadence: "webhook + poll · 5m" },
  owntracks:     { id: "owntracks",     label: "OwnTracks",      glyph: "O", kind: "webhook", authority: "self-hosted",          brief: "Location pings via MQTT.",         cadence: "event-driven" },
  steam:         { id: "steam",         label: "Steam",          glyph: "V", kind: "apikey",  authority: "steamcommunity.com",   brief: "Library, playtime.",               cadence: "poll · 6h" },
  telegram_bot:  { id: "telegram_bot",  label: "Telegram Bot",   glyph: "T", kind: "token",   authority: "api.telegram.org",     brief: "Bot inbound + outbound.",          cadence: "webhook + poll · 30s" },
  anthropic:     { id: "anthropic",     label: "Anthropic",      glyph: "A", kind: "apikey",  authority: "api.anthropic.com",    brief: "Claude model calls.",              cadence: "on demand" },
};

// ---------------------------------------------------------------------------
// Adapter helpers
// ---------------------------------------------------------------------------

/**
 * Extract the provider slug from an entity_info.type string.
 * Convention: `<provider>_oauth_refresh`, `<provider>_api_key`, etc.
 * Returns the first underscore-delimited segment.
 */
function extractProvider(type: string): string {
  const idx = type.indexOf("_");
  return idx > 0 ? type.slice(0, idx) : type;
}

function adaptProbeResult(raw: SecretsCliRaw["test"]): TestResult | null {
  if (!raw) return null;
  return {
    ok: raw.ok,
    code: raw.code ?? null,
    message: raw.message ?? null,
    latencyMs: 0,           // not returned in inventory response
    at: raw.at ?? "",
  };
}

function adaptUserCredential(raw: SecretsUserRaw): UserCredential {
  return {
    provider:       extractProvider(raw.type),
    identity:       raw.entity_id,
    state:          raw.state as CredentialState,
    fingerprint:    raw.fingerprint ?? null,
    issued:         null,
    expires:        null,
    lastVerified:   raw.last_verified ?? null,
    lastUsed:       null,
    scopesRequired: [],
    scopesGranted:  [],
    feeds:          [],
    breaks:         [],
    test:           adaptProbeResult(raw.test),
    audit:          [],
  };
}

function adaptSystemCredential(raw: SecretsSystemRaw): SystemCredential {
  return {
    key:          raw.key,
    category:     raw.category,
    rowState:     "shared",   // inventory returns system rows as shared by default
    fingerprint:  raw.fingerprint ?? null,
    description:  raw.description ?? null,
    source:       raw.butler,
    target:       "",
    lastVerified: raw.last_verified ?? null,
    usedBy:       [],
    breaks:       [],
    test:         adaptProbeResult(raw.test),
    audit:        [],
  };
}

function adaptCliCredential(raw: SecretsCliRaw): CliCredential {
  return {
    id:             raw.key,
    label:          raw.description ?? raw.key,
    fingerprint:    raw.fingerprint ?? null,
    state:          raw.state as CredentialState,
    lastUsed:       null,
    issued:         null,
    expires:        null,
    scopesGranted:  [],
    scopesRequired: [],
    test:           adaptProbeResult(raw.test),
  };
}

/**
 * Derive a minimal Identity list from the unique entity_ids in the user array.
 *
 * The inventory endpoint does not return identity metadata (label, role, hue).
 * We construct placeholder entries keyed on entity_id. The identity switcher
 * in DirectionPassport needs at least an id and label to render.
 *
 * The owner identity is assumed to be the first entity_id seen (API orders
 * owner first when no ?identity= param is provided, and filters to a single
 * entity when one is supplied).
 */
function deriveIdentities(userRaw: SecretsUserRaw[]): Identity[] {
  const seen = new Set<string>();
  const identities: Identity[] = [];
  for (const u of userRaw) {
    if (!seen.has(u.entity_id)) {
      seen.add(u.entity_id);
      identities.push({
        id:    u.entity_id,
        label: u.label ?? u.entity_id.slice(0, 8),
        role:  identities.length === 0 ? "owner" : "member",
      });
    }
  }
  return identities;
}

// ---------------------------------------------------------------------------
// Public adapter (exported for test use)
// ---------------------------------------------------------------------------

export function adaptInventoryResponse(data: {
  cli: SecretsCliRaw[];
  system: SecretsSystemRaw[];
  user: SecretsUserRaw[];
}): InventoryResponse {
  return {
    user:       data.user.map(adaptUserCredential),
    system:     data.system.map(adaptSystemCredential),
    cli:        data.cli.map(adaptCliCredential),
    identities: deriveIdentities(data.user),
    providers:  PROVIDER_CATALOG,
  };
}

// ---------------------------------------------------------------------------
// Query keys
// ---------------------------------------------------------------------------

export const secretsInventoryKeys = {
  all: ["secrets", "inventory"] as const,
  byIdentity: (identity: string | null | undefined) =>
    ["secrets", "inventory", identity ?? "owner"] as const,
};

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

const FIVE_MINUTES_MS = 5 * 60 * 1000;
const THIRTY_SECONDS_MS = 30 * 1000;

interface UseSecretsInventoryArgs {
  /** Entity UUID to scope user credentials to. Omit for owner (default). */
  identity?: string | null;
}

export function useSecretsInventory(args: UseSecretsInventoryArgs = {}) {
  const { identity } = args;
  return useQuery<InventoryResponse>({
    queryKey: secretsInventoryKeys.byIdentity(identity),
    queryFn: async () => {
      const resp = await getSecretsInventory(
        identity ? { identity } : undefined,
      );
      return adaptInventoryResponse(resp.data);
    },
    staleTime: THIRTY_SECONDS_MS,
    refetchInterval: FIVE_MINUTES_MS,
    refetchOnWindowFocus: true,
  });
}
