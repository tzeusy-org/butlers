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

/** Sort mode for the spine. */
export type SpineSortMode = "severity" | "recency" | "alpha";

/** Reveal mode tweak. */
export type RevealMode = "eye" | "hover" | "never";

/** A single spine entry — one row in the left-hand index. */
export interface SpineEntry {
  /** Focus key: `u:<provider>`, `s:<KEY>`, `c:<id>` */
  key: string;
  family: CredentialFamily;
  label: string;
  /** For user entries: provider slug */
  provider?: string;
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
  latencyMs: number;
  at: string;
  message?: string | null;
}

/** A break entry — butler feature that goes silent when credential is sick. */
export interface BreakEntry {
  butler: string;
  feature: string;
  severity: "high" | "medium" | "low";
}

/** User credential (entity_info-based, oauth/token/apikey/webhook). */
export interface UserCredential {
  provider: string;
  identity: string;
  state: CredentialState;
  fingerprint: string | null;
  issued: string | null;
  expires: string | null;
  lastVerified: string | null;
  lastUsed: string | null;
  scopesRequired: string[];
  scopesGranted: string[];
  feeds: string[];
  breaks: BreakEntry[];
  test: TestResult | null;
  audit: AuditEvent[];
  failureTail?: string | null;
  webhook?: string | null;
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
  usedBy: string[];
  breaks: BreakEntry[];
  test: TestResult | null;
  audit: AuditEvent[];
  plainValue?: string | null;
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
  scopesGranted: string[];
  scopesRequired: string[];
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
}
