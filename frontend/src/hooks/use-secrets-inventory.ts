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
  SecretsCliRaw,
  SecretsCredentialAuditOutcome,
  SecretsCredentialCapabilityOutcome,
  SecretsCredentialTestOutcome,
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

function titleFromProviderId(providerId: string): string {
  return providerId
    .split(/[_-]+/)
    .filter(Boolean)
    .map((part) => part.slice(0, 1).toUpperCase() + part.slice(1))
    .join(" ") || "Credential";
}

/**
 * Placeholder metadata for a provider slug the backend catalog does not carry.
 *
 * The brief is deliberately generic: the inventory no longer publishes the raw
 * entity_info type this row came from (bu-iph56), so there is nothing specific
 * to say about the credential beyond the slug the backend chose to publish.
 */
function genericProvider(providerId: string): SecretsProviderInfo {
  const label = titleFromProviderId(providerId);
  return {
    id: providerId,
    label,
    glyph: label.slice(0, 1).toUpperCase() || "?",
    kind: "token",
    authority: "credential store",
    brief: "Stored credential.",
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
 * Map a content-blind probe outcome (bu-iph56) to the FE TestResult shape.
 *
 * `message` is pinned to null rather than threaded: no inventory family has a
 * probe message on the wire any more, by design. `latencyMs` stays null
 * (never a fabricated "0ms") for probes the backend never timed; see
 * ProbeResult's conditional render in atoms.tsx.
 */
function adaptTestOutcome(raw: SecretsCredentialTestOutcome | null): TestResult | null {
  if (!raw) return null;
  return {
    ok: raw.ok,
    code: raw.code ?? null,
    message: null,
    latencyMs: raw.latency_ms ?? null,
    at: raw.at ?? "",
  };
}

/**
 * Map content-blind audit rows (bu-iph56) to the FE AuditEvent shape.
 *
 * `note` is pinned to "" because the backend drops it on read for every
 * writer of the `u:` and `s:` audit namespaces — there is no note to render,
 * and this must not be "fixed" by reaching for a note field that is not on
 * the wire.
 */
function adaptAuditOutcomes(raw: SecretsCredentialAuditOutcome[] | undefined): AuditEvent[] {
  if (!raw) return [];
  return raw.map((event) => ({
    ts: event.ts,
    actor: event.actor,
    action: event.action,
    note: "",
  }));
}

/** Map content-blind per-capability probe rows (bu-iph56) to CapabilityStatus. */
function adaptCapabilityOutcomes(
  raw: SecretsCredentialCapabilityOutcome[] | undefined,
): CapabilityStatus[] {
  if (!raw) return [];
  return raw.map((c) => ({
    capability: c.capability,
    test: adaptTestOutcome(c.test),
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

/**
 * Adapt one content-blind inventory row (bu-iph56).
 *
 * `provider` is taken straight from the wire — the backend clamps it to its
 * own USER_PROVIDER_VOCABULARY, so there is no entity_info type left here to
 * re-derive it from and no client-side guess to make.
 */
function adaptUserCredential(raw: SecretsUserRaw): UserCredential {
  return {
    provider:       raw.provider,
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
    // Real (bu-6v1hx, categorised bu-iph56): provider_feature_catalogue
    // required_scopes, mapped server-side onto CAPABILITY_VOCABULARY.
    capabilitiesRequired: raw.capabilities_required ?? [],
    // Real for Google only (public.google_accounts.granted_scopes); every
    // other provider has no per-credential granted-scope tracking yet and
    // stays honestly empty.
    capabilitiesGranted:  raw.capabilities_granted ?? [],
    feeds:          [],
    test:           adaptTestOutcome(raw.test),
    // Real (bu-6v1hx): last few public.audit_log rows for this credential,
    // without their notes.
    audit:          adaptAuditOutcomes(raw.audit),
    // Real (bu-4v5es): per-capability probe state.
    capabilities:   adaptCapabilityOutcomes(raw.capabilities),
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
    test:         adaptTestOutcome(raw.test),
    // Real (bu-6v1hx): last few public.audit_log rows for this credential.
    audit:        adaptAuditOutcomes(raw.audit),
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
    test:           adaptTestOutcome(raw.test),
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
      capabilitiesRequired: Array.from(
        new Set([...existing.capabilitiesRequired, ...credential.capabilitiesRequired]),
      ),
      capabilitiesGranted: Array.from(
        new Set([...existing.capabilitiesGranted, ...credential.capabilitiesGranted]),
      ),
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
    const credential = adaptUserCredential(raw);
    providers[credential.provider] ??= genericProvider(credential.provider);
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
