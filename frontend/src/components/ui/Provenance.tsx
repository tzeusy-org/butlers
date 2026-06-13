// ---------------------------------------------------------------------------
// Provenance — shared provenance display primitives (bu-ovq7t)
//
// Confidence and staleness are TWO VISUALLY DISTINCT AXES and MUST NEVER be
// blended into a single "score" (spec: dashboard-relationship
// "Provenance rendering in the UI" + "Confidence and staleness are separate
// axes"). These primitives render each axis independently so the same fact can
// show full confidence AND a stale band simultaneously.
//
//   - ConfBar         — 4px confidence bar; amber when conf < 0.85.
//   - StalenessBand   — read-time freshness band; dim treatment when stale.
//   - ProvenanceMarks — the source attribution (`src`) + `verified` marks.
//
// Axis 1 (ConfBar) answers "how sure are we this is true". Axis 2
// (StalenessBand) answers "how recently did we last observe it". They are
// orthogonal: a 1.0-confidence fact observed 300 days ago renders a full bar
// AND a stale band. No combined numeral is ever produced.
//
// Token discipline: no hex literals here. Colors come from --amber / --green /
// --dim / --mfg / --fg. (Per entity-model.ts: hex is permitted only there.)
// ---------------------------------------------------------------------------

import * as React from "react"

import type { EntityFactStalenessBand } from "@/api/types"
import { cn } from "@/lib/utils"

// ---------------------------------------------------------------------------
// Axis 1 — Confidence
// ---------------------------------------------------------------------------

/**
 * Confidence threshold below which the bar turns amber. A fact at or above
 * this confidence renders neutral; below it the bar signals "low confidence".
 * Spec: conf bar "amber when < 0.85".
 */
export const CONF_AMBER_THRESHOLD = 0.85

export interface ConfBarProps extends React.HTMLAttributes<HTMLDivElement> {
  /** Confidence in [0, 1]. Clamped on render. */
  conf: number
  /** Bar width in pixels. Defaults to 48. */
  width?: number
}

/**
 * A 4px-tall confidence bar. The fill width is proportional to `conf`; the
 * fill color is amber below {@link CONF_AMBER_THRESHOLD}, neutral above it.
 *
 * This is ONLY the confidence axis — it carries no staleness information.
 *
 * @example
 *   <ConfBar conf={0.92} />   // full-ish, neutral
 *   <ConfBar conf={0.40} />   // short, amber
 */
export function ConfBar({ conf, width = 48, className, style, ...props }: ConfBarProps) {
  const clamped = Math.max(0, Math.min(1, conf))
  const isLow = clamped < CONF_AMBER_THRESHOLD
  const pct = `${Math.round(clamped * 100)}%`

  return (
    <div
      role="meter"
      aria-label="Confidence"
      aria-valuenow={clamped}
      aria-valuemin={0}
      aria-valuemax={1}
      data-low-confidence={isLow ? "true" : undefined}
      className={cn("inline-block overflow-hidden rounded-sm", className)}
      style={{
        width,
        height: 4,
        // Track: faint neutral so the unfilled portion reads as "remaining".
        backgroundColor: "var(--border)",
        ...style,
      }}
      {...props}
    >
      <div
        style={{
          width: pct,
          height: "100%",
          backgroundColor: isLow ? "var(--amber)" : "var(--mfg)",
        }}
      />
    </div>
  )
}

// ---------------------------------------------------------------------------
// Axis 2 — Staleness
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
 * "did verification pass", independent of the confidence and staleness axes.
 *
 * The verified mark renders green when verified, dim when not. The `src` tag
 * is mono/muted. Either field may be omitted.
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
