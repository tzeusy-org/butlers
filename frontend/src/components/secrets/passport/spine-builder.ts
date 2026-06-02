// ---------------------------------------------------------------------------
// Spine entry builder — projects inventory data into flat SpineEntry list [bu-qu8v8]
// ---------------------------------------------------------------------------

import type { SpineEntry, InventoryResponse } from "./types.ts";
import { severityRank } from "./constants.ts";

/**
 * Build the flat list of spine entries from inventory data.
 * User entries are filtered by identityId.
 */
export function buildSpineEntries(
  inventory: InventoryResponse,
  identityId: string,
): SpineEntry[] {
  const userSecrets = inventory.user.filter((s) => s.identity === identityId);

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
    subline:
      s.state === "expired"
        ? "refresh failed · 2d"
        : s.state === "expiring" && s.expires
          ? `expires ${s.expires}`
          : s.state === "scope_mismatch"
            ? "1 scope missing"
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
