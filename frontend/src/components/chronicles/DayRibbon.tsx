// ---------------------------------------------------------------------------
// DayRibbon — horizontal day timeline with a ghost Intent track (IEA §10)
//
// The IEA reframe replaces the misleading source-shaped pie with a plan-vs-
// reality ribbon:
//   - a faint "ghost" Intent track (calendar/planned blocks) along the top,
//   - the lived Activity blocks below, coloured by life-balance lane.
//
// Each lived-activity block is clickable to reveal its evidence chain ("why is
// this counted?") — the click opens the EpisodeDrawer, which now renders the
// GET /episodes/{id}/evidence-chain response.
//
// Intent (calendar) episodes are NOT counted toward any lane (they are the
// intent layer); they render only as the faint ghost track so plan-vs-reality
// is visible. Activity is everything else — coloured by its lane.
//
// Privacy: restricted episodes never reach the frontend (server-excluded).
// Sensitive episodes render as masked (hatched) blocks with only their lane +
// duration exposed, matching the Gantt privacy contract (bu-6c5i6).
//
// Presentational: episodes are passed in as a prop so the component is
// trivially unit-testable via renderToStaticMarkup (no react-query needed).
// ---------------------------------------------------------------------------

import { useMemo } from "react";

import type { ChroniclerEpisode } from "@/api/types";
import { categoryForSource, LANE_TAXONOMY, type Category } from "./lane-taxonomy";

export interface DayRibbonProps {
  episodes: ChroniclerEpisode[];
  windowStart: Date;
  windowEnd: Date;
  /** Called with the episode ID when a lived-activity block is clicked. */
  onEpisodeClick?: (episodeId: string) => void;
}

interface PlacedBlock {
  episode: ChroniclerEpisode;
  leftPct: number;
  widthPct: number;
  isOpen: boolean;
  isSensitive: boolean;
}

/** Backend `category` when present, else derive from (source_name, type). */
function laneFor(episode: ChroniclerEpisode): Category {
  const fromBackend = episode.category;
  if (fromBackend && fromBackend in LANE_TAXONOMY) {
    return fromBackend as Category;
  }
  return categoryForSource(episode.source_name, episode.episode_type);
}

/** Intent (calendar) episodes are the ghost track; everything else is lived. */
function isIntentEpisode(episode: ChroniclerEpisode): boolean {
  return episode.source_name.startsWith("google_calendar");
}

function place(
  episode: ChroniclerEpisode,
  startMs: number,
  endMs: number,
  spanMs: number,
): PlacedBlock {
  const s = new Date(episode.canonical_start_at).getTime();
  const rawEnd = episode.canonical_end_at
    ? new Date(episode.canonical_end_at).getTime()
    : endMs;
  const isOpen = episode.canonical_end_at == null;
  const clampedStart = Math.max(startMs, Math.min(s, endMs));
  const clampedEnd = Math.max(clampedStart, Math.min(rawEnd, endMs));
  const leftPct = ((clampedStart - startMs) / spanMs) * 100;
  const widthPct = Math.max(0.5, ((clampedEnd - clampedStart) / spanMs) * 100);
  return {
    episode,
    leftPct,
    widthPct,
    isOpen,
    isSensitive: episode.canonical_privacy === "sensitive",
  };
}

function formatBlockDuration(startIso: string, endIso: string | null): string {
  if (!endIso) return "ongoing";
  const ms = new Date(endIso).getTime() - new Date(startIso).getTime();
  if (ms <= 0) return "—";
  const mins = Math.round(ms / 60_000);
  if (mins < 60) return `${mins}m`;
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  return m === 0 ? `${h}h` : `${h}h ${m}m`;
}

export function DayRibbon({ episodes, windowStart, windowEnd, onEpisodeClick }: DayRibbonProps) {
  const startMs = windowStart.getTime();
  const endMs = windowEnd.getTime();
  const spanMs = Math.max(1, endMs - startMs);

  const { intentBlocks, activityBlocks } = useMemo(() => {
    const intent: PlacedBlock[] = [];
    const activity: PlacedBlock[] = [];
    for (const ep of episodes) {
      const block = place(ep, startMs, endMs, spanMs);
      if (isIntentEpisode(ep)) intent.push(block);
      else activity.push(block);
    }
    intent.sort((a, b) => a.leftPct - b.leftPct);
    activity.sort((a, b) => a.leftPct - b.leftPct);
    return { intentBlocks: intent, activityBlocks: activity };
  }, [episodes, startMs, endMs, spanMs]);

  if (episodes.length === 0) {
    return (
      <div
        className="rounded-md border border-dashed py-10 text-center text-sm text-muted-foreground"
        style={{ borderColor: "var(--border)" }}
        data-testid="day-ribbon-empty"
      >
        No activity recorded for this window.
      </div>
    );
  }

  return (
    <div className="space-y-2" data-testid="day-ribbon">
      {/* Ghost Intent track — planned/calendar blocks, faint, non-interactive. */}
      <div className="space-y-1">
        <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
          Planned
        </span>
        <div
          className="relative h-4 w-full rounded-sm bg-[var(--muted)]/30"
          data-testid="day-ribbon-intent-track"
          aria-label="Planned calendar blocks (intent)"
        >
          {intentBlocks.map((b) => (
            <div
              key={b.episode.id}
              className="absolute top-0 h-full rounded-sm border border-dashed"
              style={{
                left: `${b.leftPct}%`,
                width: `${b.widthPct}%`,
                borderColor: "var(--muted-foreground)",
                background:
                  "repeating-linear-gradient(45deg, var(--muted-foreground) 0, var(--muted-foreground) 1px, transparent 1px, transparent 5px)",
                opacity: 0.35,
              }}
              title={
                b.isSensitive
                  ? `Planned · ${formatBlockDuration(b.episode.canonical_start_at, b.episode.canonical_end_at)}`
                  : `${b.episode.canonical_title ?? "Planned"} · ${formatBlockDuration(b.episode.canonical_start_at, b.episode.canonical_end_at)}`
              }
              data-testid={`day-ribbon-intent-${b.episode.id}`}
            />
          ))}
        </div>
      </div>

      {/* Lived Activity track — coloured by lane, clickable for evidence chain. */}
      <div className="space-y-1">
        <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
          Lived
        </span>
        <div
          className="relative h-7 w-full rounded-sm bg-[var(--muted)]/20"
          data-testid="day-ribbon-activity-track"
          aria-label="Lived activity blocks"
        >
          {activityBlocks.map((b) => {
            const lane = laneFor(b.episode);
            const config = LANE_TAXONOMY[lane];
            const label = b.isSensitive
              ? `${config.label} · ${formatBlockDuration(b.episode.canonical_start_at, b.episode.canonical_end_at)}`
              : `${b.episode.canonical_title ?? config.label} · ${formatBlockDuration(b.episode.canonical_start_at, b.episode.canonical_end_at)}`;
            return (
              <button
                key={b.episode.id}
                type="button"
                onClick={() => onEpisodeClick?.(b.episode.id)}
                className="absolute top-0 h-full rounded-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)]"
                style={{
                  left: `${b.leftPct}%`,
                  width: `${b.widthPct}%`,
                  backgroundColor: config.hex,
                  ...(b.isSensitive
                    ? {
                        backgroundImage:
                          "repeating-linear-gradient(45deg, rgba(255,255,255,0.35) 0, rgba(255,255,255,0.35) 2px, transparent 2px, transparent 4px)",
                      }
                    : {}),
                  ...(b.isOpen ? { borderRight: "2px dashed var(--background)" } : {}),
                }}
                title={label}
                aria-label={`${label}. Show evidence chain.`}
                data-testid={`day-ribbon-activity-${b.episode.id}`}
              />
            );
          })}
        </div>
      </div>
    </div>
  );
}
