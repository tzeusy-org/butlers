/**
 * DispatchTicksCell — per-butler dispatch-ticks cell for a Timeline ledger
 * row (bu-4utdw.8, "micro-flame").
 *
 * Promotes a compact summary of the EventDrawer sessions-tab flamegraph
 * (EventDrawer.tsx's `DrawerSessionsTab`) into the row itself: one tick per
 * butler session, width proportional to that session's duration, so "who
 * reacted to this signal and how hard" is visible without opening the
 * drawer. The full flamegraph (with butler names, per-session links, and
 * per-step detail) remains the drawer's job; this cell is a summary, not a
 * replacement.
 *
 * Color: the ingestion dispatch console spec ("Dispatch Visual Language",
 * openspec/specs/dashboard-ingestion-dispatch-console/spec.md) states
 * "butler hues only on letter marks" as a binding visual contract, and
 * StatusBadge.tsx's RowStatus already documents dispatch ticks as a
 * *state*-color carrier (alongside the hour strip), not a butler-hue
 * carrier. So ticks are neutral foreground bars; a failed session renders
 * destructive-red (matching the hour strip's error-segment color and the
 * row status dot). Butler identity is surfaced in the tick's tooltip
 * instead of via hue, per the bead's own fallback guidance — this
 * deliberately diverges from EventDrawer's flamegraph, which already fills
 * bars with `butlerHueVar()` (a pre-existing, narrower exception scoped to
 * the drawer's click-gated detail view, not audited here).
 *
 * Data: the `sessions` array is already provided by the events list
 * response (bu-4utdw.3), capped at 8 entries server-side — no extra
 * requests fired by this cell.
 *
 * Spec: openspec/specs/dashboard-ingestion-dispatch-console/spec.md
 *       §"Timeline Ledger"
 */

import type { IngestionEventListSessionSummary } from "@/api/index.ts";

// ---------------------------------------------------------------------------
// Layout constants
// ---------------------------------------------------------------------------

/** Px budget for the tick strip itself, leaving room in the ~120px column for the trailing count. */
const TICK_BUDGET_PX = 84;
const TICK_GAP_PX = 2;
const MIN_TICK_WIDTH_PX = 3;
/** Server caps `sessions` at 8 (bu-4utdw.3); slice defensively so the width math in
 * computeTickWidths keeps holding even if that upstream cap ever changes. */
const MAX_RENDERED_TICKS = 8;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatDurationMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || ms < 0) return "—";
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  return `${(ms / 60_000).toFixed(1)}m`;
}

function formatCost(usd: number | undefined | null): string {
  if (usd === undefined || usd === null) return "—";
  if (usd === 0) return "$0.00";
  if (usd < 0.001) return "<$0.001";
  return `$${usd.toFixed(4)}`;
}

/**
 * Per-tick width, proportional to duration among this row's own sessions,
 * clamped to [MIN_TICK_WIDTH_PX, the largest width that still lets every
 * tick + gap fit inside TICK_BUDGET_PX]. Pure so it's unit-testable without
 * a DOM.
 */
// eslint-disable-next-line react-refresh/only-export-components
export function computeTickWidths(sessions: IngestionEventListSessionSummary[]): number[] {
  const n = sessions.length;
  if (n === 0) return [];
  const durations = sessions.map((s) => Math.max(0, s.duration_ms ?? 0));
  const maxDuration = Math.max(...durations, 1);
  const totalGap = TICK_GAP_PX * Math.max(0, n - 1);
  const maxPerTick = Math.max(MIN_TICK_WIDTH_PX, (TICK_BUDGET_PX - totalGap) / n);
  return durations.map((d) => {
    const proportional = (d / maxDuration) * maxPerTick;
    return Math.max(MIN_TICK_WIDTH_PX, Math.min(maxPerTick, proportional));
  });
}

function tickTitle(s: IngestionEventListSessionSummary): string {
  const parts = [s.butler_name, formatDurationMs(s.duration_ms), formatCost(s.cost_usd)];
  if (s.success === false) parts.push("failed");
  return parts.join(" · ");
}

/** Accessible summary for the whole cell, standing in for the per-tick hover tooltips. */
function buildAriaLabel(sessions: IngestionEventListSessionSummary[], sessionCount: number): string {
  const noun = sessionCount === 1 ? "session" : "sessions";
  const detail = sessions.map((s) => tickTitle(s)).join(", ");
  return `${sessionCount} butler ${noun}: ${detail}. Open drawer.`;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export interface DispatchTicksCellProps {
  /**
   * Compact per-session summaries for this event, capped at 8 by the API.
   * Typed as required, but events from older fixtures/callers that predate
   * bu-4utdw.3's rollup enrichment can omit it at runtime — default to empty
   * rather than crash the row.
   */
  sessions: IngestionEventListSessionSummary[] | null | undefined;
  /** Total session count for this event (may exceed sessions.length if capped upstream). */
  sessionCount: number | null | undefined;
  /** Opens (or, if already open, closes) the row's drawer — defaults to the sessions tab. */
  onOpenDrawer: () => void;
}

/**
 * Compact per-butler dispatch-ticks cell: one tick per session, width
 * proportional to duration, red for a failed session, trailing mono count
 * when more than one session fired. Rows with no sessions render a muted
 * em-dash instead of an interactive cell.
 */
export function DispatchTicksCell({
  sessions: sessionsProp,
  sessionCount: sessionCountProp,
  onOpenDrawer,
}: DispatchTicksCellProps) {
  const sessions = sessionsProp ?? [];
  const sessionCount = sessionCountProp ?? sessions.length;

  if (sessions.length === 0) {
    return (
      <span
        className="font-mono text-[11px] text-muted-foreground select-none"
        data-testid="dispatch-ticks-empty"
      >
        —
      </span>
    );
  }

  const renderedSessions = sessions.slice(0, MAX_RENDERED_TICKS);
  const widths = computeTickWidths(renderedSessions);

  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation();
        onOpenDrawer();
      }}
      className="flex items-center gap-1 rounded focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring focus-visible:ring-inset"
      aria-label={buildAriaLabel(renderedSessions, sessionCount)}
      data-testid="dispatch-ticks-cell"
    >
      <span className="flex items-end gap-[2px] h-2 shrink-0">
        {renderedSessions.map((s, i) => (
          <span
            key={`${s.butler_name}-${i}`}
            className={[
              "h-2 rounded-[1px]",
              s.success === false ? "bg-destructive" : "bg-foreground/40",
            ].join(" ")}
            style={{ width: widths[i] }}
            title={tickTitle(s)}
            data-testid="dispatch-tick"
            data-failed={s.success === false ? "true" : undefined}
          />
        ))}
      </span>
      {sessionCount > 1 && (
        <span className="font-mono text-[10px] tabular-nums text-muted-foreground" data-testid="dispatch-tick-count">
          {sessionCount}
        </span>
      )}
    </button>
  );
}
