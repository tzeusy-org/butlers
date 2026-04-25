// ---------------------------------------------------------------------------
// GanttSwimlaneInner — hand-rolled SVG Gantt (bu-ig72b.28)
//
// Renders one swimlane per category with overlap-aware stacked bars.
// No new dependencies — pure SVG with Tailwind for chrome elements.
//
// Layout:
//   - Label column (fixed width) on the left
//   - SVG time-bar area on the right, scaled to the active time window
//
// Open episodes (end_at = null) are clipped to the window end and rendered
// with a dashed right edge + arrow indicator.
//
// Sensitive episodes (canonical_privacy = "sensitive") are rendered as
// masked (hatched) bars and show only "Private activity" in the tooltip.
//
// A hover tooltip (Radix UI primitive) surfaces: title, source, precision,
// duration, and a drilldown link to /chronicles/episodes/{id}.
// ---------------------------------------------------------------------------

import { useMemo } from "react"

import type { ChroniclerEpisode } from "@/api/types"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import type { Category } from "./lane-taxonomy"
import { LANE_TAXONOMY } from "./lane-taxonomy"

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const LANE_HEIGHT = 20          // px per row within a swimlane
const LANE_GAP = 4              // px gap between stacked rows
const LANE_PADDING_TOP = 6      // px above first bar in a lane
const LANE_PADDING_BOTTOM = 6   // px below last bar in a lane
const LABEL_WIDTH = 90          // px for the lane label column
const BAR_RADIUS = 3            // px border-radius on bars
const OPEN_ARROW_WIDTH = 8      // px for the open-episode arrowhead

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface PositionedEpisode {
  episode: ChroniclerEpisode
  row: number           // 0-indexed stack row within the lane
  xPct: number          // left edge as fraction [0,1] of the window
  widthPct: number      // width as fraction [0,1] of the window
  isOpen: boolean       // end_at was null; clipped to window end
}

interface LaneLayout {
  category: Category
  episodes: PositionedEpisode[]
  rowCount: number      // how many stacked rows this lane needs
  yOffset: number       // cumulative y offset from top of the SVG bar area
  laneHeight: number    // total px height of this lane
}

export interface GanttSwimlaneInnerProps {
  episodes: ChroniclerEpisode[]
  windowStart: Date
  windowEnd: Date
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Derive the LANE_TAXONOMY category for an episode from source_name. */
function categoryFor(episode: ChroniclerEpisode): Category {
  const name = episode.source_name as Category
  return name in LANE_TAXONOMY ? name : "other"
}

/** Parse a UTC ISO string to ms. Returns NaN if falsy. */
function parseMs(iso: string | null | undefined): number {
  if (!iso) return NaN
  return new Date(iso).getTime()
}

/** Format a duration in ms as a human-readable string. */
function formatDuration(ms: number): string {
  if (ms < 60_000) return `${Math.round(ms / 1000)}s`
  if (ms < 3_600_000) return `${Math.round(ms / 60_000)}m`
  const h = Math.floor(ms / 3_600_000)
  const m = Math.round((ms % 3_600_000) / 60_000)
  return m === 0 ? `${h}h` : `${h}h ${m}m`
}

/**
 * Assign stacked row indices to episodes within a lane so that no two
 * overlapping episodes share the same row.
 *
 * Uses a greedy left-to-right interval coloring: iterate episodes sorted by
 * start time and assign the lowest available row that doesn't overlap.
 */
function assignRows(
  episodes: ChroniclerEpisode[],
  windowStartMs: number,
  windowEndMs: number,
): PositionedEpisode[] {
  if (episodes.length === 0) return []

  const windowDuration = windowEndMs - windowStartMs
  if (windowDuration <= 0) return []

  // Sort by start time so greedy assignment is valid.
  const sorted = [...episodes].sort(
    (a, b) => parseMs(a.canonical_start_at) - parseMs(b.canonical_start_at),
  )

  // For each row, track the rightmost end time (ms) already placed.
  const rowEnds: number[] = []

  return sorted.map((ep): PositionedEpisode => {
    const startMs = parseMs(ep.canonical_start_at)
    const rawEndMs = parseMs(ep.canonical_end_at)
    const isOpen = isNaN(rawEndMs) || ep.canonical_end_at === null
    const endMs = isOpen ? windowEndMs : rawEndMs

    // Clamp to window bounds for display.
    const clampedStart = Math.max(startMs, windowStartMs)
    const clampedEnd = Math.min(endMs, windowEndMs)

    const xPct = (clampedStart - windowStartMs) / windowDuration
    const widthPct = Math.max(0, (clampedEnd - clampedStart) / windowDuration)

    // Find the lowest row whose last episode ends before this one starts.
    // If no such row exists, open a new row.
    let assignedRow = -1
    for (let r = 0; r < rowEnds.length; r++) {
      if (rowEnds[r] <= startMs + 1) {
        assignedRow = r
        rowEnds[r] = endMs
        break
      }
    }
    if (assignedRow === -1) {
      assignedRow = rowEnds.length
      rowEnds.push(endMs)
    }

    return { episode: ep, row: assignedRow, xPct, widthPct, isOpen }
  })
}

/**
 * Build per-lane layout from a flat list of episodes, sorted by LANE_TAXONOMY
 * sortOrder. Lanes with no episodes are omitted.
 */
function buildLanes(
  episodes: ChroniclerEpisode[],
  windowStartMs: number,
  windowEndMs: number,
): LaneLayout[] {
  // Group by category.
  const grouped = new Map<Category, ChroniclerEpisode[]>()
  for (const ep of episodes) {
    const cat = categoryFor(ep)
    let arr = grouped.get(cat)
    if (!arr) {
      arr = []
      grouped.set(cat, arr)
    }
    arr.push(ep)
  }

  if (grouped.size === 0) return []

  // Sort categories by LANE_TAXONOMY sortOrder.
  const sorted = [...grouped.entries()].sort(
    ([a], [b]) => LANE_TAXONOMY[a].sortOrder - LANE_TAXONOMY[b].sortOrder,
  )

  const lanes: LaneLayout[] = []
  let yOffset = 0

  for (const [category, catEpisodes] of sorted) {
    const positioned = assignRows(catEpisodes, windowStartMs, windowEndMs)
    const rowCount = positioned.length === 0 ? 1 : Math.max(...positioned.map((p) => p.row)) + 1
    const laneHeight =
      LANE_PADDING_TOP +
      rowCount * LANE_HEIGHT +
      Math.max(0, rowCount - 1) * LANE_GAP +
      LANE_PADDING_BOTTOM

    lanes.push({ category, episodes: positioned, rowCount, yOffset, laneHeight })
    yOffset += laneHeight
  }

  return lanes
}

/** Convert a Tailwind bg-* class to a rough hex/CSS colour for SVG fill. */
const COLOUR_MAP: Record<string, string> = {
  "bg-blue-600": "#2563eb",
  "bg-indigo-500": "#6366f1",
  "bg-purple-500": "#a855f7",
  "bg-violet-600": "#7c3aed",
  "bg-cyan-500": "#06b6d4",
  "bg-slate-500": "#64748b",
  "bg-amber-500": "#f59e0b",
  "bg-emerald-600": "#059669",
  "bg-slate-400": "#94a3b8",
}

function laneColour(category: Category): string {
  return COLOUR_MAP[LANE_TAXONOMY[category].colour] ?? "#94a3b8"
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

interface EpisodeBarProps {
  positioned: PositionedEpisode
  laneY: number           // y offset of this lane's top in the SVG
  svgWidth: number        // pixel width of the SVG bar area
  colour: string
  patternId: string | null  // hatch pattern id if sensitive, else null
  windowEndMs: number
}

function EpisodeBar({ positioned, laneY, svgWidth, colour, patternId, windowEndMs }: EpisodeBarProps) {
  const { episode, row, xPct, widthPct, isOpen } = positioned
  const isSensitive = episode.canonical_privacy === "sensitive"

  const x = xPct * svgWidth
  const w = Math.max(widthPct * svgWidth, 2) // minimum 2px so bars are visible
  const y = laneY + LANE_PADDING_TOP + row * (LANE_HEIGHT + LANE_GAP)
  const h = LANE_HEIGHT

  const barId = `bar-${episode.id}`

  const startMs = parseMs(episode.canonical_start_at)
  const rawEndMs = parseMs(episode.canonical_end_at)
  const endMs = isOpen ? windowEndMs : rawEndMs
  const durationMs = isNaN(startMs) || isNaN(endMs) ? null : endMs - startMs
  const durationLabel = durationMs !== null ? formatDuration(durationMs) : "?"
  const startLabel = isNaN(startMs) ? "?" : new Date(startMs).toLocaleTimeString()
  const endLabel = isOpen
    ? "ongoing"
    : isNaN(rawEndMs)
    ? "?"
    : new Date(rawEndMs).toLocaleTimeString()

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <g
          role="img"
          aria-label={isSensitive ? "Private activity" : (episode.canonical_title ?? episode.source_name)}
          data-testid={`gantt-bar-${episode.id}`}
          style={{ cursor: "pointer" }}
        >
          {/* Bar body */}
          <rect
            id={barId}
            x={x}
            y={y}
            width={isOpen ? Math.max(w - OPEN_ARROW_WIDTH, 2) : w}
            height={h}
            rx={BAR_RADIUS}
            ry={BAR_RADIUS}
            fill={isSensitive && patternId ? `url(#${patternId})` : colour}
            stroke={colour}
            strokeWidth={isSensitive ? 1 : 0}
            fillOpacity={isSensitive ? 1 : 0.85}
          />

          {/* Open episode: dashed right edge + arrow */}
          {isOpen && (
            <>
              <line
                x1={x + w - OPEN_ARROW_WIDTH}
                y1={y}
                x2={x + w - OPEN_ARROW_WIDTH}
                y2={y + h}
                stroke={colour}
                strokeWidth={1.5}
                strokeDasharray="3 2"
              />
              <polygon
                points={`
                  ${x + w - OPEN_ARROW_WIDTH},${y + h / 2 - 4}
                  ${x + w},${y + h / 2}
                  ${x + w - OPEN_ARROW_WIDTH},${y + h / 2 + 4}
                `}
                fill={colour}
                fillOpacity={0.85}
              />
            </>
          )}
        </g>
      </TooltipTrigger>
      <TooltipContent
        data-testid="gantt-tooltip"
        className="w-56 space-y-0.5 text-xs"
      >
        {isSensitive ? (
          <>
            <p className="font-medium">Private activity</p>
            <p className="text-yellow-600 mt-0.5">Sensitive</p>
          </>
        ) : (
          <>
            <p className="font-medium">{episode.canonical_title ?? episode.source_name}</p>
            <p className="opacity-70">
              Source: <span className="opacity-100">{episode.source_name}</span>
            </p>
            <p className="opacity-70">
              Precision: <span className="opacity-100">{episode.precision}</span>
            </p>
            <p className="opacity-70">
              Duration: <span className="opacity-100">{durationLabel}</span>
            </p>
            <p className="opacity-70">
              {startLabel} – {endLabel}
            </p>
            {isOpen && <p className="opacity-70 italic">Episode ongoing</p>}
            <p className="pt-0.5">
              <a
                href={`/chronicles/episodes/${episode.id}`}
                className="underline opacity-70 hover:opacity-100"
              >
                View details →
              </a>
            </p>
          </>
        )}
      </TooltipContent>
    </Tooltip>
  )
}

// ---------------------------------------------------------------------------
// Axis tick helpers
// ---------------------------------------------------------------------------

function buildTicks(windowStartMs: number, windowEndMs: number, count = 6): number[] {
  const step = (windowEndMs - windowStartMs) / (count - 1)
  return Array.from({ length: count }, (_, i) => windowStartMs + i * step)
}

function formatTickLabel(ms: number, windowDuration: number): string {
  const d = new Date(ms)
  if (windowDuration <= 2 * 86_400_000) {
    // ≤ 2 days: show time
    return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })
  }
  // > 2 days: show date
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" })
}

// ---------------------------------------------------------------------------
// Main inner component
// ---------------------------------------------------------------------------

const AXIS_HEIGHT = 24    // px for the time axis at the bottom
const SVG_WIDTH = 800     // logical pixel width (SVG viewBox width)

export function GanttSwimlaneInner({
  episodes,
  windowStart,
  windowEnd,
}: GanttSwimlaneInnerProps) {
  const windowStartMs = windowStart.getTime()
  const windowEndMs = windowEnd.getTime()
  const windowDuration = windowEndMs - windowStartMs

  const lanes = useMemo(
    () => buildLanes(episodes, windowStartMs, windowEndMs),
    [episodes, windowStartMs, windowEndMs],
  )

  const ticks = useMemo(
    () => buildTicks(windowStartMs, windowEndMs),
    [windowStartMs, windowEndMs],
  )

  // Empty state
  if (lanes.length === 0) {
    return (
      <div
        className="flex items-center justify-center rounded-md border border-dashed py-12 text-muted-foreground text-sm"
        data-testid="gantt-empty"
      >
        No episodes in this time window.
      </div>
    )
  }

  const totalSvgHeight = lanes.reduce((sum, l) => sum + l.laneHeight, 0) + AXIS_HEIGHT

  return (
    <TooltipProvider>
      <div className="relative" data-testid="gantt-container">
        {/* Label column + SVG area side-by-side */}
        <div className="flex">
          {/* Label column */}
          <div
            className="shrink-0 select-none"
            style={{ width: LABEL_WIDTH, paddingTop: 0 }}
            aria-hidden
          >
            {lanes.map((lane) => (
              <div
                key={lane.category}
                className="flex items-center text-xs font-medium text-muted-foreground pr-2"
                style={{ height: lane.laneHeight }}
              >
                <span
                  className="inline-block w-2.5 h-2.5 rounded-sm mr-1.5 shrink-0"
                  style={{ backgroundColor: laneColour(lane.category) }}
                />
                {LANE_TAXONOMY[lane.category].label}
              </div>
            ))}
            {/* Spacer for axis row */}
            <div style={{ height: AXIS_HEIGHT }} />
          </div>

          {/* SVG bar area — responsive width, logical viewBox */}
          <div className="grow min-w-0 relative" data-testid="gantt-svg-wrapper">
            <svg
              viewBox={`0 0 ${SVG_WIDTH} ${totalSvgHeight}`}
              preserveAspectRatio="none"
              width="100%"
              height={totalSvgHeight}
              aria-label="Gantt timeline"
              role="img"
            >
              {/* Hatch patterns for sensitive episodes — one per category colour */}
              <defs>
                {lanes.map((lane) => {
                  const colour = laneColour(lane.category)
                  const id = `hatch-${lane.category}`
                  return (
                    <pattern
                      key={id}
                      id={id}
                      patternUnits="userSpaceOnUse"
                      width={8}
                      height={8}
                      patternTransform="rotate(45)"
                    >
                      <rect width={8} height={8} fill={colour} fillOpacity={0.3} />
                      <line x1={0} y1={0} x2={0} y2={8} stroke={colour} strokeWidth={3} strokeOpacity={0.6} />
                    </pattern>
                  )
                })}
              </defs>

              {/* Lane background alternating stripes */}
              {lanes.map((lane, i) => (
                <rect
                  key={lane.category}
                  x={0}
                  y={lane.yOffset}
                  width={SVG_WIDTH}
                  height={lane.laneHeight}
                  fill={i % 2 === 0 ? "rgba(0,0,0,0.02)" : "rgba(0,0,0,0.04)"}
                />
              ))}

              {/* Tick grid lines */}
              {ticks.map((ms) => {
                const xPct = (ms - windowStartMs) / windowDuration
                const x = xPct * SVG_WIDTH
                return (
                  <line
                    key={ms}
                    x1={x}
                    y1={0}
                    x2={x}
                    y2={totalSvgHeight - AXIS_HEIGHT}
                    stroke="currentColor"
                    strokeOpacity={0.08}
                    strokeWidth={1}
                  />
                )
              })}

              {/* Episode bars per lane */}
              {lanes.map((lane) => {
                const colour = laneColour(lane.category)
                // Hatch pattern is defined once per category in <defs> above.
                const categoryPatternId = `hatch-${lane.category}`
                return lane.episodes.map((positioned) => (
                  <EpisodeBar
                    key={positioned.episode.id}
                    positioned={positioned}
                    laneY={lane.yOffset}
                    svgWidth={SVG_WIDTH}
                    colour={colour}
                    patternId={
                      positioned.episode.canonical_privacy === "sensitive"
                        ? categoryPatternId
                        : null
                    }
                    windowEndMs={windowEndMs}
                  />
                ))
              })}

              {/* Time axis */}
              {ticks.map((ms) => {
                const xPct = (ms - windowStartMs) / windowDuration
                const x = xPct * SVG_WIDTH
                return (
                  <g key={ms}>
                    <line
                      x1={x}
                      y1={totalSvgHeight - AXIS_HEIGHT}
                      x2={x}
                      y2={totalSvgHeight - AXIS_HEIGHT + 4}
                      stroke="currentColor"
                      strokeOpacity={0.4}
                      strokeWidth={1}
                    />
                    <text
                      x={x}
                      y={totalSvgHeight - 6}
                      textAnchor="middle"
                      fontSize={10}
                      fill="currentColor"
                      fillOpacity={0.5}
                    >
                      {formatTickLabel(ms, windowDuration)}
                    </text>
                  </g>
                )
              })}
            </svg>
          </div>
        </div>
      </div>
    </TooltipProvider>
  )
}
