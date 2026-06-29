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
// masked (hatched) bars and show only the duration + lane label in the
// tooltip (e.g. "Travel: 38 min"). The title and payload are masked.
// Restricted episodes (canonical_privacy = "restricted") remain fully
// hidden at the server layer and never reach the frontend.
// See bu-6c5i6 privacy contract in roster/chronicler/AGENTS.md.
//
// A hover tooltip (Radix UI primitive) surfaces: title, source, precision,
// and duration. Drilldown is via clicking the bar (opens EpisodeDrawer);
// no in-tooltip "View details" link is rendered.
//
// Calendar episode location pan (bu-ig72b.24):
//   Clicking a calendar-lane bar attempts to pan the map to the episode's
//   location field (read from payload.location).  If the field parses as a
//   "lat,lng" pair the map pans; otherwise the tooltip notes the location is
//   unparseable.  No geocoding is performed.
// ---------------------------------------------------------------------------

import { useMemo, useState } from "react"

import type { ChroniclerEpisode } from "@/api/types"
import { Badge } from "@/components/ui/badge"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import type { Category } from "./lane-taxonomy"
import { categoryForSource, LANE_TAXONOMY } from "./lane-taxonomy"
import { parseLatLng } from "./location-utils"
import { useMapPanTo } from "@/components/workspace/map-pan-store"
import { useChroniclesTimezone } from "./use-chronicles-timezone"
import { formatTimeInTz, formatGanttTickLabel } from "./tz-format"

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** All LANE_TAXONOMY categories sorted by their display order. Computed once
 * at module level so the reference is stable across renders. */
const ALL_CATEGORIES: Category[] = (Object.keys(LANE_TAXONOMY) as Category[]).sort(
  (a, b) => LANE_TAXONOMY[a].sortOrder - LANE_TAXONOMY[b].sortOrder,
)

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
  /** Called with the episode ID when the user explicitly clicks a bar. */
  onEpisodeClick?: (episodeId: string) => void
  /**
   * Optional playhead cursor position in epoch ms.
   * When set, a vertical cursor line is rendered at the corresponding x
   * position in the SVG bar area (D12 — scrubber drives Gantt cursor).
   */
  cursorMs?: number | null
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Derive the LANE_TAXONOMY category for an episode.
 *
 * Primary: read `episode.category` if present (set by the backend's
 * `aggregations.category_for`). Fallback: derive locally from
 * `(source_name, episode_type)` so the UI keeps working against API responses
 * that pre-date the `category` field.
 */
function categoryFor(episode: ChroniclerEpisode): Category {
  const fromBackend = episode.category
  if (fromBackend && fromBackend in LANE_TAXONOMY) {
    return fromBackend as Category
  }
  return categoryForSource(episode.source_name, episode.episode_type)
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
 * sortOrder. Every LANE_TAXONOMY entry is included — lanes with no episodes
 * show a muted empty-lane affordance (bu-p4vd3).
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

  // All categories in sortOrder — always include every LANE_TAXONOMY entry.
  const lanes: LaneLayout[] = []
  let yOffset = 0

  for (const category of ALL_CATEGORIES) {
    const catEpisodes = grouped.get(category) ?? []
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

function laneColour(category: Category): string {
  return LANE_TAXONOMY[category].hex
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
  onEpisodeClick?: (episodeId: string) => void
  /** Category of the lane this bar belongs to — used to gate location panning. */
  category: Category
}

function EpisodeBar({ positioned, laneY, svgWidth, colour, patternId, windowEndMs, onEpisodeClick, category }: EpisodeBarProps) {
  const { episode, row, xPct, widthPct, isOpen } = positioned
  const isSensitive = episode.canonical_privacy === "sensitive"

  // Owner timezone from context (default: Asia/Singapore).
  const tz = useChroniclesTimezone()

  // ---------------------------------------------------------------------------
  // Location pan (calendar episodes only) — bu-ig72b.24
  // ---------------------------------------------------------------------------
  const panTo = useMapPanTo()

  // Extract location from payload only for calendar (intent) episodes. Keyed on
  // source_name, not lane: under the IEA reframe calendar episodes have no
  // Activity lane (they fold into "other"), so detect them by their source.
  // payload is JSONB — location may be absent, null, or any type.
  const isCalendarEpisode = episode.source_name.startsWith("google_calendar")
  const rawLocation =
    isCalendarEpisode && typeof episode.payload?.location === "string"
      ? (episode.payload.location as string)
      : null

  const parsedCoords = rawLocation !== null ? parseLatLng(rawLocation) : null

  // locationStatus is used to annotate the tooltip:
  //   "pannable"     — location string parses as lat,lng; click pans the map
  //   "unparseable"  — location string present but not parseable lat,lng
  //   null           — no location field or not a calendar episode
  const locationStatus: "pannable" | "unparseable" | null =
    rawLocation === null ? null : parsedCoords !== null ? "pannable" : "unparseable"

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
  const startLabel = isNaN(startMs) ? "?" : formatTimeInTz(startMs, tz)
  const endLabel = isOpen
    ? "ongoing"
    : isNaN(rawEndMs)
    ? "?"
    : formatTimeInTz(rawEndMs, tz)

  function handleClick() {
    // Open drilldown drawer for all episode types (bu-ig72b.31).
    onEpisodeClick?.(episode.id)
    // Pan map to location for calendar episodes with parseable coordinates (bu-ig72b.24).
    if (!isSensitive && parsedCoords !== null) {
      panTo(parsedCoords.lat, parsedCoords.lng)
    }
  }

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <g
          role="button"
          aria-label={isSensitive ? "Private activity" : (episode.canonical_title ?? episode.source_name)}
          data-testid={`gantt-bar-${episode.id}`}
          className="cursor-pointer"
          onClick={handleClick}
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
            <p className="font-medium" data-testid="gantt-tooltip-sensitive-label">
              {LANE_TAXONOMY[category].label}: {durationLabel}
            </p>
            <p className="opacity-70">
              {startLabel} – {endLabel}
            </p>
            <p className="text-yellow-600 mt-0.5">Sensitive</p>
          </>
        ) : (
          <>
            <p className="font-medium">{episode.canonical_title ?? episode.source_name}</p>
            <p>
              <span className="opacity-70">Source: </span>{episode.source_name}
            </p>
            <p>
              <span className="opacity-70">Precision: </span>{episode.precision}
            </p>
            <p>
              <span className="opacity-70">Duration: </span>{durationLabel}
            </p>
            <p className="opacity-70">
              {startLabel} – {endLabel}
            </p>
            {isOpen && <p className="opacity-70 italic">Episode ongoing</p>}
            {locationStatus === "pannable" && (
              <p className="text-emerald-600 mt-0.5" data-testid="gantt-location-pannable">
                Click to pan map to location
              </p>
            )}
            {locationStatus === "unparseable" && (
              <p className="text-muted-foreground mt-0.5 italic" data-testid="gantt-location-unparseable">
                Location: {rawLocation} (no coordinates)
              </p>
            )}
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

// ---------------------------------------------------------------------------
// Main inner component
// ---------------------------------------------------------------------------

const AXIS_HEIGHT = 24    // px for the time axis at the bottom
const SVG_WIDTH = 800     // logical pixel width (SVG viewBox width)

export function GanttSwimlaneInner({
  episodes,
  windowStart,
  windowEnd,
  onEpisodeClick,
  cursorMs,
}: GanttSwimlaneInnerProps) {
  const windowStartMs = windowStart.getTime()
  const windowEndMs = windowEnd.getTime()
  const windowDuration = windowEndMs - windowStartMs

  // Owner timezone from context (default: Asia/Singapore).
  const tz = useChroniclesTimezone()

  // Categories that have at least one episode in the current window. Derived
  // from the raw `episodes` prop (NOT from `lanes`) so the chip row stays
  // stable while a user toggles filters on/off.
  const availableCategories = useMemo<Category[]>(() => {
    const seen = new Set<Category>()
    for (const ep of episodes) {
      seen.add(categoryFor(ep))
    }
    return [...seen].sort(
      (a, b) => LANE_TAXONOMY[a].sortOrder - LANE_TAXONOMY[b].sortOrder,
    )
  }, [episodes])

  // Visible-category filter (bug 4). Defaults to "all visible". Stored as a
  // Set so per-chip toggle is O(1). Component-local — not URL-persisted.
  const [hiddenCategories, setHiddenCategories] = useState<Set<Category>>(
    () => new Set(),
  )

  function toggleCategory(category: Category) {
    setHiddenCategories((prev) => {
      const next = new Set(prev)
      if (next.has(category)) {
        next.delete(category)
      } else {
        next.add(category)
      }
      return next
    })
  }

  const visibleEpisodes = useMemo(
    () => episodes.filter((ep) => !hiddenCategories.has(categoryFor(ep))),
    [episodes, hiddenCategories],
  )

  const lanes = useMemo(
    () => buildLanes(visibleEpisodes, windowStartMs, windowEndMs),
    [visibleEpisodes, windowStartMs, windowEndMs],
  )

  const ticks = useMemo(
    () => buildTicks(windowStartMs, windowEndMs),
    [windowStartMs, windowEndMs],
  )

  // Filter chip row — shown above the chart whenever there are episodes.
  // Rendered even in the empty/all-hidden state so the user can re-enable
  // categories they have just hidden.
  const filterChipRow = availableCategories.length > 0 ? (
    <div
      className="flex flex-wrap items-center gap-1.5 mb-3"
      data-testid="gantt-filter-chips"
      role="group"
      aria-label="Filter Gantt categories"
    >
      {availableCategories.map((category) => {
        const visible = !hiddenCategories.has(category)
        const colour = laneColour(category)
        const label = LANE_TAXONOMY[category].label
        return (
          <Badge
            key={category}
            asChild
            variant={visible ? "default" : "outline"}
            data-testid={`gantt-filter-chip-${category}`}
          >
            <button
              type="button"
              aria-pressed={visible}
              onClick={() => toggleCategory(category)}
              className="cursor-pointer"
              style={
                visible
                  ? { backgroundColor: colour, color: "white", borderColor: colour }
                  : { color: colour, borderColor: colour }
              }
            >
              <span
                aria-hidden
                className="inline-block w-2 h-2 rounded-full"
                style={{ backgroundColor: colour }}
              />
              {label}
            </button>
          </Badge>
        )
      })}
    </div>
  ) : null

  // hasAnyEpisodes tracks whether there is something to render (for the
  // per-lane empty affordance logic). The all-lanes-empty case shows a
  // compact top-level notice beneath the swimlane grid.
  const hasAnyEpisodes = visibleEpisodes.length > 0

  // When no episodes exist at all (no filters active), show the simple empty state.
  if (!hasAnyEpisodes && hiddenCategories.size === 0 && episodes.length === 0) {
    return (
      <div className="relative" data-testid="gantt-container">
        {filterChipRow}
        <div
          className="flex items-center justify-center rounded-md border border-dashed py-12 text-muted-foreground text-sm"
          data-testid="gantt-empty"
        >
          No activity recorded for this window.
        </div>
      </div>
    )
  }

  const totalSvgHeight = lanes.reduce((sum, l) => sum + l.laneHeight, 0) + AXIS_HEIGHT

  return (
    <TooltipProvider>
      <div className="relative" data-testid="gantt-container">
        {filterChipRow}
        {/* Label column + SVG area side-by-side */}
        <div className="flex">
          {/* Label column */}
          <div
            className="shrink-0 select-none"
            style={{ width: LABEL_WIDTH, paddingTop: 0 }}
            aria-hidden
          >
            {lanes.map((lane) => {
              const isEmpty = lane.episodes.length === 0
              return (
                <div
                  key={lane.category}
                  className={[
                    "flex items-center text-xs font-medium pr-2",
                    isEmpty ? "text-muted-foreground/40" : "text-muted-foreground",
                  ].join(" ")}
                  style={{ height: lane.laneHeight }}
                >
                  <span
                    className="inline-block w-2.5 h-2.5 rounded-sm mr-1.5 shrink-0"
                    style={{
                      backgroundColor: laneColour(lane.category),
                      opacity: isEmpty ? 0.35 : 1,
                    }}
                  />
                  {LANE_TAXONOMY[lane.category].label}
                </div>
              )
            })}
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

              {/* Empty-lane placeholder rectangles — the muted dotted outline
                  for lanes with zero episodes in the current window (bu-p4vd3).
                  The "No data this period" TEXT is rendered as an HTML overlay
                  outside the SVG so it is not stretched horizontally by the
                  preserveAspectRatio="none" scaling (matches axis-label fix). */}
              {lanes.map((lane) => {
                if (lane.episodes.length > 0) return null
                const y = lane.yOffset + LANE_PADDING_TOP
                const h = LANE_HEIGHT
                return (
                  <rect
                    key={`empty-${lane.category}`}
                    data-testid={`gantt-empty-lane-${lane.category}`}
                    aria-label={`${LANE_TAXONOMY[lane.category].label}: no data this period`}
                    x={4}
                    y={y}
                    width={SVG_WIDTH - 8}
                    height={h}
                    rx={BAR_RADIUS}
                    ry={BAR_RADIUS}
                    fill="none"
                    stroke="currentColor"
                    strokeOpacity={0.15}
                    strokeWidth={1}
                    strokeDasharray="4 3"
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
                    onEpisodeClick={onEpisodeClick}
                    category={lane.category}
                  />
                ))
              })}

              {/* Scrubber cursor line (D12) */}
              {cursorMs != null && cursorMs >= windowStartMs && cursorMs <= windowEndMs && (
                <line
                  x1={(cursorMs - windowStartMs) / windowDuration * SVG_WIDTH}
                  y1={0}
                  x2={(cursorMs - windowStartMs) / windowDuration * SVG_WIDTH}
                  y2={totalSvgHeight - AXIS_HEIGHT}
                  stroke="currentColor"
                  strokeOpacity={0.7}
                  strokeWidth={1.5}
                  strokeDasharray="4 2"
                  data-testid="gantt-cursor"
                />
              )}

              {/* Time axis tick marks (strokes only).
                  Tick LABELS are rendered as absolutely-positioned HTML divs
                  below this SVG so they are not stretched by
                  preserveAspectRatio="none" (bug 3 fix). */}
              {ticks.map((ms) => {
                const xPct = (ms - windowStartMs) / windowDuration
                const x = xPct * SVG_WIDTH
                return (
                  <line
                    key={ms}
                    x1={x}
                    y1={totalSvgHeight - AXIS_HEIGHT}
                    x2={x}
                    y2={totalSvgHeight - AXIS_HEIGHT + 4}
                    stroke="currentColor"
                    strokeOpacity={0.4}
                    strokeWidth={1}
                  />
                )
              })}
            </svg>

            {/* Empty-lane "No data this period" labels — rendered as HTML so
                the text is NOT distorted by the SVG's preserveAspectRatio="none"
                stretch. Each lane label is centred over its dotted placeholder
                rect using the lane's yOffset. */}
            <div
              className="pointer-events-none absolute inset-0"
              data-testid="gantt-no-data-labels"
              aria-hidden
            >
              {lanes.map((lane) => {
                if (lane.episodes.length > 0) return null
                const top = lane.yOffset + LANE_PADDING_TOP + LANE_HEIGHT / 2
                return (
                  <span
                    key={`no-data-label-${lane.category}`}
                    className="absolute left-0 right-0 text-center text-[10px] text-muted-foreground/40 whitespace-nowrap"
                    style={{ top, transform: "translateY(-50%)" }}
                  >
                    No data this period
                  </span>
                )
              })}
            </div>

            {/* Time-axis labels — rendered as HTML so the text is NOT distorted
                by the SVG's preserveAspectRatio="none" stretch (bug 3 fix).
                Positioned absolutely over the bottom axis strip. The SVG
                already reserves AXIS_HEIGHT px of empty space at the bottom
                (no <text> elements there), so this overlay sits in that gap
                without overlapping bars. */}
            <div
              className="pointer-events-none absolute left-0 right-0"
              style={{ bottom: 0, height: AXIS_HEIGHT }}
              data-testid="gantt-axis-labels"
              aria-hidden
            >
              {ticks.map((ms, i) => {
                const pct = ((ms - windowStartMs) / windowDuration) * 100
                // Anchor first/last tick to the edges so labels don't clip.
                const transform =
                  i === 0
                    ? "translateX(0)"
                    : i === ticks.length - 1
                    ? "translateX(-100%)"
                    : "translateX(-50%)"
                return (
                  <span
                    key={ms}
                    className="absolute text-[10px] text-muted-foreground/70 whitespace-nowrap"
                    style={{
                      left: `${pct}%`,
                      top: 6,
                      transform,
                    }}
                  >
                    {formatGanttTickLabel(ms, windowDuration, tz)}
                  </span>
                )
              })}
            </div>
          </div>
        </div>
      </div>
    </TooltipProvider>
  )
}
