/**
 * HourFlameStrip — status-stacked, clickable per-minute activity strip for an
 * hour group (bu-4utdw.7).
 *
 * Renders one interactive segment per minute (or coarser bucket for wide
 * ranges), height proportional to that minute's total event count, each
 * segment internally stacked by status share so an all-error minute reads
 * solid destructive-red at a glance instead of undifferentiated gray.
 *
 * Data comes from GET /api/ingestion/events/histogram (see
 * use-ingestion-events.ts's useIngestionEventsHistogram) rather than the
 * currently-loaded ledger page, so the strip is correct even when only some
 * pages of an hour have loaded — the historical `deriveMinuteCounts` derived
 * density from loaded rows only, which understated hours cut by a page
 * boundary. That helper and its density-only rendering are retired; this
 * component owns bucket alignment, stacking, and honesty directly.
 *
 * Stack order within a bar, top (most visible) to bottom (baseline):
 * error+failed+replay failed → replay pending → replay complete → ingested →
 * filtered/skipped. Both "failed" (routing failure after ingestion, bu-lkzsf.1)
 * and "replay_failed" share the destructive segment: each is a live failure,
 * not an informational replay category. Pending replays remain neutral; completed
 * replays use the existing success color.
 *
 * The strip is always 60px wide (bucketMinutes * slotCount == 60) so the
 * timeline column width stays stable regardless of bucket granularity.
 *
 * Interaction: every minute is a real <button>, so it is keyboard-reachable
 * and activatable without extra ARIA. Hover/focus reveals a compact
 * "HH:mm · counts" label; the strip itself exposes an aria-label summary of
 * the whole hour instead of being hidden from assistive tech.
 *
 * Spec: openspec/specs/dashboard-ingestion-dispatch-console/spec.md
 *       §"Timeline Ledger" (hour strip scenarios)
 */

import { useMemo, useState } from "react";
import { formatInTimeZone } from "date-fns-tz";

import { useTimezone } from "@/components/ui/timezone-context";
import type { IngestionHistogramBucket, IngestionHistogramCounts } from "@/api/index.ts";

const ZERO_COUNTS: IngestionHistogramCounts = {
  ingested: 0,
  skipped: 0,
  filtered: 0,
  error: 0,
  failed: 0,
  replay_pending: 0,
  replay_complete: 0,
  replay_failed: 0,
};

interface MinuteSlot {
  offsetMinutes: number;
  minuteIso: string;
  counts: IngestionHistogramCounts;
  total: number;
}

function countsTotal(c: IngestionHistogramCounts): number {
  return (
    c.ingested +
    c.skipped +
    c.filtered +
    c.error +
    c.failed +
    c.replay_pending +
    c.replay_complete +
    c.replay_failed
  );
}

/**
 * Every terminal failure shares the destructive segment. `replay_failed` is a
 * failed outcome, not a replay category, so it must remain visible to both the
 * error marker and assistive-technology summaries.
 */
function errorTotal(c: IngestionHistogramCounts): number {
  return c.error + c.failed + c.replay_failed;
}

function directErrorTotal(c: IngestionHistogramCounts): number {
  return c.error + c.failed;
}

function replayPendingTotal(c: IngestionHistogramCounts): number {
  return c.replay_pending;
}

function replayCompleteTotal(c: IngestionHistogramCounts): number {
  return c.replay_complete;
}

function countLabel(count: number, singular: string, plural = `${singular}s`): string {
  return `${count} ${count === 1 ? singular : plural}`;
}

function formatClock(iso: string, tz: string): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "—";
  return formatInTimeZone(d, tz, "HH:mm");
}

/** "17:12 · 3 errors · 9 filtered" — hover/focus label for one minute. */
function formatMinuteSummary(slot: MinuteSlot, tz: string): string {
  const label = formatClock(slot.minuteIso, tz);
  if (slot.total === 0) return `${label} · no events`;
  const { counts } = slot;
  const parts: string[] = [];
  const directErrors = directErrorTotal(counts);
  if (directErrors > 0) parts.push(countLabel(directErrors, "error"));
  if (counts.replay_failed > 0) {
    parts.push(countLabel(counts.replay_failed, "replay failure", "replay failures"));
  }
  const pendingReplays = replayPendingTotal(counts);
  if (pendingReplays > 0) {
    parts.push(countLabel(pendingReplays, "replay pending", "replays pending"));
  }
  const completeReplays = replayCompleteTotal(counts);
  if (completeReplays > 0) {
    parts.push(countLabel(completeReplays, "replay complete", "replays complete"));
  }
  if (counts.filtered > 0) parts.push(`${counts.filtered} filtered`);
  if (counts.skipped > 0) parts.push(`${counts.skipped} skipped`);
  if (counts.ingested > 0) parts.push(`${counts.ingested} ingested`);
  return `${label} · ${parts.join(" · ")}`;
}

/** "Activity 17:00–18:00, peak 17:22, 6 errors starting 17:10" — strip-level summary. */
function buildAriaSummary(hourStart: string, slots: MinuteSlot[], tz: string): string {
  const hourStartMs = new Date(hourStart).getTime();
  if (isNaN(hourStartMs)) return "Activity, no data";
  const startLabel = formatClock(hourStart, tz);
  const endLabel = formatClock(new Date(hourStartMs + 3_600_000).toISOString(), tz);
  const totalEvents = slots.reduce((sum, s) => sum + s.total, 0);
  if (totalEvents === 0) return `Activity ${startLabel}–${endLabel}, no events`;

  const peakSlot = slots.reduce((best, s) => (s.total > best.total ? s : best), slots[0]);
  const directErrorCount = slots.reduce((sum, s) => sum + directErrorTotal(s.counts), 0);
  const replayFailureCount = slots.reduce((sum, s) => sum + s.counts.replay_failed, 0);
  const pendingReplayCount = slots.reduce((sum, s) => sum + replayPendingTotal(s.counts), 0);
  const completeReplayCount = slots.reduce((sum, s) => sum + replayCompleteTotal(s.counts), 0);

  let summary = `Activity ${startLabel}–${endLabel}, peak ${formatClock(peakSlot.minuteIso, tz)}`;
  if (directErrorCount > 0) {
    const firstError = slots.find((s) => directErrorTotal(s.counts) > 0);
    summary += `, ${countLabel(directErrorCount, "error")}`;
    if (firstError) summary += ` starting ${formatClock(firstError.minuteIso, tz)}`;
  }
  if (replayFailureCount > 0) {
    const firstFailure = slots.find((s) => s.counts.replay_failed > 0);
    summary += `, ${countLabel(replayFailureCount, "replay failure", "replay failures")}`;
    if (firstFailure && directErrorCount === 0) {
      summary += ` starting ${formatClock(firstFailure.minuteIso, tz)}`;
    }
  }
  if (pendingReplayCount > 0) {
    summary += `, ${countLabel(pendingReplayCount, "replay pending", "replays pending")}`;
  }
  if (completeReplayCount > 0) {
    summary += `, ${countLabel(completeReplayCount, "replay complete", "replays complete")}`;
  }
  return summary;
}

interface HourFlameStripProps {
  /** ISO-8601 start of this hour bucket (e.g. "2026-05-17T14:00:00Z"). */
  hourStart: string;
  /**
   * Histogram buckets whose `ts` falls within [hourStart, hourStart+1h).
   * Zero-count minutes are simply absent (the histogram endpoint omits them).
   */
  buckets: IngestionHistogramBucket[];
  /** Bucket granularity in minutes (1 for "1m", 5 for "5m"). Determines slot count (60 / bucketMinutes). */
  bucketMinutes: number;
  /** Height in pixels. Default 16. */
  height?: number;
  /** Additional Tailwind classes on the outer wrapper. */
  className?: string;
  /** Fired when a minute segment is activated (click or keyboard). counts is null for an all-zero minute. */
  onMinuteClick?: (minuteIso: string, counts: IngestionHistogramCounts | null) => void;
  /** Test id for the outer wrapper. Defaults to "hour-flame-strip". */
  "data-testid"?: string;
}

/**
 * Status-stacked, clickable per-minute activity strip for one hour group.
 */
export function HourFlameStrip({
  hourStart,
  buckets,
  bucketMinutes,
  height = 16,
  className,
  onMinuteClick,
  "data-testid": testId = "hour-flame-strip",
}: HourFlameStripProps) {
  const tz = useTimezone();
  const [activeOffset, setActiveOffset] = useState<number | null>(null);

  const slots = useMemo((): MinuteSlot[] => {
    const hourStartMs = new Date(hourStart).getTime();
    const bucketByOffset = new Map<number, IngestionHistogramCounts>();
    if (!isNaN(hourStartMs)) {
      for (const b of buckets) {
        const ms = new Date(b.ts).getTime();
        if (isNaN(ms)) continue;
        const offset = Math.round((ms - hourStartMs) / 60_000);
        if (offset >= 0 && offset < 60) bucketByOffset.set(offset, b.counts);
      }
    }
    const slotCount = Math.max(1, Math.floor(60 / bucketMinutes));
    const out: MinuteSlot[] = [];
    for (let i = 0; i < slotCount; i++) {
      const offsetMinutes = i * bucketMinutes;
      const minuteIso = isNaN(hourStartMs)
        ? ""
        : new Date(hourStartMs + offsetMinutes * 60_000).toISOString();
      const counts = bucketByOffset.get(offsetMinutes) ?? ZERO_COUNTS;
      out.push({ offsetMinutes, minuteIso, counts, total: countsTotal(counts) });
    }
    return out;
  }, [hourStart, buckets, bucketMinutes]);

  const peak = Math.max(...slots.map((s) => s.total), 1);
  const ariaSummary = useMemo(() => buildAriaSummary(hourStart, slots, tz), [hourStart, slots, tz]);
  const activeSlot =
    activeOffset !== null ? slots.find((s) => s.offsetMinutes === activeOffset) : undefined;

  return (
    <div className={["relative", className].filter(Boolean).join(" ")} data-testid={testId}>
      <div role="group" aria-label={ariaSummary} className="flex items-end" style={{ width: 60, height }}>
        {slots.map((slot) => {
          const isEmpty = slot.total === 0;
          const barH = isEmpty ? 1 : Math.max(1, (slot.total / peak) * height);
          // Stack order, first-rendered (top of bar, most visible) to
          // last-rendered (baseline): terminal failures, pending replay,
          // completed replay, ingested, filtered/skipped.
          const segments = isEmpty
            ? []
            : [
                { count: errorTotal(slot.counts), className: "bg-destructive" },
                { count: replayPendingTotal(slot.counts), className: "bg-[var(--dim)]" },
                { count: replayCompleteTotal(slot.counts), className: "bg-[var(--green)]" },
                { count: slot.counts.ingested, className: "bg-foreground/30" },
                { count: slot.counts.filtered + slot.counts.skipped, className: "bg-foreground/10" },
              ].filter((seg) => seg.count > 0);

          return (
            <button
              key={slot.offsetMinutes}
              type="button"
              onClick={() => onMinuteClick?.(slot.minuteIso, isEmpty ? null : slot.counts)}
              onMouseEnter={() => setActiveOffset(slot.offsetMinutes)}
              onMouseLeave={() => setActiveOffset((prev) => (prev === slot.offsetMinutes ? null : prev))}
              onFocus={() => setActiveOffset(slot.offsetMinutes)}
              onBlur={() => setActiveOffset((prev) => (prev === slot.offsetMinutes ? null : prev))}
              className="relative flex flex-col justify-end shrink-0 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring focus-visible:z-10"
              style={{ width: bucketMinutes, height }}
              aria-label={formatMinuteSummary(slot, tz)}
              data-testid="hour-strip-minute"
              data-minute-iso={slot.minuteIso}
              data-has-error={errorTotal(slot.counts) > 0 ? "true" : undefined}
            >
              {isEmpty ? (
                <div className="w-full bg-border" style={{ height: barH }} />
              ) : (
                <div className="w-full flex flex-col justify-end" style={{ height: barH }}>
                  {segments.map((seg, i) => (
                    <div
                      key={i}
                      className={seg.className}
                      style={{ height: `${(seg.count / slot.total) * 100}%` }}
                    />
                  ))}
                </div>
              )}
            </button>
          );
        })}
      </div>

      {activeSlot && (
        <div
          className="absolute -top-6 left-0 whitespace-nowrap font-mono text-[10px] text-foreground bg-background border border-border rounded px-1.5 py-0.5 pointer-events-none z-10"
          data-testid="hour-strip-tooltip"
        >
          {formatMinuteSummary(activeSlot, tz)}
        </div>
      )}
    </div>
  );
}
