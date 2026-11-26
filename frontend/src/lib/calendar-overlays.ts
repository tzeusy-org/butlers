/**
 * Pure helpers for the calendar cross-domain overlay layer (`view=overlays`).
 *
 * Overlays are read-only, precomputed domain-context contributions projected
 * onto calendar days: finance bill/renewal pills, travel trip ribbons,
 * relationship important-date markers, health appointment context. These helpers
 * group/format the projected `UnifiedCalendarEntry` rows for the day cells and
 * are kept separate from the page component so they can be unit-tested and so the
 * page file only exports its component (react-refresh).
 *
 * Structured only — NO generated prose. Everything rendered comes verbatim from
 * the entry's `title` and `metadata` (`kind` / `priority` / `source_butler` /
 * `meta`); this module never synthesizes narrative text.
 */

import { formatOwnerDateTime } from "@/components/ui/time";
import type { OverlayFreshness, UnifiedCalendarEntry } from "@/api/types.ts";

/** Overlay priority as written by the contribution jobs. */
export type OverlayPriority = "high" | "medium" | "low";

/**
 * Narrowed view of an overlay entry's `metadata`. The backend projection always
 * sets `source_type="overlay_contribution"` plus `kind`/`priority`/
 * `source_butler`/`meta`; everything is read defensively since `metadata` is an
 * untyped JSON bag on the wire.
 */
export interface OverlayMetadata {
  kind: string;
  priority: OverlayPriority | null;
  source_butler: string | null;
  meta: Record<string, unknown>;
  /** Set when the producing butler's context is out of date or incomplete. */
  notCurrent: "stale" | "partial" | null;
}

/** True when an entry is a projected overlay contribution (vs a real event). */
export function isOverlayEntry(entry: UnifiedCalendarEntry): boolean {
  return entry.source_type === "overlay_contribution";
}

/** Read the overlay metadata off an entry, defaulting missing fields. */
export function overlayMetadata(entry: UnifiedCalendarEntry): OverlayMetadata {
  const md = entry.metadata ?? {};
  const kind = typeof md.kind === "string" ? md.kind : "";
  const priorityRaw = md.priority;
  const priority =
    priorityRaw === "high" || priorityRaw === "medium" || priorityRaw === "low"
      ? priorityRaw
      : null;
  const source_butler =
    typeof md.source_butler === "string"
      ? md.source_butler
      : typeof entry.source_butler === "string"
        ? entry.source_butler
        : null;
  const meta =
    md.meta && typeof md.meta === "object" ? (md.meta as Record<string, unknown>) : {};
  const notCurrent = md.stale === true ? "stale" : md.partial === true ? "partial" : null;
  return { kind, priority, source_butler, meta, notCurrent };
}

/** The domain claim a calendar row (butler-lane event) was written for, when it has one. */
export interface ClaimLink {
  claimKey: string;
  door: { route: string; ref: string | null } | null;
}

function doorOf(raw: unknown): ClaimLink["door"] {
  if (!raw || typeof raw !== "object") return null;
  const door = raw as Record<string, unknown>;
  if (typeof door.route !== "string" || !door.route.startsWith("/")) return null;
  return { route: door.route, ref: typeof door.ref === "string" ? door.ref : null };
}

/**
 * The claim link of a calendar row or an overlay entry: a row carries it in its event metadata
 * (`claim`), an overlay entry as `claim_key` / `claim_door`. Null when the entry has no claim.
 */
export function claimLinkOf(entry: UnifiedCalendarEntry): ClaimLink | null {
  const md = entry.metadata ?? {};
  if (isOverlayEntry(entry)) {
    if (typeof md.claim_key !== "string" || !md.claim_key) return null;
    return { claimKey: md.claim_key, door: doorOf(md.claim_door) };
  }
  const raw = md.event_metadata;
  const claim =
    raw && typeof raw === "object" ? (raw as Record<string, unknown>).claim : undefined;
  if (!claim || typeof claim !== "object") return null;
  const record = claim as Record<string, unknown>;
  if (typeof record.claim_key !== "string" || !record.claim_key) return null;
  return { claimKey: record.claim_key, door: doorOf(record.door) };
}

/**
 * Overlay entries minus those whose claim also owns a calendar row in `rows`: a record shows once,
 * as its row. An overlay entry with no claim, or whose claim owns no row here, is kept.
 */
export function dedupOverlaysAgainstRows(
  overlays: UnifiedCalendarEntry[],
  rows: UnifiedCalendarEntry[],
): UnifiedCalendarEntry[] {
  const owned = new Set<string>();
  for (const row of rows) {
    if (isOverlayEntry(row)) continue;
    const link = claimLinkOf(row);
    if (link) owned.add(link.claimKey);
  }
  if (owned.size === 0) return overlays;
  return overlays.filter((entry) => {
    const link = claimLinkOf(entry);
    return !link || !owned.has(link.claimKey);
  });
}

/** Sort rank so high-priority overlays render first (lower = earlier). */
export function overlayPriorityRank(priority: OverlayPriority | null): number {
  switch (priority) {
    case "high":
      return 0;
    case "medium":
      return 1;
    case "low":
      return 2;
    default:
      return 3;
  }
}

/**
 * Group overlay entries by their local calendar day (`yyyy-MM-dd`), each day's
 * list ordered priority-descending then by source butler for stable rendering.
 */
export function overlaysByDay(
  entries: UnifiedCalendarEntry[],
): Map<string, UnifiedCalendarEntry[]> {
  const buckets = new Map<string, UnifiedCalendarEntry[]>();
  for (const entry of entries) {
    if (!isOverlayEntry(entry)) continue;
    // Bucket by the ISO date portion directly (timezone-independent). The backend
    // sets ``start_at`` to local midnight on the contribution's target date, so
    // ``YYYY-MM-DD`` is the intended day. Parsing via ``new Date()`` and
    // reformatting in the browser's local timezone could shift the day.
    const key = entry.start_at.slice(0, 10);
    const bucket = buckets.get(key) ?? [];
    bucket.push(entry);
    buckets.set(key, bucket);
  }
  for (const bucket of buckets.values()) {
    bucket.sort((a, b) => {
      const pa = overlayPriorityRank(overlayMetadata(a).priority);
      const pb = overlayPriorityRank(overlayMetadata(b).priority);
      if (pa !== pb) return pa - pb;
      const ba = overlayMetadata(a).source_butler ?? "";
      const bb = overlayMetadata(b).source_butler ?? "";
      return ba.localeCompare(bb);
    });
  }
  return buckets;
}

/** Tailwind accent classes (text/border/bg tint) per source butler. */
export function overlayButlerAccent(sourceButler: string | null): string {
  switch (sourceButler) {
    case "finance":
      return "border-categorical-1/40 text-categorical-1";
    case "travel":
      return "border-categorical-2/40 text-categorical-2";
    case "relationship":
      return "border-categorical-3/40 text-categorical-3";
    case "health":
      return "border-categorical-4/40 text-categorical-4";
    case "education":
      return "border-categorical-5/40 text-categorical-5";
    default:
      return "border-[var(--border)] text-[var(--mfg)]";
  }
}

function titleizeButler(value: string): string {
  return value.replace(/[_-]+/g, " ").replace(/\b\w/g, (ch) => ch.toUpperCase()).trim();
}

/**
 * One plaque line per butler whose overlay context is stale or partial, so it is never
 * read as current. A stale-since instant renders in `timezone` (the owner's zone).
 */
export function overlayFreshnessNotes(
  freshness: OverlayFreshness[] | undefined,
  timezone: string,
): string[] {
  const notes: string[] = [];
  for (const item of freshness ?? []) {
    if (item.state !== "stale" && item.state !== "partial") continue;
    const name = titleizeButler(item.butler);
    if (item.state === "partial") {
      notes.push(`${name} context is incomplete`);
      continue;
    }
    if (!item.stale_since) {
      notes.push(`${name} context has no freshness stamp`);
      continue;
    }
    const when = formatOwnerDateTime(item.stale_since, timezone);
    notes.push(`${name} context stale since ${when}`);
  }
  return notes;
}

/** Short glyph prefix per overlay kind (no prose — a compact category marker). */
export function overlayKindGlyph(kind: string): string {
  switch (kind) {
    case "bill_due":
      return "$";
    case "subscription_renewal":
      return "↻";
    case "departure":
      return "✈";
    case "arrival":
      return "⇲";
    case "check_in":
      return "▸";
    case "check_out":
      return "◂";
    case "birthday":
      return "★";
    case "important_date":
      return "◆";
    case "follow_up":
      return "↳";
    case "appointment":
      return "+";
    case "medication_reminder":
      return "℞";
    case "reviews_due":
      return "↺";
    default:
      return "•";
  }
}

/**
 * Compact trailing badge for an overlay pill, derived structurally from `meta`.
 * Finance entries surface a currency-prefixed amount (e.g. `SGD 84`); other
 * kinds have no badge. Returns `null` when no structured badge applies.
 */
export function overlayAmountBadge(meta: Record<string, unknown>): string | null {
  const amount = meta.amount;
  if (typeof amount !== "number" || !Number.isFinite(amount)) return null;
  const currencyRaw = meta.currency;
  const currency =
    typeof currencyRaw === "string" && currencyRaw.trim() ? currencyRaw.trim() : null;
  const rounded = Math.round(amount).toLocaleString();
  return currency ? `${currency} ${rounded}` : rounded;
}
