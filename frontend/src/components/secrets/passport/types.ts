// ---------------------------------------------------------------------------
// Passport types — shared across all passport-book components [bu-qu8v8]
// ---------------------------------------------------------------------------

/** Credential state as returned by the API. */
export type CredentialState =
  | "ok"
  | "expired"
  | "revoked"
  | "expiring"
  | "scope_mismatch"
  | "warn"
  | "rotating"
  | "never_set"
  | "failed";

/** Credential family. */
export type CredentialFamily = "user" | "system" | "cli";

/** Deduplicated failing/unverified counts supplied by the inventory backend. */
export type CredentialFamilyCounts = Record<CredentialFamily, number>;

/** Sort mode for the spine. */
export type SpineSortMode = "severity" | "recency" | "alpha";

/** A single spine entry — one row in the left-hand index. */
export interface SpineEntry {
  /** Focus key: `u:<provider>`, `s:<KEY>`, `c:<id>` */
  key: string;
  family: CredentialFamily;
  label: string;
  /** For user entries: provider slug */
  provider?: string;
  /**
   * For user entries: the owning identity id. Two identities can hold a
   * credential on the same provider (owner-default projection), so `key`
   * (`u:<provider>`, a provider-level focus deep-link target) is NOT unique on
   * its own — `identity` disambiguates the React key for those siblings
   * (bu-ffjig) without changing the focus/selection contract.
   */
  identity?: string;
  state: CredentialState;
  /** Render label in mono (system keys). */
  mono: boolean;
  /** Secondary line in the row (state detail). */
  subline: string;
  /** Sort order for recency mode. Lower = more recent. */
  lastTouchOrder: number;
}

/** State metadata for display. */
export interface StateMeta {
  label: string;
  tone: "ok" | "amber" | "red" | "dim";
  sliver: boolean;
  rank: number;
}

/** Audit event. */
export interface AuditEvent {
  ts: string;
  actor: string;
  action: string;
  note: string;
}

/** Probe test result. */
export interface TestResult {
  ok: boolean;
  code?: number | null;
  /**
   * Probe round-trip latency in milliseconds, when the server reports one.
   * Populated (bu-6v1hx) only for probes that made a real live network call
   * (currently the user-credential probe's live OAuth/PAT verify) —
   * render nothing (never a fabricated "0ms") when null.
   */
  latencyMs?: number | null;
  at: string;
  message?: string | null;
}

/**
 * Latest probe result for one capability family of a user credential
 * (bu-4v5es) — e.g. 'calendar' | 'gmail' | 'drive' | 'health' for Google,
 * 'connectivity' for every other provider's single live-verify call.
 */
export interface CapabilityStatus {
  capability: string;
  test: TestResult | null;
}

/**
 * User credential (entity_info-based, oauth/token/apikey/webhook).
 *
 * Fed by the content-blind `user` array of GET /api/secrets/inventory
 * (bu-iph56). The raw entity_info type, the credential label, raw OAuth scope
 * identifiers, probe messages, and audit note free text are not on that wire
 * and cannot be added back here — the passport shows capability categories
 * from the backend's fixed vocabulary instead.
 */
export interface UserCredential {
  provider: string;
  identity: string;
  state: CredentialState;
  fingerprint: string | null;
  issued: string | null;
  expires: string | null;
  lastVerified: string | null;
  lastUsed: string | null;
  /**
   * Capability categories this credential's provider needs, and the ones it
   * actually has. Members of the backend's CAPABILITY_VOCABULARY —
   * 'calendar' | 'gmail' | 'drive' | 'health' | 'connectivity' | 'other' —
   * never raw scope strings. Empty means "nothing recorded", not "unknown".
   */
  capabilitiesRequired: string[];
  capabilitiesGranted: string[];
  feeds: string[];
  test: TestResult | null;
  audit: AuditEvent[];
  failureTail?: string | null;
  webhook?: string | null;
  /**
   * Per-capability probe state (bu-4v5es). Optional/empty until the
   * credential has been probed at least once under the capability scheme —
   * absent in older mock fixtures and pre-bu-4v5es data.
   */
  capabilities?: CapabilityStatus[];
}

/** System credential (butler_secrets-based). */
export interface SystemCredential {
  key: string;
  category: string;
  state?: CredentialState;
  rowState: "shared" | "local" | "missing";
  fingerprint: string | null;
  description: string | null;
  source: string;
  target: string;
  lastVerified: string | null;
  /**
   * Statically known consumers of this key (bu-xzaxm) — e.g. ["email"] for
   * BUTLER_EMAIL_ADDRESS. Empty means "no known consumer in the backend's
   * static map", NEVER "verified nobody depends on this credential" — the
   * "used by" band must render that distinction as "usage not tracked",
   * not a confident "nobody yet".
   */
  usedBy: string[];
  test: TestResult | null;
  audit: AuditEvent[];
  plainValue?: string | null;
  /**
   * When true, the generic editor is suppressed (read-only row).
   * Shared-public rows (butler="shared-public") are NOT flagged read_only —
   * they use target="shared-public" which routes to the correct pool.
   * Reserved for externally-managed or future restricted rows.
   */
  readOnly?: boolean;
}

/** CLI runtime credential. */
export interface CliCredential {
  id: string;
  label: string;
  fingerprint: string | null;
  state: CredentialState;
  lastUsed: string | null;
  issued: string | null;
  expires: string | null;
  // No capability evidence: nothing in this system records a scope or
  // capability for a CLI runtime token (bu-v8mlr). butler_secrets has no
  // scope column, cli_auth has no scope concept, and CliRuntimeSummary — the
  // inventory row these are adapted from — carries no capability field.
  test: TestResult | null;
}

/** Provider info (for display). */
export interface ProviderInfo {
  id: string;
  label: string;
  glyph: string;
  kind: "oauth" | "token" | "apikey" | "webhook";
  authority: string;
  brief: string;
  cadence: string;
}

/** Identity (owner or household member). */
export interface Identity {
  id: string;
  label: string;
  role: string;
  pronoun?: string | null;
  hue?: string;
}

/** Inventory response shape (mocked for B3). */
export interface InventoryResponse {
  user: UserCredential[];
  system: SystemCredential[];
  cli: CliCredential[];
  identities: Identity[];
  providers: Record<string, ProviderInfo>;
  /** Aggregate failing count from the backend's deduplicated credential set. */
  failingCount: number;
  /** Aggregate unverified count from the backend's deduplicated credential set. */
  unverifiedCount: number;
  /** Per-family failing counts from that same backend deduplication. */
  failingCountByFamily: CredentialFamilyCounts;
  /** Per-family unverified counts from that same backend deduplication. */
  unverifiedCountByFamily: CredentialFamilyCounts;
  /**
   * The owner entity UUID — used by the add-credential flow (bu-ayp6v.6)
   * to POST entity_info rows. Populated from identities[role==="owner"].id.
   * May be undefined in older mock data; create-user flow degrades gracefully.
   */
  ownerEntityId?: string;
  /**
   * Named backend sources that failed during this fan-out and were dropped
   * from the response (bu-5ccth). Threaded from meta.sources_degraded so
   * SecretsPage can name the missing family instead of failing the whole
   * page. Empty/undefined means nothing was degraded.
   */
  sourcesDegraded?: string[];
}
