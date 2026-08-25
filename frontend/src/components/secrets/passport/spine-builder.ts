// ---------------------------------------------------------------------------
// Spine entry builder — projects inventory data into flat SpineEntry list [bu-qu8v8]
// ---------------------------------------------------------------------------

import type { SpineEntry, InventoryResponse } from "./types.ts";
import { severityRank } from "./constants.ts";

/**
 * Real missing-capability count for a scope_mismatch credential, or a plain
 * label when the required/granted arrays aren't populated for this provider
 * (bu-6v1hx: most providers besides Google have no granted-scope tracking
 * yet — never fabricate a count in that case).
 *
 * Counts capability categories, not scopes (bu-iph56): the inventory publishes
 * 'calendar' / 'gmail' / 'drive' / 'health' / 'connectivity' / 'other', so a
 * provider missing three calendar scopes reads as one missing capability. The
 * subline says "capability" for that reason — do not relabel it "scope".
 */
function capabilityMismatchSubline(
  capabilitiesRequired: string[],
  capabilitiesGranted: string[],
): string {
  if (capabilitiesRequired.length === 0) return "scope mismatch";
  const granted = new Set(capabilitiesGranted);
  const missing = capabilitiesRequired.filter((c) => !granted.has(c)).length;
  if (missing === 0) return "scope mismatch";
  return `${missing} capabilit${missing === 1 ? "y" : "ies"} missing`;
}

/**
 * Build the flat list of spine entries from inventory data.
 *
 * When ``identityId`` is an array with more than one entry (owner-default
 * projection), ALL matching user credentials are included.  The backend
 * already gates the owner-default response to owner-relevant companion
 * entities (primary Google account only), so every identity returned is
 * intentional.
 *
 * When ``identityId`` is a single string (explicit ?identity= param or a chip
 * click), only that identity's credentials are included — this preserves the
 * per-member projection-lens contract.
 *
 * Both ``string`` and ``string[]`` are accepted via the union type so callers
 * can pass a single ID or the full identity list without casting.
 */
export function buildSpineEntries(
  inventory: InventoryResponse,
  identityId: string | string[],
): SpineEntry[] {
  const identityIds = Array.isArray(identityId) ? identityId : [identityId];
  const identitySet = new Set(identityIds);
  const userSecrets = inventory.user.filter((s) => identitySet.has(s.identity));

  const cli: SpineEntry[] = inventory.cli.map((r, i) => ({
    key: `c:${r.id}`,
    family: "cli" as const,
    label: r.label,
    state: r.state,
    mono: false,
    lastTouchOrder:
      r.state === "never_set" ? 900 : r.test ? i : 500,
    // bu-hd1vs: this used to end at `used ${r.lastUsed ?? "—"}`. Nothing has
    // ever persisted a per-credential usage time, so every healthy CLI row
    // read "used —" in production — which states that usage IS tracked and
    // there is none. The probe timestamp is the real last-touch signal these
    // rows have (it already drives lastTouchOrder above), so say that instead.
    subline:
      r.state === "never_set"
        ? "not set"
        : r.state === "warn"
          ? "unverified"
        : r.state === "expiring" && r.expires
          ? `expires ${r.expires}`
          : r.test
            ? `verified ${r.test.at}`
            : "not probed",
  }));

  const system: SpineEntry[] = inventory.system.map((s, i) => ({
    key: `s:${s.key}`,
    family: "system" as const,
    label: s.key,
    state: s.rowState === "missing" ? "never_set" : (s.state ?? "ok"),
    mono: true,
    lastTouchOrder: s.rowState === "missing" ? 900 : i,
    subline:
      s.rowState === "missing"
        ? "not set"
        : s.rowState === "local"
          ? `local · ${s.target}`
          : "shared default",
  }));

  const user: SpineEntry[] = userSecrets.map((s) => ({
    key: `u:${s.provider}`,
    family: "user" as const,
    label: inventory.providers[s.provider]?.label ?? s.provider,
    provider: s.provider,
    // Carried so the React key can disambiguate two identities sharing a
    // provider (bu-ffjig); `key` stays provider-level for focus deep-links.
    identity: s.identity,
    state: s.state,
    mono: false,
    // Fixed rank: user rows carry no last-touch signal of their own (no usage
    // time is tracked anywhere — bu-hd1vs), so they sort as one block after
    // the system and probed-CLI rows and ahead of the never-set ones. This was
    // already the production behaviour; the `s.lastUsed ? i : 800` it replaces
    // could only ever take the 800 branch.
    lastTouchOrder: 800,
    // bu-86c4c.1 (truth amnesty): these sublines used to hardcode a fake
    // failure age ("refresh failed · 2d") and a fake missing-scope count
    // ("1 scope missing") for EVERY expired / scope_mismatch credential,
    // regardless of how long ago it actually broke or how many scopes are
    // actually missing. bu-6v1hx wired the required/granted arrays to real
    // sources (provider_feature_catalogue / google_accounts.granted_scopes),
    // and bu-iph56 moved them to capability categories, so a real missing
    // count can be shown when both arrays are populated for this credential's
    // provider; otherwise there is still no real number and the plain state
    // label is used. Failure age is still not
    // tracked server-side, so the last known-good verification is shown
    // instead (real data), or a plain label when even that is unavailable.
    subline:
      s.state === "expired"
        ? s.lastVerified
          ? `last verified ${s.lastVerified}`
          : "refresh failed"
        : s.state === "expiring" && s.expires
          ? `expires ${s.expires}`
          : s.state === "scope_mismatch"
            ? capabilityMismatchSubline(s.capabilitiesRequired, s.capabilitiesGranted)
            // bu-976n0: "warn" is set-but-never-probed (see _derive_state) —
            // last_test_ok is None whenever this state is emitted, so there is
            // never a real lastVerified to show here; state a plain "unverified"
            // rather than fabricating a probe result. If a future backend
            // change re-probes and re-verifies a stale-but-previously-ok
            // credential into this bucket, lastVerified would be populated and
            // this falls through to the honest "verified <when>" branch below.
            : s.state === "warn"
              ? (s.lastVerified ? `verified ${s.lastVerified}` : "unverified")
            : s.state === "never_set"
              ? "not connected"
              : `verified ${s.lastVerified ?? "—"}`,
  }));

  return [...cli, ...system, ...user];
}

/** Pick the default focus key (most severe entry). */
export function pickDefaultKey(entries: SpineEntry[]): string {
  if (entries.length === 0) return "";
  const sorted = [...entries].sort((a, b) => {
    return severityRank(a.state) - severityRank(b.state);
  });
  return sorted[0]?.key ?? entries[0]?.key ?? "";
}
