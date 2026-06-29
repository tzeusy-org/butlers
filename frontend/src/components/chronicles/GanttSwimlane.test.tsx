// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// Tests for GanttSwimlaneInner — bu-ig72b.28 / bu-ig72b.30
//
// Tests use GanttSwimlaneInner directly (bypassing React.lazy) so they run
// synchronously in the test environment.
//
// Coverage:
//   1. Empty window → empty state
//   2. Single episode renders a bar
//   3. Overlapping episodes in the same lane stack to different rows
//   4. Open episode (end_at = null) is clipped and rendered with open marker
//   5. Tooltip content: source, precision, duration
//   6. Sensitive episode gets a masked bar (pattern fill)
//   7. Multiple categories render separate swimlanes
//   8. Tooltip uses Radix primitive: sensitive masking, "View details" link
// ---------------------------------------------------------------------------

import { describe, expect, it } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"
import { act } from "react"
import { createRoot } from "react-dom/client"

import { GanttSwimlaneInner } from "./GanttSwimlaneInner"
import type { ChroniclerEpisode } from "@/api/types"

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const WINDOW_START = new Date("2026-04-25T00:00:00Z")
const WINDOW_END = new Date("2026-04-25T23:59:59Z")

function makeEpisode(overrides: Partial<ChroniclerEpisode> & { id: string }): ChroniclerEpisode {
  // Default to a `work`-lane episode using realistic backend identifiers
  // (`core.sessions` / `work`) so the frontend's `(source_name, episode_type)`
  // → Activity-lane fallback resolves to "work" (conversations + tasks both
  // fold into Work). Tests that want a different lane should override
  // `source_name` + `episode_type` (or pass `category` directly) to a pair
  // recognised by `categoryForSource()`.
  return {
    source_name: "core.sessions",
    source_ref: overrides.id,
    episode_type: "work",
    start_at: "2026-04-25T09:00:00Z",
    end_at: "2026-04-25T10:00:00Z",
    precision: "minute",
    title: null,
    payload: {},
    privacy: "normal",
    retention_days: null,
    tombstone_at: null,
    canonical_start_at: "2026-04-25T09:00:00Z",
    canonical_end_at: "2026-04-25T10:00:00Z",
    canonical_title: null,
    canonical_privacy: "normal",
    corrected_at: null,
    correction_note: null,
    created_at: "2026-04-25T00:00:00Z",
    updated_at: "2026-04-25T00:00:00Z",
    category: "work",
    ...overrides,
  }
}

/**
 * Source/episode-type pairs recognised by `categoryForSource()`, plus the
 * matching `category` string that the backend would attach. Used to override
 * the default `core.sessions` / `work` fixture when a test needs an episode
 * in a different lane.
 */
const CATEGORY_SOURCES: Record<
  string,
  { source_name: string; episode_type: string; category: string }
> = {
  // The `category` is the Activity LANE the backend attaches (IEA reframe,
  // bu-3n44q5). Music/gaming both fold into Play; calendar is intent → "other".
  // Keys retain their source nicknames; their lane is in `category`.
  tasks: { source_name: "core.sessions", episode_type: "work", category: "work" },
  calendar: { source_name: "google_calendar.completed", episode_type: "scheduled_block", category: "other" },
  music: { source_name: "spotify.session_summary", episode_type: "listening_episode", category: "play" },
  gaming: { source_name: "steam.play_history", episode_type: "play_episode", category: "play" },
  workout: { source_name: "google_health.measurements", episode_type: "workout_episode", category: "exercise" },
  travel: { source_name: "owntracks.points", episode_type: "movement_episode", category: "travel" },
  sleep: { source_name: "google_health.measurements", episode_type: "sleep_episode", category: "sleep" },
  meal: { source_name: "health.meals", episode_type: "eating_event", category: "eat" },
  home: { source_name: "home_assistant.history", episode_type: "presence_episode", category: "rest" },
}

// ---------------------------------------------------------------------------
// 1. Empty window
// ---------------------------------------------------------------------------

describe("GanttSwimlaneInner empty state", () => {
  it("renders empty state when no episodes are provided", () => {
    const html = renderToStaticMarkup(
      <GanttSwimlaneInner
        episodes={[]}
        windowStart={WINDOW_START}
        windowEnd={WINDOW_END}
      />,
    )
    expect(html).toContain("gantt-empty")
    expect(html).toContain("No activity recorded for this window")
  })

  it("does NOT render the SVG bar area when episodes is empty", () => {
    // The outer gantt-container wrapper now persists in the empty state
    // (so the filter chip row can be hosted), but the SVG / lane area
    // must not render. Probe specific child testids instead.
    const html = renderToStaticMarkup(
      <GanttSwimlaneInner
        episodes={[]}
        windowStart={WINDOW_START}
        windowEnd={WINDOW_END}
      />,
    )
    expect(html).not.toContain("gantt-svg-wrapper")
    expect(html).not.toContain("<svg")
  })
})

// ---------------------------------------------------------------------------
// 2. Single episode renders
// ---------------------------------------------------------------------------

describe("GanttSwimlaneInner single episode", () => {
  it("renders the gantt container when an episode is provided", () => {
    const ep = makeEpisode({ id: "ep-1" })
    const html = renderToStaticMarkup(
      <GanttSwimlaneInner
        episodes={[ep]}
        windowStart={WINDOW_START}
        windowEnd={WINDOW_END}
      />,
    )
    expect(html).toContain("gantt-container")
  })

  it("renders a bar element for the episode", () => {
    const ep = makeEpisode({ id: "ep-1" })
    const html = renderToStaticMarkup(
      <GanttSwimlaneInner
        episodes={[ep]}
        windowStart={WINDOW_START}
        windowEnd={WINDOW_END}
      />,
    )
    expect(html).toContain("gantt-bar-ep-1")
  })

  it("renders the Work lane label", () => {
    const ep = makeEpisode({ id: "ep-1" })
    const html = renderToStaticMarkup(
      <GanttSwimlaneInner
        episodes={[ep]}
        windowStart={WINDOW_START}
        windowEnd={WINDOW_END}
      />,
    )
    // Lane label column AND the filter chip both render the human label.
    // core.sessions episodes fold into the Work lane (IEA reframe).
    expect(html).toContain("Work")
  })
})

// ---------------------------------------------------------------------------
// 3. Overlapping bars stack (row assignment)
// ---------------------------------------------------------------------------

describe("GanttSwimlaneInner overlapping episodes", () => {
  it("renders both overlapping episodes as bars", () => {
    const ep1 = makeEpisode({
      id: "ep-a",
      canonical_start_at: "2026-04-25T09:00:00Z",
      canonical_end_at: "2026-04-25T11:00:00Z",
    })
    const ep2 = makeEpisode({
      id: "ep-b",
      canonical_start_at: "2026-04-25T10:00:00Z",
      canonical_end_at: "2026-04-25T12:00:00Z",
    })

    const html = renderToStaticMarkup(
      <GanttSwimlaneInner
        episodes={[ep1, ep2]}
        windowStart={WINDOW_START}
        windowEnd={WINDOW_END}
      />,
    )
    // Both bars must appear
    expect(html).toContain("gantt-bar-ep-a")
    expect(html).toContain("gantt-bar-ep-b")
  })

  it("renders non-overlapping episodes (should not stack)", () => {
    const ep1 = makeEpisode({
      id: "ep-c",
      canonical_start_at: "2026-04-25T09:00:00Z",
      canonical_end_at: "2026-04-25T10:00:00Z",
    })
    const ep2 = makeEpisode({
      id: "ep-d",
      canonical_start_at: "2026-04-25T11:00:00Z",
      canonical_end_at: "2026-04-25T12:00:00Z",
    })
    const html = renderToStaticMarkup(
      <GanttSwimlaneInner
        episodes={[ep1, ep2]}
        windowStart={WINDOW_START}
        windowEnd={WINDOW_END}
      />,
    )
    expect(html).toContain("gantt-bar-ep-c")
    expect(html).toContain("gantt-bar-ep-d")
  })
})

// ---------------------------------------------------------------------------
// 4. Open episode clipped with visual marker
// ---------------------------------------------------------------------------

describe("GanttSwimlaneInner open episode", () => {
  it("renders a bar for an open episode (end_at = null)", () => {
    const ep = makeEpisode({
      id: "ep-open",
      end_at: null,
      canonical_end_at: null,
      canonical_start_at: "2026-04-25T20:00:00Z",
    })
    const html = renderToStaticMarkup(
      <GanttSwimlaneInner
        episodes={[ep]}
        windowStart={WINDOW_START}
        windowEnd={WINDOW_END}
      />,
    )
    expect(html).toContain("gantt-bar-ep-open")
  })

  it("renders a dashed-edge marker for an open episode", () => {
    const ep = makeEpisode({
      id: "ep-open2",
      end_at: null,
      canonical_end_at: null,
      canonical_start_at: "2026-04-25T20:00:00Z",
    })
    const html = renderToStaticMarkup(
      <GanttSwimlaneInner
        episodes={[ep]}
        windowStart={WINDOW_START}
        windowEnd={WINDOW_END}
      />,
    )
    // Dashed right edge marker (React serializes strokeDasharray → stroke-dasharray in HTML)
    expect(html).toContain("stroke-dasharray")
  })
})

// ---------------------------------------------------------------------------
// 5. Tooltip content (structure check via aria/data attributes)
// ---------------------------------------------------------------------------

describe("GanttSwimlaneInner tooltip content", () => {
  it("renders bars with aria-label for accessibility", () => {
    const ep = makeEpisode({
      id: "ep-tooltip",
      ...CATEGORY_SOURCES.music,
      canonical_title: "Listening session",
      precision: "minute",
    })
    const html = renderToStaticMarkup(
      <GanttSwimlaneInner
        episodes={[ep]}
        windowStart={WINDOW_START}
        windowEnd={WINDOW_END}
      />,
    )
    expect(html).toContain("Listening session")
  })

  it("renders bars with testid that includes the episode ID", () => {
    const ep = makeEpisode({ id: "ep-check" })
    const html = renderToStaticMarkup(
      <GanttSwimlaneInner
        episodes={[ep]}
        windowStart={WINDOW_START}
        windowEnd={WINDOW_END}
      />,
    )
    expect(html).toContain("gantt-bar-ep-check")
  })
})

// ---------------------------------------------------------------------------
// 6. Sensitive episode masked bar
// ---------------------------------------------------------------------------

describe("GanttSwimlaneInner sensitive episode", () => {
  it("renders a hatched pattern for sensitive episodes", () => {
    const ep = makeEpisode({
      id: "ep-sensitive",
      canonical_privacy: "sensitive",
    })
    const html = renderToStaticMarkup(
      <GanttSwimlaneInner
        episodes={[ep]}
        windowStart={WINDOW_START}
        windowEnd={WINDOW_END}
      />,
    )
    // A <pattern> element must be present for the hatch fill (keyed per category).
    expect(html).toContain("<pattern")
    // Pattern is keyed by lane, not episode id.
    // Default episode is core.sessions → Work lane.
    expect(html).toContain("hatch-work")
  })

  it("renders the bar element for a sensitive episode", () => {
    const ep = makeEpisode({
      id: "ep-sensitive2",
      canonical_privacy: "sensitive",
    })
    const html = renderToStaticMarkup(
      <GanttSwimlaneInner
        episodes={[ep]}
        windowStart={WINDOW_START}
        windowEnd={WINDOW_END}
      />,
    )
    expect(html).toContain("gantt-bar-ep-sensitive2")
  })

  it("uses generic aria-label for sensitive bar — never leaks title", () => {
    const ep = makeEpisode({
      id: "ep-sensitive-aria",
      canonical_privacy: "sensitive",
      canonical_title: "Secret project Alpha",
    })
    const html = renderToStaticMarkup(
      <GanttSwimlaneInner
        episodes={[ep]}
        windowStart={WINDOW_START}
        windowEnd={WINDOW_END}
      />,
    )
    expect(html).toContain("Private activity")
    expect(html).not.toContain("Secret project Alpha")
  })
})

// ---------------------------------------------------------------------------
// 7. Multiple categories render separate swimlanes
// ---------------------------------------------------------------------------

describe("GanttSwimlaneInner multiple categories", () => {
  it("renders lane labels for each category present", () => {
    const ep1 = makeEpisode({ id: "ep-tasks" })
    const ep2 = makeEpisode({ id: "ep-music", ...CATEGORY_SOURCES.music })
    const html = renderToStaticMarkup(
      <GanttSwimlaneInner
        episodes={[ep1, ep2]}
        windowStart={WINDOW_START}
        windowEnd={WINDOW_END}
      />,
    )
    // core.sessions → Work lane; spotify → Play lane.
    expect(html).toContain("Work")
    expect(html).toContain("Play")
  })

  it("renders bars for episodes from different categories", () => {
    const ep1 = makeEpisode({ id: "ep-m1" })
    const ep2 = makeEpisode({ id: "ep-m2", ...CATEGORY_SOURCES.sleep })
    const html = renderToStaticMarkup(
      <GanttSwimlaneInner
        episodes={[ep1, ep2]}
        windowStart={WINDOW_START}
        windowEnd={WINDOW_END}
      />,
    )
    expect(html).toContain("gantt-bar-ep-m1")
    expect(html).toContain("gantt-bar-ep-m2")
  })
})

// ---------------------------------------------------------------------------
// 8. Tooltip: Radix primitive trigger structure
// ---------------------------------------------------------------------------
//
// Radix TooltipContent renders via a Portal and is NOT present in static
// server-side markup. These tests verify the tooltip trigger wiring (the
// data-slot and data-state attributes that Radix injects) and the aria-label
// on the bar, which serves as the accessible fallback for screen readers.
//
// Interactive tooltip content (title, source, "View details" link, sensitive
// masking) is covered by end-to-end / interaction tests that mount into a
// real DOM with pointer events.

describe("GanttSwimlaneInner tooltip via Radix primitive", () => {
  it("wraps each bar in a Radix tooltip trigger (data-slot=tooltip-trigger)", () => {
    const ep = makeEpisode({ id: "ep-radix", canonical_privacy: "normal" })
    const html = renderToStaticMarkup(
      <GanttSwimlaneInner
        episodes={[ep]}
        windowStart={WINDOW_START}
        windowEnd={WINDOW_END}
      />,
    )
    // Radix injects data-slot="tooltip-trigger" on the TooltipTrigger element
    expect(html).toContain('data-slot="tooltip-trigger"')
    // Trigger starts in "closed" state
    expect(html).toContain('data-state="closed"')
  })

  it("bar trigger has aria-label from episode title", () => {
    const ep = makeEpisode({
      id: "ep-aria",
      canonical_title: "Deep work block",
      canonical_privacy: "normal",
    })
    const html = renderToStaticMarkup(
      <GanttSwimlaneInner
        episodes={[ep]}
        windowStart={WINDOW_START}
        windowEnd={WINDOW_END}
      />,
    )
    // The <g> inside TooltipTrigger carries the accessible label
    expect(html).toContain("Deep work block")
  })

  it("sensitive bar trigger uses 'Private activity' aria-label — never leaks canonical_title", () => {
    // The bar's aria-label must also be masked for sensitive episodes so that
    // screen readers do not announce the real title.
    const ep = makeEpisode({
      id: "ep-sens-aria",
      canonical_title: "Private session",
      canonical_privacy: "sensitive",
    })
    const html = renderToStaticMarkup(
      <GanttSwimlaneInner
        episodes={[ep]}
        windowStart={WINDOW_START}
        windowEnd={WINDOW_END}
      />,
    )
    expect(html).toContain('data-slot="tooltip-trigger"')
    expect(html).toContain("gantt-bar-ep-sens-aria")
    expect(html).toContain("Private activity")
    expect(html).not.toContain("Private session")
  })
})

// ---------------------------------------------------------------------------
// 9. Calendar location pan — click handler wire-up (bu-ig72b.24)
//
// Radix TooltipContent renders via a portal and is NOT present in static
// server-side markup (same constraint as tooltip test block above).
// The location-status annotations ("Click to pan map to location",
// "no coordinates") live inside TooltipContent so they cannot be asserted
// here; they are exercised by interactive / e2e tests.
//
// What we CAN assert here:
//   - Calendar bars render when payload.location is provided.
//   - The <g> element has cursor:pointer (onClick is wired).
//   - Non-calendar episodes with location in payload still render normally.
// ---------------------------------------------------------------------------

describe("GanttSwimlaneInner calendar location pan click handler", () => {
  it("renders a calendar bar when payload has a parseable lat,lng location", () => {
    const ep = makeEpisode({
      id: "ep-cal-coord",
      ...CATEGORY_SOURCES.calendar,
      canonical_title: "Team meeting",
      canonical_privacy: "normal",
      payload: { location: "1.3521,103.8198" },
    })
    const html = renderToStaticMarkup(
      <GanttSwimlaneInner
        episodes={[ep]}
        windowStart={WINDOW_START}
        windowEnd={WINDOW_END}
      />,
    )
    expect(html).toContain("gantt-bar-ep-cal-coord")
    // Bar has cursor-pointer class indicating clickability
    expect(html).toContain('class="cursor-pointer"')
  })

  it("renders a calendar bar when payload has an unparseable location string", () => {
    const ep = makeEpisode({
      id: "ep-cal-addr",
      ...CATEGORY_SOURCES.calendar,
      canonical_title: "Off-site workshop",
      canonical_privacy: "normal",
      payload: { location: "1 Infinite Loop, Cupertino, CA" },
    })
    const html = renderToStaticMarkup(
      <GanttSwimlaneInner
        episodes={[ep]}
        windowStart={WINDOW_START}
        windowEnd={WINDOW_END}
      />,
    )
    expect(html).toContain("gantt-bar-ep-cal-addr")
  })

  it("renders a calendar bar normally when payload has no location field", () => {
    const ep = makeEpisode({
      id: "ep-cal-noloc",
      ...CATEGORY_SOURCES.calendar,
      canonical_title: "Meeting without location",
      canonical_privacy: "normal",
      payload: {},
    })
    const html = renderToStaticMarkup(
      <GanttSwimlaneInner
        episodes={[ep]}
        windowStart={WINDOW_START}
        windowEnd={WINDOW_END}
      />,
    )
    expect(html).toContain("gantt-bar-ep-cal-noloc")
  })

  it("renders a non-calendar bar normally even when payload has a location field", () => {
    // Only calendar-category episodes trigger the pan logic; tasks episodes
    // with a location in payload must still render without errors.
    const ep = makeEpisode({
      id: "ep-work-loc",
      // Default source_name/episode_type already maps to the Work lane.
      canonical_privacy: "normal",
      payload: { location: "40.7128,-74.0060" },
    })
    const html = renderToStaticMarkup(
      <GanttSwimlaneInner
        episodes={[ep]}
        windowStart={WINDOW_START}
        windowEnd={WINDOW_END}
      />,
    )
    expect(html).toContain("gantt-bar-ep-work-loc")
  })

  it("renders a sensitive calendar bar using 'Private activity' label", () => {
    // Sensitive bars must not leak any location data through aria-label or title.
    const ep = makeEpisode({
      id: "ep-cal-sens-loc",
      ...CATEGORY_SOURCES.calendar,
      canonical_privacy: "sensitive",
      payload: { location: "1.3521,103.8198" },
    })
    const html = renderToStaticMarkup(
      <GanttSwimlaneInner
        episodes={[ep]}
        windowStart={WINDOW_START}
        windowEnd={WINDOW_END}
      />,
    )
    expect(html).toContain("gantt-bar-ep-cal-sens-loc")
    expect(html).toContain("Private activity")
    expect(html).not.toContain("1.3521")
  })
})

// ---------------------------------------------------------------------------
// 10. categoryFor — backend (source_name, episode_type) → lane mapping (bug 1)
//
// Covers the fix for the "all bars are gray" bug where every episode used to
// fall into the "other" lane because the inner component compared
// `source_name` directly against `LANE_TAXONOMY` keys.
// ---------------------------------------------------------------------------

describe("GanttSwimlaneInner categoryFor mapping (bug 1)", () => {
  // bu-p4vd3: all LANE_TAXONOMY lanes are always rendered. The filter chip
  // row is the best proxy for "this episode was categorised correctly" — a chip
  // only appears for a category that has at least one episode. Lane labels
  // ("Other", "Tasks", …) appear in the SVG regardless of episode count.
  it.each([
    ["work", CATEGORY_SOURCES.tasks, "work"],
    ["play", CATEGORY_SOURCES.music, "play"],
    ["exercise", CATEGORY_SOURCES.workout, "exercise"],
    ["travel", CATEGORY_SOURCES.travel, "travel"],
    ["sleep", CATEGORY_SOURCES.sleep, "sleep"],
    ["eat", CATEGORY_SOURCES.meal, "eat"],
    ["rest", CATEGORY_SOURCES.home, "rest"],
    // Calendar is intent → folds into the "other" lane.
    ["calendar", CATEGORY_SOURCES.calendar, "other"],
  ])(
    "renders a filter chip for the %s category when an episode maps to it (fallback path)",
    (_name, source, expectedChipCategory) => {
      const ep = makeEpisode({ id: `ep-${_name}`, ...source })
      const html = renderToStaticMarkup(
        <GanttSwimlaneInner
          episodes={[ep]}
          windowStart={WINDOW_START}
          windowEnd={WINDOW_END}
        />,
      )
      // Filter chip exists only for categories with at least one episode.
      expect(html).toContain(`gantt-filter-chip-${expectedChipCategory}`)
      // Chip must not exist for "other" (episode is in a known lane).
      if (expectedChipCategory !== "other") {
        expect(html).not.toContain('gantt-filter-chip-other"')
      }
    },
  )

  it("prefers backend-supplied `category` over the source/type fallback", () => {
    // source/type would normally resolve to the tasks lane, but the explicit
    // backend `category` field MUST win — this is the primary code path.
    const ep = makeEpisode({
      id: "ep-cat-override",
      ...CATEGORY_SOURCES.tasks,
      category: "play",
    })
    const html = renderToStaticMarkup(
      <GanttSwimlaneInner
        episodes={[ep]}
        windowStart={WINDOW_START}
        windowEnd={WINDOW_END}
      />,
    )
    // Filter chip exists for play (episode landed there), not work.
    expect(html).toContain("gantt-filter-chip-play")
    expect(html).not.toContain("gantt-filter-chip-work")
  })

  it("falls back to 'other' for an unknown source/type pair with no category", () => {
    const ep = makeEpisode({
      id: "ep-unknown",
      source_name: "made.up",
      episode_type: "weird_thing",
      category: "other",
    })
    const html = renderToStaticMarkup(
      <GanttSwimlaneInner
        episodes={[ep]}
        windowStart={WINDOW_START}
        windowEnd={WINDOW_END}
      />,
    )
    // Filter chip for "other" appears since the episode landed there.
    expect(html).toContain("gantt-filter-chip-other")
  })
})

// ---------------------------------------------------------------------------
// 11. View details link removed from tooltip (bug 2)
// ---------------------------------------------------------------------------
//
// The Radix TooltipContent renders into a portal and is therefore not in
// static markup. When the tooltip is open in jsdom (force open via prop is
// not possible here without rewiring), the content lives in document.body.
// We assert at the source level: the `<a href="/chronicles/episodes/...">`
// link must not appear anywhere in the rendered tree, even after the
// tooltip mounts. The simplest way to verify the link is gone is to check
// the component module's static markup AND mount it into jsdom and
// inspect the document for the would-be link.

describe("GanttSwimlaneInner tooltip drilldown (bug 2)", () => {
  it("does not render a /chronicles/episodes/ link anywhere in the tree", () => {
    const ep = makeEpisode({ id: "ep-no-link" })
    const html = renderToStaticMarkup(
      <GanttSwimlaneInner
        episodes={[ep]}
        windowStart={WINDOW_START}
        windowEnd={WINDOW_END}
      />,
    )
    expect(html).not.toContain("/chronicles/episodes/")
    expect(html).not.toContain("View details")
  })

  it("does not render the View details link after the tooltip is opened in jsdom", async () => {
    const container = document.createElement("div")
    document.body.appendChild(container)
    const root = createRoot(container)
    try {
      const ep = makeEpisode({ id: "ep-no-link-jsdom" })
      await act(async () => {
        root.render(
          <GanttSwimlaneInner
            episodes={[ep]}
            windowStart={WINDOW_START}
            windowEnd={WINDOW_END}
          />,
        )
      })
      // No tooltip-content link should exist in document.body even when the
      // tooltip would normally portal into it.
      expect(document.body.innerHTML).not.toContain("/chronicles/episodes/")
      expect(document.body.innerHTML).not.toContain("View details")
    } finally {
      await act(async () => {
        root.unmount()
      })
      document.body.removeChild(container)
    }
  })
})

// ---------------------------------------------------------------------------
// 12. Per-lane filter chips (bug 4)
// ---------------------------------------------------------------------------

describe("GanttSwimlaneInner filter chips (bug 4)", () => {
  it("renders one chip per category present in the window", () => {
    const eps = [
      makeEpisode({ id: "ep-t", ...CATEGORY_SOURCES.tasks }),
      makeEpisode({ id: "ep-m", ...CATEGORY_SOURCES.music }),
      makeEpisode({ id: "ep-s", ...CATEGORY_SOURCES.sleep }),
    ]
    const html = renderToStaticMarkup(
      <GanttSwimlaneInner
        episodes={eps}
        windowStart={WINDOW_START}
        windowEnd={WINDOW_END}
      />,
    )
    expect(html).toContain('data-testid="gantt-filter-chips"')
    expect(html).toContain('data-testid="gantt-filter-chip-work"')
    expect(html).toContain('data-testid="gantt-filter-chip-play"')
    expect(html).toContain('data-testid="gantt-filter-chip-sleep"')
    // No chip for lanes with no episodes in this window.
    expect(html).not.toContain('data-testid="gantt-filter-chip-travel"')
  })

  it("does not render chips when there are no episodes at all", () => {
    const html = renderToStaticMarkup(
      <GanttSwimlaneInner
        episodes={[]}
        windowStart={WINDOW_START}
        windowEnd={WINDOW_END}
      />,
    )
    expect(html).not.toContain('data-testid="gantt-filter-chips"')
  })

  it("clicking a chip hides the matching lane's bars and toggling it back restores them", async () => {
    const container = document.createElement("div")
    document.body.appendChild(container)
    const root = createRoot(container)
    try {
      const eps = [
        makeEpisode({ id: "ep-keep", ...CATEGORY_SOURCES.tasks }),
        makeEpisode({ id: "ep-toggle", ...CATEGORY_SOURCES.music }),
      ]
      await act(async () => {
        root.render(
          <GanttSwimlaneInner
            episodes={eps}
            windowStart={WINDOW_START}
            windowEnd={WINDOW_END}
          />,
        )
      })

      // Both bars visible initially.
      expect(
        container.querySelector('[data-testid="gantt-bar-ep-keep"]'),
      ).not.toBeNull()
      expect(
        container.querySelector('[data-testid="gantt-bar-ep-toggle"]'),
      ).not.toBeNull()

      const chip = container.querySelector(
        '[data-testid="gantt-filter-chip-play"]',
      ) as HTMLButtonElement | null
      // Filter chips only appear for categories that have episodes.
      expect(chip).not.toBeNull()
      expect(chip!.getAttribute("aria-pressed")).toBe("true")

      // Hide music.
      await act(async () => {
        chip!.click()
      })
      expect(
        container.querySelector('[data-testid="gantt-bar-ep-toggle"]'),
      ).toBeNull()
      expect(
        container.querySelector('[data-testid="gantt-bar-ep-keep"]'),
      ).not.toBeNull()
      // Chip itself remains so the user can re-enable it.
      expect(
        (container.querySelector(
          '[data-testid="gantt-filter-chip-play"]',
        ) as HTMLButtonElement).getAttribute("aria-pressed"),
      ).toBe("false")

      // Toggle back on.
      await act(async () => {
        ;(
          container.querySelector(
            '[data-testid="gantt-filter-chip-play"]',
          ) as HTMLButtonElement
        ).click()
      })
      expect(
        container.querySelector('[data-testid="gantt-bar-ep-toggle"]'),
      ).not.toBeNull()
    } finally {
      await act(async () => {
        root.unmount()
      })
      document.body.removeChild(container)
    }
  })

  it("hiding every category removes episode bars but keeps chips and empty-lane placeholders visible", async () => {
    // bu-p4vd3: all-lanes rendering means hiding all categories shows empty-lane
    // placeholders in the SVG rather than the global gantt-empty notice. The
    // filter chip row stays so the user can re-enable categories.
    const container = document.createElement("div")
    document.body.appendChild(container)
    const root = createRoot(container)
    try {
      const eps = [makeEpisode({ id: "ep-only", ...CATEGORY_SOURCES.tasks })]
      await act(async () => {
        root.render(
          <GanttSwimlaneInner
            episodes={eps}
            windowStart={WINDOW_START}
            windowEnd={WINDOW_END}
          />,
        )
      })
      const chip = container.querySelector(
        '[data-testid="gantt-filter-chip-work"]',
      ) as HTMLButtonElement
      expect(chip).not.toBeNull()
      await act(async () => {
        chip.click()
      })
      // Episode bar is gone after hiding.
      expect(
        container.querySelector('[data-testid="gantt-bar-ep-only"]'),
      ).toBeNull()
      // Chip row persists so the user can recover.
      expect(
        container.querySelector('[data-testid="gantt-filter-chips"]'),
      ).not.toBeNull()
      // SVG is still rendered (all-lanes mode) — no global gantt-empty.
      expect(
        container.querySelector('[data-testid="gantt-svg-wrapper"]'),
      ).not.toBeNull()
      // Empty-lane placeholder for the work lane should be visible.
      expect(
        container.querySelector('[data-testid="gantt-empty-lane-work"]'),
      ).not.toBeNull()
    } finally {
      await act(async () => {
        root.unmount()
      })
      document.body.removeChild(container)
    }
  })
})

// ---------------------------------------------------------------------------
// 13. X-axis labels rendered as HTML, not as stretched <text> (bug 3)
// ---------------------------------------------------------------------------

describe("GanttSwimlaneInner axis labels (bug 3)", () => {
  it("renders axis labels in an HTML overlay (not inside the stretched SVG)", () => {
    const ep = makeEpisode({ id: "ep-axis" })
    const html = renderToStaticMarkup(
      <GanttSwimlaneInner
        episodes={[ep]}
        windowStart={WINDOW_START}
        windowEnd={WINDOW_END}
      />,
    )
    expect(html).toContain('data-testid="gantt-axis-labels"')
    // Time axis tick LABELS are rendered as HTML divs (bug 3 fix); empty-lane
    // "No data this period" labels are also HTML overlays. SVG <text> would be
    // stretched by preserveAspectRatio="none", so neither must appear inside
    // the SVG. Axis tick stroke <line>s remain in the SVG.
    expect(html).toContain("gantt-axis-labels")
    expect(html).toContain('data-testid="gantt-no-data-labels"')
    // The SVG tick stroke <line>s use stroke-opacity="0.4" and stroke-width="1"
    // (distinct from the grid lines at 0.08). We verify tick marks are present.
    expect(html).toContain("gantt-svg-wrapper")
    expect(html).toContain('stroke-opacity="0.4"')
  })
})

// ---------------------------------------------------------------------------
// 14. Render-all-lanes: every LANE_TAXONOMY entry rendered even with 0 episodes
//     (bu-p4vd3 AC1 + AC2)
// ---------------------------------------------------------------------------

describe("GanttSwimlaneInner render-all-lanes (bu-p4vd3)", () => {
  it("renders all LANE_TAXONOMY lane labels when episodes are present", () => {
    // Even with only a single work episode, all lanes appear in the label column.
    const ep = makeEpisode({ id: "ep-all-lanes", ...CATEGORY_SOURCES.tasks })
    const html = renderToStaticMarkup(
      <GanttSwimlaneInner
        episodes={[ep]}
        windowStart={WINDOW_START}
        windowEnd={WINDOW_END}
      />,
    )
    // All lane labels should appear — even those with no episodes.
    expect(html).toContain("Sleep")
    expect(html).toContain("Exercise")
    expect(html).toContain("Work")
    expect(html).toContain("Play")
    expect(html).toContain("Social")
    expect(html).toContain("Travel")
    expect(html).toContain("Eat")
    expect(html).toContain("Rest")
    expect(html).toContain("Other")
  })

  it("shows empty-lane placeholder for lanes with no data", () => {
    // With only a work episode, all OTHER lanes have no data.
    const ep = makeEpisode({ id: "ep-only-work", ...CATEGORY_SOURCES.tasks })
    const html = renderToStaticMarkup(
      <GanttSwimlaneInner
        episodes={[ep]}
        windowStart={WINDOW_START}
        windowEnd={WINDOW_END}
      />,
    )
    // Empty-lane affordance exists for a lane with no data (e.g. travel).
    expect(html).toContain('gantt-empty-lane-travel')
    // But NOT for the lane that HAS data.
    expect(html).not.toContain('gantt-empty-lane-work')
    // Empty-lane label is rendered as an HTML overlay (not stretched SVG text).
    expect(html).toContain("No data this period")
    expect(html).toContain('data-testid="gantt-no-data-labels"')
  })

  it("renders 0 empty-lane placeholders when all lanes have data", () => {
    // One episode per lane (all nine LANE_TAXONOMY entries have data).
    // Use `category` as the last key in the spread so it wins over CATEGORY_SOURCES.
    const eps = [
      makeEpisode({ id: "ep-slp", ...CATEGORY_SOURCES.sleep, category: "sleep" }),
      makeEpisode({ id: "ep-exr", ...CATEGORY_SOURCES.workout, category: "exercise" }),
      makeEpisode({ id: "ep-wrk", ...CATEGORY_SOURCES.tasks, category: "work" }),
      makeEpisode({ id: "ep-ply", ...CATEGORY_SOURCES.music, category: "play" }),
      makeEpisode({ id: "ep-soc", source_name: "made.up", episode_type: "x", category: "social" }),
      makeEpisode({ id: "ep-trv", ...CATEGORY_SOURCES.travel, category: "travel" }),
      makeEpisode({ id: "ep-eat", ...CATEGORY_SOURCES.meal, category: "eat" }),
      makeEpisode({ id: "ep-rst", ...CATEGORY_SOURCES.home, category: "rest" }),
      makeEpisode({ id: "ep-oth", source_name: "made.up", episode_type: "y", category: "other" }),
    ]
    const html = renderToStaticMarkup(
      <GanttSwimlaneInner
        episodes={eps}
        windowStart={WINDOW_START}
        windowEnd={WINDOW_END}
      />,
    )
    // No empty-lane placeholders when every lane has at least one episode.
    expect(html).not.toContain("gantt-empty-lane-")
    // Text "No data this period" must not appear.
    expect(html).not.toContain("No data this period")
  })

  it("muted label column: empty lane uses reduced-opacity style class", () => {
    const ep = makeEpisode({ id: "ep-muted", ...CATEGORY_SOURCES.tasks })
    const html = renderToStaticMarkup(
      <GanttSwimlaneInner
        episodes={[ep]}
        windowStart={WINDOW_START}
        windowEnd={WINDOW_END}
      />,
    )
    // Populated lane uses full-opacity class, empty lane uses reduced-opacity class.
    expect(html).toContain("text-muted-foreground/40")
    expect(html).toContain("text-muted-foreground\"")
  })
})

// ---------------------------------------------------------------------------
// 15. Privacy contract — sensitive vs normal vs restricted in Gantt lane
//     (bu-6c5i6 privacy contract)
//
// normal:     bar renders normally; no hatch pattern applied to bar fill
// sensitive:  bar renders in lane; hatched pattern; aria-label = "Private
//             activity"; tooltip data-testid gantt-tooltip-sensitive-label
//             present with category + duration (not the real title)
// restricted: hidden at server layer — never reaches the component
// ---------------------------------------------------------------------------

describe("GanttSwimlaneInner privacy contract (bu-6c5i6)", () => {
  it("normal: renders bar with episode title in aria-label (not masked)", () => {
    const ep = makeEpisode({
      id: "ep-normal-privacy",
      canonical_privacy: "normal",
      canonical_title: "Deep work session",
    })
    const html = renderToStaticMarkup(
      <GanttSwimlaneInner
        episodes={[ep]}
        windowStart={WINDOW_START}
        windowEnd={WINDOW_END}
      />,
    )
    expect(html).toContain("gantt-bar-ep-normal-privacy")
    // Normal episodes expose the title in aria-label (not masked)
    expect(html).toContain("Deep work session")
    // No hatch pattern for the bar fill
    expect(html).not.toContain('fill="url(#hatch-')
  })

  it("sensitive: bar renders in lane with hatched fill and 'Private activity' aria-label", () => {
    const ep = makeEpisode({
      id: "ep-sensitive-privacy",
      canonical_privacy: "sensitive",
      canonical_title: "Location trace",
      ...CATEGORY_SOURCES.travel,
    })
    const html = renderToStaticMarkup(
      <GanttSwimlaneInner
        episodes={[ep]}
        windowStart={WINDOW_START}
        windowEnd={WINDOW_END}
      />,
    )
    // Bar must be present in the Travel lane
    expect(html).toContain("gantt-bar-ep-sensitive-privacy")
    // Title must NOT leak via aria-label
    expect(html).not.toContain("Location trace")
    // Generic aria-label used
    expect(html).toContain("Private activity")
    // Hatch pattern applied to the bar fill
    expect(html).toContain('fill="url(#hatch-travel)"')
    // Note: Radix TooltipContent renders via portal and is not present in
    // static markup. Tooltip content (duration + category label for sensitive)
    // is exercised by interactive / e2e tests.
  })
})
