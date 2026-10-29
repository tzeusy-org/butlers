/**
 * Sync-freshness presentation for the calendar workspace: staleness wording,
 * the grid-level plaque, and optional timestamp formatting.
 */

import { format, isValid, parseISO } from "date-fns";

import type { CalendarWorkspaceSourceFreshness } from "@/api/types.ts";

export function formatStaleness(stalenessMs: number | null): string {
  if (stalenessMs == null) {
    return "staleness unknown";
  }
  if (stalenessMs < 1_000) {
    return "fresh";
  }
  const seconds = Math.floor(stalenessMs / 1_000);
  if (seconds < 60) {
    return `${seconds}s stale`;
  }
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) {
    return `${minutes}m stale`;
  }
  const hours = Math.floor(minutes / 60);
  if (hours < 48) {
    return `${hours}h stale`;
  }
  return `${Math.floor(hours / 24)}d stale`;
}

/**
 * Grid-level sync-freshness plaque (bu-hmdqz.10): the worst-case staleness
 * across every enabled provider-synced source, phrased for the masthead rather than the
 * Sources rail's compact per-source dots. Only returns non-null when the
 * data warrants a degraded clause -- callers must render nothing otherwise
 * (quiet by default; a fresh calendar gets no pixel here).
 */
export interface CalendarFreshnessPlaque {
  detail: string;
  /** Unknown/missing freshness data (never treated as fresh -- see caller). */
  unknown: boolean;
}

export function formatFreshnessPlaqueDetail(
  lastSyncedAt: string | null,
  stalenessMs: number | null,
): string {
  if (!lastSyncedAt || stalenessMs == null) {
    return "Never synced";
  }
  const parsed = parseISO(lastSyncedAt);
  if (!isValid(parsed)) {
    return "Never synced";
  }
  const days = Math.floor(stalenessMs / 86_400_000);
  if (days >= 1) {
    return `Last synced ${format(parsed, "MMM d")} — ${days} day${days === 1 ? "" : "s"} ago`;
  }
  const hours = Math.floor(stalenessMs / 3_600_000);
  if (hours >= 1) {
    return `Last synced ${hours}h ago`;
  }
  const minutes = Math.max(1, Math.floor(stalenessMs / 60_000));
  return `Last synced ${minutes}m ago`;
}

/**
 * Compute the grid-level freshness plaque from the enabled connected
 * sources. Mirrors the backend's own `sync_state` classification (the exact
 * dots the Sources rail already renders) rather than a second staleness
 * threshold that could silently disagree with it.
 *
 * Degraded-envelope contract: missing/unavailable freshness data renders as
 * `unknown: true` (a degraded clause), never as an absence of a clause --
 * silently rendering nothing here would read as "fresh" to the owner, which
 * is exactly the failure this plaque exists to prevent (see the 96-day-stale
 * live incident this bead is named for).
 */
// Exported for unit testing (bu-sn71y): the sources_available degraded path is a
// pure computation better verified directly than through a full page render.
export function computeFreshnessPlaque(
  sources: CalendarWorkspaceSourceFreshness[],
  metaStatus: "pending" | "error" | "success",
  sourcesAvailable: boolean = true,
): CalendarFreshnessPlaque | null {
  if (metaStatus === "error") {
    return { detail: "Sync status unavailable", unknown: true };
  }
  if (metaStatus === "pending") {
    return null; // still loading -- not yet an honest signal either way
  }
  if (!sourcesAvailable) {
    // 200 OK but a targeted schema's calendar_sources fan-out FAILED: the
    // connected list is silently incomplete, so a "fresh" verdict below would be
    // a false all-clear. Name the degraded lookup instead (bu-sn71y).
    return { detail: "Sync status unavailable", unknown: true };
  }
  // Internal sources never sync, so their missing cursor would otherwise read
  // as "Never synced" forever (bu-ak7fij).
  const syncable = sources.filter((source) => source.sync_applicable);
  const enabled = syncable.filter((source) => source.sync_enabled);
  if (enabled.length === 0) {
    // No sources configured, or every source was deliberately disabled --
    // a legitimate empty state (see backend `sync_enabled` semantics), but
    // an empty *connected* list on a page the owner expects synced data on
    // is itself unusual enough to name rather than silently show nothing.
    return syncable.length === 0
      ? { detail: "Sync status unknown", unknown: true }
      : null;
  }
  const worst = enabled.reduce((a, b) => {
    const aMs = a.staleness_ms ?? Number.POSITIVE_INFINITY;
    const bMs = b.staleness_ms ?? Number.POSITIVE_INFINITY;
    return bMs > aMs ? b : a;
  });
  if (worst.sync_state === "fresh") {
    return null;
  }
  return {
    detail: formatFreshnessPlaqueDetail(worst.last_synced_at, worst.staleness_ms),
    unknown: false,
  };
}

export function formatOptionalTimestamp(value: string | null): string | null {
  if (!value) return null;
  const parsed = parseISO(value);
  if (!isValid(parsed)) return null;
  return format(parsed, "MMM d, HH:mm");
}
