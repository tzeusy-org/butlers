/**
 * Day-briefing card — "tomorrow at a glance" (bu-jj0b3n).
 *
 * Renders the structured day-briefing read
 * (`GET /api/calendar/workspace/day-briefing`) for a target date: the date's
 * precomputed overlay contributions grouped by butler then kind, each item a
 * chip that links to its day in the calendar.
 *
 * STRUCTURED v1 — there is NO per-open LLM call and NO generated prose. Every
 * label/glyph/badge is drawn verbatim from the projected entry's `metadata`
 * (the same overlay helpers the day-grid pills use). Any narrative summary is
 * deferred to the batched pre-render layer (cf5) and is intentionally absent.
 *
 * Honest empty/degraded state:
 * - `has_domain_context === false` (no specialist contributed, or the cached
 *   view is absent/unreadable) → render an explicit "Tomorrow is clear" /
 *   "No domain context for this day" line rather than omitting the card.
 * - `has_domain_context === true` with zero entries → the specialist contributed
 *   but had nothing for the day; still rendered as "clear" with that distinction.
 */

import type { UnifiedCalendarEntry } from "@/api/types.ts";
import {
  overlayAmountBadge,
  overlayButlerAccent,
  overlayKindGlyph,
  overlayMetadata,
} from "@/lib/calendar-overlays.ts";
import { cn } from "@/lib/utils.ts";

/** Title-case a `source_butler` / `kind` identifier for section labels. */
function titleizeToken(value: string): string {
  return value
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (ch) => ch.toUpperCase())
    .trim();
}

/** One chip linking to an underlying overlay item; clicking jumps to its day. */
function DayBriefingChip({
  entry,
  onSelect,
}: {
  entry: UnifiedCalendarEntry;
  onSelect?: (entry: UnifiedCalendarEntry) => void;
}) {
  const md = overlayMetadata(entry);
  const badge = overlayAmountBadge(md.meta);
  const accent = overlayButlerAccent(md.source_butler);
  const label = `${md.source_butler ?? "overlay"} · ${md.kind || "context"}${
    md.priority ? ` (${md.priority})` : ""
  }: ${entry.title}${badge ? ` — ${badge}` : ""}`;
  const interactive = typeof onSelect === "function";

  const content = (
    <>
      <span aria-hidden className="shrink-0 font-mono">
        {overlayKindGlyph(md.kind)}
      </span>
      <span className="truncate text-[var(--fg)]">{entry.title}</span>
      {badge ? (
        <span className="ml-auto shrink-0 font-mono tabular-nums text-[var(--mfg)]">{badge}</span>
      ) : null}
    </>
  );

  const className = cn(
    "flex max-w-full items-center gap-1 truncate rounded-[2px] border border-dashed bg-foreground/[0.02] px-1.5 py-0.5 text-left text-[11px] leading-none",
    accent,
    interactive &&
      "cursor-pointer transition-colors hover:bg-foreground/[0.06] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--fg)]/30",
  );

  if (!interactive) {
    return (
      <div
        data-day-briefing-chip={entry.entry_id}
        data-day-briefing-kind={md.kind}
        data-day-briefing-butler={md.source_butler ?? ""}
        title={label}
        className={className}
      >
        {content}
      </div>
    );
  }

  return (
    <button
      type="button"
      data-day-briefing-chip={entry.entry_id}
      data-day-briefing-kind={md.kind}
      data-day-briefing-butler={md.source_butler ?? ""}
      title={label}
      aria-label={label}
      onClick={() => onSelect?.(entry)}
      className={className}
    >
      {content}
    </button>
  );
}

export interface DayBriefingCardProps {
  /** Target date label (e.g. "Tomorrow · Mon, Feb 23") shown in the header. */
  heading: string;
  /** Whether the underlying query is still loading (first fetch). */
  isLoading?: boolean;
  /** Grouped overlay entries by butler/kind (from the day-briefing response). */
  groups: import("@/api/types.ts").DayBriefingButlerGroup[];
  /** Whether at least one specialist contributed for the date. */
  hasDomainContext: boolean;
  /** Whether any overlay entry exists for the date. */
  hasEntries: boolean;
  /** Optional chip click handler — links a chip to its day in the calendar. */
  onSelectEntry?: (entry: UnifiedCalendarEntry) => void;
}

/**
 * Presentational day-briefing card. Data fetching lives in the page via
 * {@link useCalendarDayBriefing}; this component only renders the structured
 * payload (kept prop-driven so it is trivially unit-testable).
 */
export function DayBriefingCard({
  heading,
  isLoading = false,
  groups,
  hasDomainContext,
  hasEntries,
  onSelectEntry,
}: DayBriefingCardProps) {
  return (
    <section
      data-testid="day-briefing-card"
      aria-label="Day briefing"
      className="rounded-[4px] border border-[var(--border)] bg-foreground/[0.015] p-3"
    >
      <header className="mb-2 flex items-baseline justify-between gap-2">
        <h2 className="font-mono text-[11px] uppercase tracking-[0.14em] text-[var(--mfg)]">
          At a glance
        </h2>
        <span className="truncate font-mono text-[11px] text-[var(--fg)]">{heading}</span>
      </header>

      {isLoading ? (
        <p className="font-mono text-[11px] text-[var(--dim)]">Loading…</p>
      ) : !hasDomainContext ? (
        // Honest empty/degraded state — no specialist contributed (or the cached
        // view is unavailable). Render explicitly rather than omitting the card.
        <p data-testid="day-briefing-clear" className="font-mono text-[11px] text-[var(--mfg)]">
          No domain context for this day — tomorrow is clear.
        </p>
      ) : !hasEntries ? (
        // A specialist contributed but had nothing for the day: still "clear",
        // but we know coverage ran (distinct from "no domain context").
        <p data-testid="day-briefing-clear" className="font-mono text-[11px] text-[var(--mfg)]">
          Nothing scheduled across your domains — tomorrow is clear.
        </p>
      ) : (
        <div className="flex flex-col gap-2.5">
          {groups.map((group) => (
            <div key={group.source_butler} data-day-briefing-group={group.source_butler}>
              <div className="mb-1 flex items-center gap-1.5">
                <span
                  className={cn(
                    "rounded-[2px] border px-1 font-mono text-[10px] uppercase tracking-[0.1em]",
                    overlayButlerAccent(group.source_butler),
                  )}
                >
                  {titleizeToken(group.source_butler)}
                </span>
                <span className="font-mono text-[10px] text-[var(--dim)] tabular-nums">
                  {group.count}
                </span>
              </div>
              <div className="flex flex-col gap-1.5 pl-1">
                {group.kinds.map((kindGroup) => (
                  <div key={kindGroup.kind} data-day-briefing-kind={kindGroup.kind}>
                    <span className="mb-0.5 block font-mono text-[10px] text-[var(--mfg)]">
                      {titleizeToken(kindGroup.kind)}
                    </span>
                    <div className="flex flex-wrap gap-1">
                      {kindGroup.entries.map((entry) => (
                        <DayBriefingChip
                          key={entry.entry_id}
                          entry={entry}
                          onSelect={onSelectEntry}
                        />
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

export default DayBriefingCard;
