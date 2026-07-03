// ---------------------------------------------------------------------------
// Spine entry builder — projects inventory data into flat SpineEntry list [bu-qu8v8]
// ---------------------------------------------------------------------------

import type { SpineEntry, InventoryResponse } from "./types.ts";
import { severityRank } from "./constants.ts";

/**
 * Real missing-scope count for a scope_mismatch credential, or a plain label
 * when the required/granted scope arrays aren't populated for this provider
 * (bu-6v1hx: most providers besides Google have no granted-scope tracking
 * yet — never fabricate a count in that case).
 */
function scopeMismatchSubline(scopesRequired: string[], scopesGranted: string[]): string {
  if (scopesRequired.length === 0) return "scope mismatch";
  const granted = new Set(scopesGranted);
  const missing = scopesRequired.filter((scope) => !granted.has(scope)).length;
  if (missing === 0) return "scope mismatch";
  return `${missing} scope${missing === 1 ? "" : "s"} missing`;
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
    subline:
      r.state === "never_set"
        ? "not set"
        : r.state === "warn"
          ? "needs probe"
        : r.state === "expiring" && r.expires
          ? `expires ${r.expires}`
          : `used ${r.lastUsed ?? "—"}`,
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

  const user: SpineEntry[] = userSecrets.map((s, i) => ({
    key: `u:${s.provider}`,
    family: "user" as const,
    label: inventory.providers[s.provider]?.label ?? s.provider,
    provider: s.provider,
    state: s.state,
    mono: false,
    lastTouchOrder: s.lastUsed ? i : 800,
    // bu-86c4c.1 (truth amnesty): these sublines used to hardcode a fake
    // failure age ("refresh failed · 2d") and a fake missing-scope count
    // ("1 scope missing") for EVERY expired / scope_mismatch credential,
    // regardless of how long ago it actually broke or how many scopes are
    // actually missing. bu-6v1hx wired scopesRequired/scopesGranted to real
    // sources (provider_feature_catalogue / google_accounts.granted_scopes),
    // so a real missing-scope count can now be shown when both arrays are
    // populated for this credential's provider; otherwise there is still no
    // real number and the plain state label is used. Failure age is still not
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
            ? scopeMismatchSubline(s.scopesRequired, s.scopesGranted)
            : s.state === "warn"
              ? "needs probe"
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
