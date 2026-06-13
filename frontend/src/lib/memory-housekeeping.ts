// ---------------------------------------------------------------------------
// memory-housekeeping — pure helpers for the /memory housekeeping band
// (bu-2ix8d.8)
//
// Band 4 of the house-ledger is three quiet sub-surfaces: retention policies,
// the compaction log, and embeddings. These helpers keep the formatting and
// the kind-constraint logic out of the component so they can be unit-tested
// directly.
//
// Binding docs:
// - pr/overview/memory-redesign/prompts/07-housekeeping.md
// - pr/overview/memory-redesign/MEMORY_LANGUAGE.md §2, §4, §6
// ---------------------------------------------------------------------------

/**
 * The backend's valid retention-policy kinds.
 *
 * Mirrors `_VALID_KINDS` in src/butlers/api/routers/memory.py. The retention
 * grid renders existing rows only and never offers free-text kind creation;
 * this set exists to guard against an invalid kind ever reaching the PUT.
 */
export const VALID_RETENTION_KINDS = [
  "event",
  "fact",
  "preference",
  "summary",
  "transcript",
  "embedding",
] as const;

export type RetentionKind = (typeof VALID_RETENTION_KINDS)[number];

/** True when `kind` is one of the backend's accepted retention kinds. */
export function isValidRetentionKind(kind: string): kind is RetentionKind {
  return (VALID_RETENTION_KINDS as readonly string[]).includes(kind);
}

/**
 * Format an ISO timestamp as the retention `updated` stamp: `YYYY-MM-DD · by`.
 *
 * The `by` suffix is the actor that last wrote the row (`updated_by`), falling
 * back to `system` when null. The date is rendered in ISO calendar form (no
 * locale month names) to match the ledger's mono aesthetic.
 */
export function formatUpdatedStamp(iso: string, by: string | null): string {
  const actor = by ?? "system";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return `—  · ${actor}`;
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day} · ${actor}`;
}

/**
 * Format a byte count as a compact `N.N MB` / `N.N KB` / `N B` string.
 *
 * Returns `null` when `bytes` is null so the caller can OMIT the bytes
 * fragment entirely — the recipe forbids em-dash filler for null bytes.
 */
export function formatBytes(bytes: number | null): string | null {
  if (bytes == null) return null;
  if (bytes < 1024) return `${bytes} B`;
  const kb = bytes / 1024;
  if (kb < 1024) return `${kb.toFixed(1)} KB`;
  const mb = kb / 1024;
  if (mb < 1024) return `${mb.toFixed(1)} MB`;
  const gb = mb / 1024;
  return `${gb.toFixed(1)} GB`;
}

/**
 * Compose the read-only counts fragment for a compaction-log row:
 * `1,204 rows · 3.1 MB`, or just `89 rows` when bytes are null.
 *
 * The bytes clause is omitted (not em-dashed) when `bytes_freed` is null.
 */
export function formatCompactionCounts(rows: number, bytes: number | null): string {
  const rowsPart = `${rows.toLocaleString()} ${rows === 1 ? "row" : "rows"}`;
  const bytesPart = formatBytes(bytes);
  return bytesPart == null ? rowsPart : `${rowsPart} · ${bytesPart}`;
}

/**
 * Compose the one-sentence embedding drift line.
 *
 * Non-zero: `412 rows on an older embedding model.` (summed across tiers).
 * Zero: returns null — the caller renders the serif-italic "All embeddings
 * current." line instead.
 */
export function embeddingDriftSentence(total: number): string | null {
  if (total <= 0) return null;
  return `${total.toLocaleString()} ${total === 1 ? "row is" : "rows are"} on an older embedding model.`;
}

/**
 * Compose the inline dry-run result line:
 * `would re-embed 412 rows across 2 tiers`.
 */
export function dryRunResultLine(total: number, tierCount: number): string {
  return `would re-embed ${total.toLocaleString()} ${
    total === 1 ? "row" : "rows"
  } across ${tierCount} ${tierCount === 1 ? "tier" : "tiers"}`;
}

/**
 * Compose the completion line for a live re-embed run:
 * `re-embedded 412 rows · 38s`.
 */
export function reembedDoneLine(total: number, elapsedSeconds: number): string {
  return `re-embedded ${total.toLocaleString()} ${
    total === 1 ? "row" : "rows"
  } · ${Math.round(elapsedSeconds)}s`;
}
