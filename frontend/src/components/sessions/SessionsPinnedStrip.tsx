// ---------------------------------------------------------------------------
// SessionsPinnedStrip -- pins actionable session rows above the chronological
// flow on /sessions (bu-ptaub, follow-up from bu-86c4c.17 / PR #2875's
// judgment layer, which deliberately deferred this slice to avoid half-doing
// it).
//
// Two row kinds, both bounded and sourced from canonical session data
// SessionsPage already fetches via useSessions -- no parallel data model:
//   - RUNNING sessions: live-ticking elapsed time (client-side tick off
//     started_at via useTickingNow -- no per-second refetch of session data).
//   - Recent FAILURES: "recent" = the same lookback window + row cap the
//     verdict opener above already uses for its failure-clustering clause
//     (SESSIONS_VERDICT_WINDOW_HOURS, capped at PINNED_FAILURES_LIMIT) --
//     one consistent meaning of "recent" on this page. Each gets an inline,
//     truncated error excerpt fetched via useSessionErrorExcerpts, which
//     reuses the exact query the session-detail drawer uses (same cache key)
//     -- so clicking through to the full drawer is a cache hit, not a
//     duplicate fetch.
//
// Design language: pinned != alarm-styled. No red banner, no animate-pulse
// (forbidden by eslint anyway) -- a plain bordered section reusing the same
// StatusBadge/ButlerMark vocabulary as the main SessionTable, so a pinned row
// reads as "the same kind of row, just surfaced first," not a klaxon. Errors
// use the StatusBadge "Failed" destructive token, not a custom red.
//
// [decision] Collapses to nothing (no "all clear" line restated) when neither
// list has anything to pin, rather than always rendering a calm one-liner
// (the NeedsYouStrip precedent). Rationale: SessionsVerdictOpener directly
// above already states the calm-day headline ("No sessions failed in the
// last 24h..."); a second always-visible "nothing pinned" line would restate
// the same fact. Reversible: yes (purely a rendering choice, no data shape
// implications).
// ---------------------------------------------------------------------------

import type { KeyboardEvent, ReactNode } from "react";

import type { SessionSummary } from "@/api/types";
import { ButlerMark } from "@/components/ui/ButlerMark";
import { StatusBadge } from "@/components/sessions/StatusBadge";
import { SourceDegradedNote } from "@/components/ui/query-boundary";
import { useSessionErrorExcerpts } from "@/hooks/use-sessions";
import { useTickingNow } from "@/hooks/use-ticking-now";
import { elapsedText } from "@/lib/session-elapsed";
import { truncate } from "@/lib/truncate";
import { cn } from "@/lib/utils";

/** Inline error excerpt length -- long enough to be identifying, short enough
 * to stay a "strip" row rather than wrapping onto several lines. Full text
 * remains one click away in the session-detail drawer. */
const ERROR_EXCERPT_MAX = 100;

const PROMPT_MAX = 70;

// ---------------------------------------------------------------------------
// PinnedRow
// ---------------------------------------------------------------------------

interface PinnedRowProps {
  session: SessionSummary;
  trailing: ReactNode;
  selected: boolean;
  onClick?: () => void;
}

function PinnedRow({ session, trailing, selected, onClick }: PinnedRowProps) {
  const interactive = Boolean(onClick);

  function handleKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    if (!interactive) return;
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onClick?.();
    }
  }

  return (
    <li>
      <div
        data-testid="pinned-session-row"
        data-session-id={session.id}
        role={interactive ? "button" : undefined}
        tabIndex={interactive ? 0 : undefined}
        aria-selected={interactive ? selected : undefined}
        aria-label={
          interactive
            ? `Open session detail for ${session.butler ?? "session"}: ${truncate(session.prompt, 80)}`
            : undefined
        }
        onClick={onClick}
        onKeyDown={handleKeyDown}
        className={cn(
          "flex flex-wrap items-baseline gap-x-2 gap-y-0.5 rounded-md px-2 py-1.5 text-sm",
          selected && "bg-muted",
          interactive &&
            "cursor-pointer hover:bg-muted/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset",
        )}
      >
        <StatusBadge success={session.success} />
        {session.butler && (
          <span className="inline-flex items-center gap-1.5 text-foreground">
            <ButlerMark name={session.butler} tone="neutral" />
            {session.butler}
          </span>
        )}
        <span className="max-w-md truncate text-muted-foreground" title={session.prompt}>
          {truncate(session.prompt, PROMPT_MAX)}
        </span>
        <span className="ml-auto text-xs">{trailing}</span>
      </div>
    </li>
  );
}

// ---------------------------------------------------------------------------
// SessionsPinnedStrip
// ---------------------------------------------------------------------------

export interface SessionsPinnedStripProps {
  /** GET /api/sessions?status=running&limit=<N> -- already fetched by
   * SessionsPage for the verdict opener's "nearest running" note; this strip
   * pins all of them (bounded by the caller's limit), not just the nearest. */
  runningSessions: SessionSummary[];
  /** GET /api/sessions?status=failed&since=<window>&limit=<N> -- bounded,
   * recency-windowed failures (see module doc for the shared "recent"
   * definition with SessionsVerdictOpener). */
  recentFailures: SessionSummary[];
  /** True when the running-sessions query (above) errored. Must NOT be
   * conflated with "no running sessions" -- a killed backend renders a
   * degraded note instead of silently collapsing the strip (fleet-wide
   * degraded-mode convention, see butlers/CLAUDE.md API Conventions). */
  runningError?: boolean;
  /** True when the recent-failures query (above) errored. Same rule as
   * `runningError`: never let a fetch failure read as "no recent failures". */
  recentFailuresError?: boolean;
  /** Butler pools dropped from the running-sessions list fan-out
   * (KeysetMeta.sources_degraded, bu-hmdqz.12). A partial per-pool drop on an
   * otherwise-200 response — distinct from `runningError` (whole-request
   * failure). Named so the pinned running rows never read as the full set. */
  runningSourcesDegraded?: string[];
  /** Butler pools dropped from the recent-failures list fan-out. Same rule as
   * `runningSourcesDegraded`. */
  recentFailuresSourcesDegraded?: string[];
  onSessionClick?: (session: SessionSummary) => void;
  /** Mirrors ?selected= (same convention as SessionTable). */
  selectedId?: string | null;
}

export function SessionsPinnedStrip({
  runningSessions,
  recentFailures,
  runningError = false,
  recentFailuresError = false,
  runningSourcesDegraded = [],
  recentFailuresSourcesDegraded = [],
  onSessionClick,
  selectedId = null,
}: SessionsPinnedStripProps) {
  // Ticks the running rows' elapsed labels forward on their own (no refetch).
  const now = useTickingNow();
  // Best-effort inline error excerpts for the pinned failures only -- bounded
  // to whatever the caller passed in `recentFailures` (a handful of rows).
  const errorsById = useSessionErrorExcerpts(recentFailures);

  const hasDegradedSource =
    runningError ||
    recentFailuresError ||
    runningSourcesDegraded.length > 0 ||
    recentFailuresSourcesDegraded.length > 0;

  // Nothing to pin AND both sources are healthy -> collapse (see module doc
  // decision note). A degraded source always renders its note, even with
  // zero rows to show, so a fetch failure OR a partial per-pool drop never
  // reads as "nothing pinned".
  if (runningSessions.length === 0 && recentFailures.length === 0 && !hasDegradedSource) {
    return null;
  }

  return (
    <div
      role="group"
      aria-label="Pinned sessions"
      data-testid="sessions-pinned-strip"
      className="border-b border-border/60 px-6 py-3"
    >
      <span className="mb-2 block font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
        Pinned
      </span>
      {hasDegradedSource && (
        <div className="mb-2 flex flex-col gap-1.5">
          {runningError && (
            <SourceDegradedNote label="Running sessions" detail="unavailable" />
          )}
          {!runningError && runningSourcesDegraded.length > 0 && (
            <SourceDegradedNote
              label="Running sessions"
              detail={`partial: ${runningSourcesDegraded.join(", ")} unreachable`}
            />
          )}
          {recentFailuresError && (
            <SourceDegradedNote label="Recent failures" detail="unavailable" />
          )}
          {!recentFailuresError && recentFailuresSourcesDegraded.length > 0 && (
            <SourceDegradedNote
              label="Recent failures"
              detail={`partial: ${recentFailuresSourcesDegraded.join(", ")} unreachable`}
            />
          )}
        </div>
      )}
      <ul className="flex flex-col gap-0.5">
        {runningSessions.map((session) => (
          <PinnedRow
            key={`running-${session.id}`}
            session={session}
            selected={selectedId === session.id}
            onClick={onSessionClick ? () => onSessionClick(session) : undefined}
            trailing={
              <span className="text-muted-foreground">
                {elapsedText(session.started_at, now) ?? "running"}
              </span>
            }
          />
        ))}
        {recentFailures.map((session) => {
          const error = errorsById.get(session.id);
          return (
            <PinnedRow
              key={`failed-${session.id}`}
              session={session}
              selected={selectedId === session.id}
              onClick={onSessionClick ? () => onSessionClick(session) : undefined}
              trailing={
                <span className="text-destructive" data-testid="pinned-failure-excerpt">
                  {error ? truncate(error, ERROR_EXCERPT_MAX) : "no error detail"}
                </span>
              }
            />
          );
        })}
      </ul>
    </div>
  );
}
