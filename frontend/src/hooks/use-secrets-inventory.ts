/**
 * Fetches the aggregated secrets inventory for the /secrets passport page.
 *
 * Wraps GET /api/secrets/inventory?identity=<uuid> with TanStack Query.
 * The `identity` parameter filters user credentials to a specific entity;
 * when omitted the owner entity is used (projection-lens semantics).
 *
 * The raw API response is adapted to the InventoryResponse shape expected by
 * DirectionPassport. Provider metadata (labels, glyphs, authority) is sourced
 * from the backend providers map returned in the inventory response.
 *
 * Spec anchor: openspec/changes/redesign-secrets-passport/specs/dashboard-api
 * §Inventory endpoint shape
 *
 * [bu-nrgk9]
 */

import { useQuery } from "@tanstack/react-query";

import { getSecretsInventory } from "@/api/client.ts";
import type {
  SecretsAuditEvent,
  SecretsCapabilityStatus,
  SecretsCliRaw,
  SecretsIdentityInfo,
  SecretsProviderInfo,
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
  CredentialFamilyCounts,
  TestResult,
  AuditEvent,
  CapabilityStatus,
} from "@/components/secrets/passport/types.ts";

const STATE_RANK: Record<CredentialState, number> = {
  expired: 0,
  revoked: 1,
  failed: 1,
  scope_mismatch: 2,
  expiring: 3,
  warn: 4,
  rotating: 4,
  ok: 5,
  never_set: 9,
};

// ---------------------------------------------------------------------------
// Adapter helpers
// ---------------------------------------------------------------------------

const USER_TYPE_PROVIDER_ALIASES: Record<string, string> = {
  home_assistant_token: "homeassistant",
  home_assistant_url: "homeassistant",
  telegram_api_hash: "telegram_bot",
  telegram_api_id: "telegram_bot",
  telegram_user_session: "telegram_bot",
};

const USER_TYPE_PREFIX_ALIASES: Record<string, string> = {
  home_assistant: "homeassistant",
  telegram: "telegram_bot",
};

function normalizeProviderId(value: string): string {
  return value.replace(/[^a-z0-9]/gi, "").toLowerCase();
}

function titleFromProviderId(providerId: string): string {
  return providerId
    .split(/[_-]+/)
    .filter(Boolean)
    .map((part) => part.slice(0, 1).toUpperCase() + part.slice(1))
    .join(" ") || "Credential";
}

function genericProvider(providerId: string, type: string): SecretsProviderInfo {
  const label = titleFromProviderId(providerId);
  return {
    id: providerId,
    label,
    glyph: label.slice(0, 1).toUpperCase() || "?",
    kind: "token",
    authority: "credential store",
    brief: `Stored ${type} credential.`,
    cadence: "on demand",
  };
}

function normalizeCredentialState(state: string): CredentialState {
  switch (state) {
    case "ok":
    case "expired":
    case "revoked":
    case "expiring":
    case "scope_mismatch":
    case "warn":
    case "rotating":
    case "never_set":
    case "failed":
      return state;
    case "failing":
      return "failed";
    case "shared":
    case "local":
      return "ok";
    case "missing":
      return "never_set";
    default:
      return "warn";
  }
}

function moreSevereState(a: CredentialState, b: CredentialState): CredentialState {
  return STATE_RANK[b] < STATE_RANK[a] ? b : a;
}

function mergeFingerprints(a: string | null, b: string | null): string | null {
  if (!a) return b;
  if (!b) return a;
  return a === b ? a : null;
}

function rowStateFromSystemRaw(raw: SecretsSystemRaw): SystemCredential["rowState"] {
  if (raw.state === "missing" || raw.state === "never_set") return "missing";
  if (raw.state === "shared" || raw.state === "local") return raw.state;
  // "shared-public" is the public credential pool — treat as shared, not local.
  return raw.butler && !["shared", "switchboard", "shared-public"].includes(raw.butler) ? "local" : "shared";
}

/**
 * Extract the provider slug from an entity_info.type string.
 *
 * Most OAuth-style rows use `<provider>_oauth_refresh`, but several live
 * entity_info rows predate that convention (`home_assistant_token`,
 * `telegram_user_session`). Prefer provider catalog keys and explicit aliases
 * so the passport page always has matching provider metadata.
 */
function extractProvider(type: string, providers: Record<string, SecretsProviderInfo>): string {
  if (providers[type]) return type;

  const exactAlias = USER_TYPE_PROVIDER_ALIASES[type];
  if (exactAlias && providers[exactAlias]) return exactAlias;

  const providerIds = Object.keys(providers);
  const directPrefix = providerIds.find((providerId) => type === providerId || type.startsWith(`${providerId}_`));
  if (directPrefix) return directPrefix;

  for (const [prefix, providerId] of Object.entries(USER_TYPE_PREFIX_ALIASES)) {
    if ((type === prefix || type.startsWith(`${prefix}_`)) && providers[providerId]) {
      return providerId;
    }
  }

  const normalizedType = normalizeProviderId(type);
  const normalizedPrefix = providerIds
    .slice()
    .sort((a, b) => b.length - a.length)
    .find((providerId) => normalizedType.startsWith(normalizeProviderId(providerId)));
  if (normalizedPrefix) return normalizedPrefix;

  const idx = type.indexOf("_");
  return idx > 0 ? type.slice(0, idx) : type;
}

function adaptProbeResult(raw: SecretsCliRaw["test"]): TestResult | null {
  if (!raw) return null;
  return {
    ok: raw.ok,
    code: raw.code ?? null,
    message: raw.message ?? null,
    // Real round-trip latency (bu-6v1hx) when the backend measured one — only
    // probes that make an actual live network call populate this column.
    // Stays null (never a fabricated "0ms") for local-state-derived probes;
    // see ProbeResult's conditional render in atoms.tsx.
    latencyMs: raw.latency_ms ?? null,
    at: raw.at ?? "",
  };
}

/** Map backend audit_log rows to the FE AuditEvent shape (note: null → ""). */
function adaptAuditEvents(raw: SecretsAuditEvent[] | undefined): AuditEvent[] {
  if (!raw) return [];
  return raw.map((event) => ({
    ts: event.ts,
    actor: event.actor,
    action: event.action,
    note: event.note ?? "",
  }));
}

/**
 * Map backend per-capability probe rows to the FE CapabilityStatus shape
 * (bu-4v5es). Absent/empty until the credential has been probed at least
 * once under the new capability-level scheme.
 */
function adaptCapabilityStatuses(
  raw: SecretsCapabilityStatus[] | undefined,
): CapabilityStatus[] {
  if (!raw) return [];
  return raw.map((c) => ({
    capability: c.capability,
    test: adaptProbeResult(c.test),
  }));
}

/** Merge two capability-status lists, keeping the first entry seen per capability. */
function mergeCapabilities(a: CapabilityStatus[], b: CapabilityStatus[]): CapabilityStatus[] {
  const byCapability = new Map<string, CapabilityStatus>();
  for (const status of [...a, ...b]) {
    if (!byCapability.has(status.capability)) byCapability.set(status.capability, status);
  }
  return Array.from(byCapability.values());
}

function adaptUserCredential(raw: SecretsUserRaw, providers: Record<string, SecretsProviderInfo>): UserCredential {
  return {
    provider:       extractProvider(raw.type, providers),
    identity:       raw.entity_id,
    state:          normalizeCredentialState(raw.state),
    fingerprint:    raw.fingerprint ?? null,
    // Real (bu-6v1hx): entity_info.created_at.
    issued:         raw.issued ?? null,
    // Real only for Google test-mode accounts (bu-1lb5j), synthesized
    // server-side from google_accounts.last_token_refresh_at + the known
    // 7-day test-mode lifetime. entity_info has no expires_at column of its
    // own, so every other provider stays honestly null.
    expires:        raw.expires ?? null,
    lastVerified:   raw.last_verified ?? null,
    lastUsed:       null,
    // Real (bu-6v1hx): union of provider_feature_catalogue.required_scopes.
    scopesRequired: raw.scopes_required ?? [],
    // Real for Google only (public.google_accounts.granted_scopes); every
    // other provider has no per-credential granted-scope tracking yet and
    // stays honestly empty.
    scopesGranted:  raw.scopes_granted ?? [],
    feeds:          [],
    test:           adaptProbeResult(raw.test),
    // Real (bu-6v1hx): last few public.audit_log rows for this credential.
    audit:          adaptAuditEvents(raw.audit),
    // Real (bu-4v5es): per-capability probe state.
    capabilities:   adaptCapabilityStatuses(raw.capabilities),
  };
}

function adaptSystemCredential(raw: SecretsSystemRaw): SystemCredential {
  const rowState = rowStateFromSystemRaw(raw);
  // Rows from the public credential pool are tagged butler="shared-public" by
  // the backend.  Their mutation target must be "shared-public" (routes to the
  // public pool) rather than "shared" (routes to the switchboard schema).
  const isSharedPublic = raw.butler === "shared-public";
  const mutationTarget = rowState === "local" ? raw.butler
    : isSharedPublic ? "shared-public"
    : "shared";
  return {
    key:          raw.key,
    category:     raw.category,
    state:        normalizeCredentialState(raw.state),
    rowState,
    fingerprint:  raw.fingerprint ?? null,
    description:  raw.description ?? null,
    source:       rowState === "shared" ? raw.butler : "",
    target:       mutationTarget,
    lastVerified: raw.last_verified ?? null,
    // Real (bu-xzaxm): statically known consumers from the backend's
    // key->consumer map. Empty means "not tracked", never "verified unused"
    // — see SystemCredential.usedBy and the "used by" band's rendering.
    usedBy:       raw.used_by ?? [],
    test:         adaptProbeResult(raw.test),
    // Real (bu-6v1hx): last few public.audit_log rows for this credential.
    audit:        adaptAuditEvents(raw.audit),
    readOnly:     raw.read_only ?? false,
  };
}

function adaptCliCredential(raw: SecretsCliRaw): CliCredential {
  return {
    id:             raw.key,
    label:          raw.description ?? raw.key,
    fingerprint:    raw.fingerprint ?? null,
    state:          normalizeCredentialState(raw.state),
    lastUsed:       null,
    // Real (bu-6v1hx): butler_secrets.created_at / expires_at.
    issued:         raw.issued ?? null,
    expires:        raw.expires ?? null,
    // No real source: CLI runtime tokens have no scope concept in this codebase.
    scopesGranted:  [],
    scopesRequired: [],
    test:           adaptProbeResult(raw.test),
  };
}

function isCliAuthSystemCredential(credential: SystemCredential): boolean {
  return credential.category === "cli-auth" || credential.key.startsWith("cli-auth/");
}

/**
 * Categories whose credentials are owned end-to-end by a provider-config drawer
 * (generate/connect/OAuth), not the generic system-secret editor. Surfacing them
 * as hand-editable system rows is a dead end — e.g. the OwnTracks webhook token
 * is server-generated and write-only, and the Spotify tokens are OAuth runtime
 * artifacts. They are configured via their drawers in the Add → providers flow.
 *
 * Keep this in sync with the drawer roster (DRAWER_PROVIDER_SLUGS in
 * pages.tsx). (Home Assistant, Steam, and WhatsApp credentials are not stored
 * as system secrets, so only the categories that actually appear in
 * butler_secrets are listed.)
 */
const PROVIDER_MANAGED_SYSTEM_CATEGORIES = new Set(["owntracks", "spotify"]);

function isProviderManagedSystemCredential(credential: SystemCredential): boolean {
  return PROVIDER_MANAGED_SYSTEM_CATEGORIES.has(credential.category);
}

function systemCliAuthToCliCredential(credential: SystemCredential): CliCredential {
  return {
    id:             credential.key,
    label:          credential.description ?? credential.key,
    fingerprint:    credential.fingerprint,
    state:          credential.state ?? "ok",
    lastUsed:       null,
    issued:         null,
    expires:        null,
    scopesGranted:  [],
    scopesRequired: [],
    test:           credential.test,
  };
}

function groupCliCredentials(credentials: CliCredential[]): CliCredential[] {
  const grouped = new Map<string, CliCredential>();

  for (const credential of credentials) {
    const existing = grouped.get(credential.id);
    if (!existing) {
      grouped.set(credential.id, credential);
      continue;
    }

    grouped.set(credential.id, {
      ...existing,
      label: existing.label || credential.label,
      fingerprint: mergeFingerprints(existing.fingerprint, credential.fingerprint),
      state: moreSevereState(existing.state, credential.state),
      lastUsed: existing.lastUsed ?? credential.lastUsed,
      issued: existing.issued ?? credential.issued,
      expires: existing.expires ?? credential.expires,
      scopesGranted: Array.from(new Set([...existing.scopesGranted, ...credential.scopesGranted])),
      scopesRequired: Array.from(new Set([...existing.scopesRequired, ...credential.scopesRequired])),
      test: existing.test ?? credential.test,
    });
  }

  return Array.from(grouped.values());
}

function groupUserCredentials(credentials: UserCredential[]): UserCredential[] {
  const grouped = new Map<string, UserCredential>();

  for (const credential of credentials) {
    const key = `${credential.identity}\u0000${credential.provider}`;
    const existing = grouped.get(key);
    if (!existing) {
      grouped.set(key, credential);
      continue;
    }

    grouped.set(key, {
      ...existing,
      state: moreSevereState(existing.state, credential.state),
      fingerprint: mergeFingerprints(existing.fingerprint, credential.fingerprint),
      lastVerified: existing.lastVerified ?? credential.lastVerified,
      lastUsed: existing.lastUsed ?? credential.lastUsed,
      scopesRequired: Array.from(new Set([...existing.scopesRequired, ...credential.scopesRequired])),
      scopesGranted: Array.from(new Set([...existing.scopesGranted, ...credential.scopesGranted])),
      feeds: Array.from(new Set([...existing.feeds, ...credential.feeds])),
      test: existing.test ?? credential.test,
      audit: [...existing.audit, ...credential.audit],
      failureTail: existing.failureTail ?? credential.failureTail,
      webhook: existing.webhook ?? credential.webhook,
      capabilities: mergeCapabilities(
        existing.capabilities ?? [],
        credential.capabilities ?? [],
      ),
    });
  }

  return Array.from(grouped.values());
}

function groupSystemCredentials(credentials: SystemCredential[]): SystemCredential[] {
  const grouped = new Map<string, SystemCredential>();

  for (const credential of credentials) {
    const existing = grouped.get(credential.key);
    if (!existing) {
      grouped.set(credential.key, credential);
      continue;
    }

    const rowState: SystemCredential["rowState"] =
      existing.rowState === "local" || credential.rowState === "local"
        ? "local"
        : existing.rowState === "shared" || credential.rowState === "shared"
          ? "shared"
          : "missing";
    const sharedSource =
      [existing, credential].find((item) => item.rowState === "shared")?.source
      ?? existing.source
      ?? credential.source;
    const localTarget =
      [existing, credential].find((item) => item.rowState === "local")?.target
      ?? existing.target
      ?? credential.target;

    // Determine the mutation target for the merged credential:
    // - local override rows use the butler name as target
    // - shared-public rows keep "shared-public" so mutations route to the
    //   public credential pool (not the switchboard schema)
    // - all other shared rows use "shared" (switchboard schema)
    const mergedTarget = rowState === "local" ? localTarget
      : (existing.target === "shared-public" || credential.target === "shared-public")
        ? "shared-public"
        : "shared";

    grouped.set(credential.key, {
      ...existing,
      category: existing.category || credential.category,
      description: existing.description ?? credential.description,
      state: moreSevereState(existing.state ?? "ok", credential.state ?? "ok"),
      rowState,
      fingerprint: mergeFingerprints(existing.fingerprint, credential.fingerprint),
      source: sharedSource,
      target: mergedTarget,
      lastVerified: existing.lastVerified ?? credential.lastVerified,
      usedBy: Array.from(new Set([...existing.usedBy, ...credential.usedBy])),
      test: existing.test ?? credential.test,
      audit: [...existing.audit, ...credential.audit],
      plainValue: existing.plainValue ?? credential.plainValue,
      // A per-butler override (local row) is editable and wins; otherwise the
      // row is read-only if any contributing source is read-only.
      readOnly:
        rowState === "local"
          ? false
          : (existing.readOnly ?? false) || (credential.readOnly ?? false),
    });
  }

  return Array.from(grouped.values());
}

/**
 * Map backend identity records to the Identity shape expected by DirectionPassport.
 *
 * The inventory endpoint returns an ``identities`` array with real names and
 * roles sourced from ``public.entities``.  We map each entry directly to the
 * frontend Identity shape, falling back to the entity_id when the backend
 * name is absent (should not happen in practice).
 */
function mapIdentities(identitiesRaw: SecretsIdentityInfo[]): Identity[] {
  return identitiesRaw.map((raw) => ({
    id:    raw.entity_id,
    label: raw.name,
    role:  raw.role,
  }));
}

// ---------------------------------------------------------------------------
// Public adapter (exported for test use)
// ---------------------------------------------------------------------------

export function adaptInventoryResponse(data: {
  cli: SecretsCliRaw[];
  system: SecretsSystemRaw[];
  user: SecretsUserRaw[];
  identities: SecretsIdentityInfo[];
  providers?: Record<string, SecretsProviderInfo>;
  failing_count: number;
  unverified_count: number;
  failing_count_by_family: CredentialFamilyCounts;
  unverified_count_by_family: CredentialFamilyCounts;
  /** Threaded from meta.sources_degraded (bu-5ccth); see InventoryResponse.sourcesDegraded. */
  sources_degraded?: string[];
}): InventoryResponse {
  const providers: Record<string, SecretsProviderInfo> = { ...(data.providers ?? {}) };
  const user = data.user.map((raw) => {
    const credential = adaptUserCredential(raw, providers);
    providers[credential.provider] ??= genericProvider(credential.provider, raw.type);
    return credential;
  });
  const system = groupSystemCredentials(data.system.map(adaptSystemCredential));
  const cliFromSystem = system
    .filter(isCliAuthSystemCredential)
    .map(systemCliAuthToCliCredential);
  const identities = mapIdentities(data.identities);
  const ownerEntityId = identities.find((i) => i.role === "owner")?.id;
  return {
    user:            groupUserCredentials(user),
    system:          system.filter(
      (credential) =>
        !isCliAuthSystemCredential(credential) &&
        !isProviderManagedSystemCredential(credential),
    ),
    cli:             groupCliCredentials([
      ...data.cli.map(adaptCliCredential),
      ...cliFromSystem,
    ]),
    identities,
    providers,
    failingCount: data.failing_count,
    unverifiedCount: data.unverified_count,
    failingCountByFamily: data.failing_count_by_family,
    unverifiedCountByFamily: data.unverified_count_by_family,
    ownerEntityId,
    sourcesDegraded: data.sources_degraded ?? [],
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
      // meta.sources_degraded (bu-5ccth) names any backend source dropped
      // from this fan-out rather than failing the whole request — thread it
      // through so SecretsPage can name the missing family inline instead of
      // silently rendering an incomplete inventory as an all-clear.
      return adaptInventoryResponse({
        ...resp.data,
        failing_count: resp.meta.failing_count,
        unverified_count: resp.meta.unverified_count,
        failing_count_by_family: resp.meta.failing_count_by_family,
        unverified_count_by_family: resp.meta.unverified_count_by_family,
        sources_degraded: resp.meta.sources_degraded,
      });
    },
    staleTime: THIRTY_SECONDS_MS,
    refetchInterval: FIVE_MINUTES_MS,
    refetchOnWindowFocus: true,
  });
}
