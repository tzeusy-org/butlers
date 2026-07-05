/**
 * Recently-used palette entries (bu-qvnce.11 — "palette browsability").
 *
 * A tiny localStorage-backed list of the last few entities/pages/actions the
 * owner opened via the command menu (EntityFinder), shown in a "Recents"
 * group at empty query — so the palette is browsable before typing a single
 * character, alongside the existing owner-pinned entity set, not just after.
 *
 * Deliberately NOT a generic key-value cache: this is one small, capped,
 * best-effort list. localStorage failures (private browsing, quota, disabled
 * storage) degrade silently to "no recents" — recents are a convenience, and
 * losing them is never a hard failure.
 */

export type RecentKind = "entity" | "page" | "action";

export interface RecentEntry {
  /** Stable id within its kind — an entity id, a route path, or a command id. */
  id: string;
  kind: RecentKind;
  /** Display label at the time it was recorded (may go stale; that's fine). */
  label: string;
  /** entity_type, for entities only — drives EntityMark's icon. */
  entityType?: string;
  /** Epoch ms — most-recent-first ordering. */
  timestamp: number;
}

const STORAGE_KEY = "dashboard.finder.recents.v1";
const MAX_ENTRIES = 8;

function isRecentEntry(value: unknown): value is RecentEntry {
  if (!value || typeof value !== "object") return false;
  const v = value as Record<string, unknown>;
  return (
    typeof v.id === "string" &&
    (v.kind === "entity" || v.kind === "page" || v.kind === "action") &&
    typeof v.label === "string" &&
    typeof v.timestamp === "number"
  );
}

function readAll(): RecentEntry[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(isRecentEntry);
  } catch {
    return [];
  }
}

function writeAll(entries: RecentEntry[]): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(entries));
  } catch {
    // Storage unavailable — recents are best-effort, never a hard dependency.
  }
}

/**
 * Record (or bump) a recent entry, keyed on `kind:id` so an entity and a page
 * can never collide even if their raw ids happen to match. Most-recent-first,
 * capped at `MAX_ENTRIES`.
 */
export function addRecent(entry: { id: string; kind: RecentKind; label: string; entityType?: string }): void {
  const key = `${entry.kind}:${entry.id}`;
  const existing = readAll().filter((e) => `${e.kind}:${e.id}` !== key);
  const next = [{ ...entry, timestamp: Date.now() }, ...existing].slice(0, MAX_ENTRIES);
  writeAll(next);
}

/** Read recents, most-recent-first. */
export function getRecents(): RecentEntry[] {
  return readAll().sort((a, b) => b.timestamp - a.timestamp);
}
