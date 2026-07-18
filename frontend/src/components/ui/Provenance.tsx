// ---------------------------------------------------------------------------
// Provenance — shared provenance display primitives (bu-ovq7t)
//
// Staleness and source attribution are TWO VISUALLY DISTINCT SIGNALS and MUST
// NEVER be blended into a single "score" (spec: dashboard-relationship
// "Provenance rendering in the UI"). These primitives render each axis
// independently.
//
//   - StalenessBand   — read-time freshness band; dim treatment when stale.
//   - ProvenanceMarks — the source attribution (`src`) + `verified` marks.
//
// Note: the `conf` column exists in the DB and is used by the backend for
// merge-conflict resolution (higher-conf wins), but conf is hardcoded 1.0 at
// every write site — no calibration path exists. The per-fact confidence bar
// (ConfBar) was removed (bu-8j0ir) to stop implying calibration that does not
// exist. If real confidence scoring is added in the future, re-introduce the
// visual axis at that point.
//
// Token discipline: no hex literals here. Colors come from --amber / --green /
// --dim / --mfg / --fg. (Per entity-model.ts: hex is permitted only there.)
// ---------------------------------------------------------------------------

import * as React from "react"

import type { EntityFactStalenessBand } from "@/api/types"
import { cn } from "@/lib/utils"

// ---------------------------------------------------------------------------
// Axis 1 — Staleness
// ---------------------------------------------------------------------------

/** Human-readable label per staleness band. */
const STALENESS_LABELS: Record<EntityFactStalenessBand, string> = {
  fresh: "Fresh",
  aging: "Aging",
  stale: "Stale",
}

/**
 * Upper bound (inclusive) of the `fresh` band, in days. Mirrors the server-side
 * canonical thresholds (`roster/relationship/tools/staleness.py`): age ≤ 30 is
 * fresh, 30 < age ≤ 180 is aging, above 180 is stale.
 */
export const FRESH_MAX_DAYS = 30
/** Upper bound (inclusive) of the `aging` band, in days. Above this is `stale`. */
export const AGING_MAX_DAYS = 180

/** Milliseconds per day. */
const MS_PER_DAY = 86_400_000

/**
 * Derive a staleness band from an observation timestamp, using the same
 * thresholds as the server's identity/narrative staleness SQL. This exists for
 * surfaces that carry a raw `occurred_at`/`last_received_at` but no server-side
 * `staleness_band` (e.g. the latest-interactions block reads through the
 * timeline and message-thread endpoints). A null/unparseable timestamp is
 * treated as `stale` (we cannot vouch for its freshness).
 *
 * @param when - ISO timestamp, Date, or null.
 * @param now - reference "now" (defaults to the current time; injectable for tests).
 */
export function stalenessBandForTimestamp(
  when: string | Date | null | undefined,
  now: Date = new Date(),
): EntityFactStalenessBand {
  if (when == null) return "stale"
  const ts = when instanceof Date ? when : new Date(when)
  const ms = ts.getTime()
  if (Number.isNaN(ms)) return "stale"
  const ageDays = (now.getTime() - ms) / MS_PER_DAY
  if (ageDays <= FRESH_MAX_DAYS) return "fresh"
  if (ageDays <= AGING_MAX_DAYS) return "aging"
  return "stale"
}

export interface StalenessBandProps extends React.HTMLAttributes<HTMLSpanElement> {
  /** Read-time freshness band. */
  band: EntityFactStalenessBand
}

/**
 * Read-time staleness band treatment. Renders the band label; `stale` rows get
 * a dim treatment (reduced opacity) so a stale fact visibly recedes. `aging`
 * is muted; `fresh` is neutral.
 *
 * This is ONLY the staleness axis — it carries no confidence information. A
 * fact may be fully confident and still stale; the two never combine.
 *
 * @example
 *   <StalenessBand band="stale" />  // dim
 *   <StalenessBand band="fresh" />  // neutral
 */
export function StalenessBand({ band, className, ...props }: StalenessBandProps) {
  const label = STALENESS_LABELS[band]
  const isStale = band === "stale"

  return (
    <span
      data-staleness={band}
      data-stale={isStale ? "true" : undefined}
      aria-label={`Staleness: ${label}`}
      className={cn(
        "inline-flex items-center font-mono text-[10px] uppercase leading-none tracking-[0.08em]",
        // Dim when stale; muted when aging; tertiary-neutral when fresh.
        isStale ? "opacity-40 text-[var(--dim)]" : band === "aging" ? "text-[var(--mfg)]" : "text-[var(--dim)]",
        className,
      )}
      {...props}
    >
      {label}
    </span>
  )
}

// ---------------------------------------------------------------------------
// Source attribution + verification marks
// ---------------------------------------------------------------------------

export interface ProvenanceMarksProps extends React.HTMLAttributes<HTMLSpanElement> {
  /** Butler slug that authored the fact (e.g. "relationship"). */
  src?: string | null
  /** Whether the fact's most recent verification succeeded. */
  verified?: boolean
}

/**
 * Source + verification marks: a mono `src` tag plus a `verified` check-mark.
 * Both are quiet attributions, not a score — they answer "who said it" and
 * "did verification pass", independent of the staleness axis.
 *
 * The verified mark renders green when verified, dim when not. The `src` tag
 * is mono/muted. Either field may be omitted.
 *
 * Note: at present, `verified=true` is only reachable for the
 * `prefers-channel` predicate (set by assert_prefers_channel). For ordinary
 * entity facts, `verified` is always false. Contact-channel verification is
 * tracked separately (bu-e90i6).
 *
 * @example
 *   <ProvenanceMarks src="relationship" verified />
 */
export function ProvenanceMarks({
  src,
  verified,
  className,
  ...props
}: ProvenanceMarksProps) {
  return (
    <span className={cn("inline-flex items-center gap-1.5", className)} {...props}>
      {src != null && src !== "" && (
        <span
          className="font-mono text-[10px] uppercase leading-none tracking-[0.08em] text-[var(--mfg)]"
          aria-label={`Source: ${src}`}
          title={`Source: ${src}`}
        >
          {src}
        </span>
      )}
      {verified != null && (
        <span
          aria-label={verified ? "Verified" : "Unverified"}
          title={verified ? "Verified" : "Unverified"}
          className="font-mono text-[10px] leading-none"
          style={{ color: verified ? "var(--green)" : "var(--dim)" }}
          data-verified={verified ? "true" : "false"}
        >
          {verified ? "✓" : "–"}
        </span>
      )}
    </span>
  )
}
