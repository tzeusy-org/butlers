// ---------------------------------------------------------------------------
// Shared duration formatters [bu-sd0l7.3]
//
// A 2026-07-10 audit found 12 component-local `formatDuration`/
// `formatDurationMs` clones. They were not all the same function: three
// genuinely distinct rendering contracts had each been independently
// duplicated at 2+ call sites:
//
// - formatDurationMs: sub-second precision — "Xms" / "X(.Y)s" / "Xm Ys"
//   combo below an hour. Was identical in components/sessions/SessionDossier
//   and SessionTable.
// - formatDurationCompact: coarser rounding — "Xs" / "Xm" / "Xh( Ym)", no
//   sub-second tier. Was identical in components/chronicles/
//   GanttSwimlaneInner and (modulo the start/end-to-ms diff) EpisodeDrawer.
// - formatDurationTicks: "Xms" / "X.Ys" / "X.Ym" — no combined m+s tier. Was
//   identical in components/ingestion/timeline/DispatchTicksCell and (modulo
//   the start/end-to-ms diff) EventDrawer.
//
// A few remaining local `formatDuration` declarations are genuinely
// different contracts (relative "X ago" suffix, HH:MM clock format,
// day-scale durations with no sub-minute tier) and were deliberately left
// local rather than forced onto one of these three shapes — see the
// eslint-disable comment at each for why.
// ---------------------------------------------------------------------------

/**
 * Format a millisecond duration with sub-second precision.
 *
 * - `null`/`undefined` renders as "—".
 * - Below one second: "Xms".
 * - Below one minute: whole seconds render as "Xs"; fractional seconds
 *   render to one decimal place, e.g. "1.5s".
 * - One minute and above: "Xm" alone, or "Xm Ys" when there is a remaining
 *   whole-second component.
 */
export function formatDurationMs(ms: number | null | undefined): string {
  if (ms == null) return "—";
  if (ms < 1000) return `${ms}ms`;
  const totalSeconds = Math.floor(ms / 1000);
  const frac = ms / 1000;
  if (totalSeconds < 60) {
    return frac % 1 === 0 ? `${totalSeconds}s` : `${frac.toFixed(1)}s`;
  }
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return seconds > 0 ? `${minutes}m ${seconds}s` : `${minutes}m`;
}

/**
 * Format a millisecond duration with coarse, rounded units and no
 * sub-second tier.
 *
 * - Below one minute: rounded whole seconds, "Xs".
 * - Below one hour: rounded whole minutes, "Xm".
 * - One hour and above: "Xh" alone, or "Xh Ym" when there is a remaining
 *   whole-minute component.
 */
export function formatDurationCompact(ms: number): string {
  if (ms < 60_000) return `${Math.round(ms / 1000)}s`;
  if (ms < 3_600_000) return `${Math.round(ms / 60_000)}m`;
  const h = Math.floor(ms / 3_600_000);
  const m = Math.round((ms % 3_600_000) / 60_000);
  return m === 0 ? `${h}h` : `${h}h ${m}m`;
}

/**
 * Format a millisecond duration for fine-grained per-tick/per-event display.
 *
 * - `null`/`undefined`/negative renders as "—".
 * - Below one second: "Xms".
 * - Below one minute: "X.Ys" (one decimal place).
 * - One minute and above: "X.Ym" (one decimal place) — no combined m+s tier.
 */
export function formatDurationTicks(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || ms < 0) return "—";
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  return `${(ms / 60_000).toFixed(1)}m`;
}
