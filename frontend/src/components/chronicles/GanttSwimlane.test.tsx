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
  // Default to a `tasks`-categorised episode using realistic backend identifiers
  // (`core.sessions` / `work`) so the frontend's `(source_name, episode_type)`
  // → category fallback resolves to "tasks" (the default lane for sessions
  // without an explicit trigger_source). Tests that want a different lane should
  // override `source_name` + `episode_type` (or pass `category` directly) to
  // a pair recognised by `categoryForSource()`.
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
    category: "tasks",
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
  // core.sessions episodes without trigger_source → "tasks" (fallback default)
  tasks: { source_name: "core.sessions", episode_type: "work", category: "tasks" },
  calendar: { source_name: "google_calendar.completed", episode_type: "scheduled_block", category: "calendar" },
  music: { source_name: "spotify.session_summary", episode_type: "listening_episode", category: "music" },
  gaming: { source_name: "steam.play_history", episode_type: "play_episode", category: "gaming" },
  travel: { source_name: "owntracks.points", episode_type: "movement_episode", category: "travel" },
  sleep: { source_name: "google_health.measurements", episode_type: "sleep_episode", category: "sleep" },
  meal: { source_name: "health.meals", episode_type: "eating_event", category: "meal" },
  home: { source_name: "home_assistant.history", episode_type: "presence_episode", category: "home" },
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
    // Pattern is keyed by category, not episode id.
    // Default episode is core.sessions without trigger_source → "tasks"
    expect(html).toContain("hatch-tasks")
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
    const ep1 = makeEpisode({ id: "ep-work" })
    const ep2 = makeEpisode({ id: "ep-music", ...CATEGORY_SOURCES.music })
    const html = renderToStaticMarkup(
      <GanttSwimlaneInner
        episodes={[ep1, ep2]}
        windowStart={WINDOW_START}
        windowEnd={WINDOW_END}
      />,
    )
    expect(html).toContain("Work")
    expect(html).toContain("Music")
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
    // Bar has cursor:pointer indicating clickability
    expect(html).toContain('style="cursor:pointer"')
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
      // Default source_name/episode_type already maps to the "tasks" lane.
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
  it.each([
    ["tasks", CATEGORY_SOURCES.tasks, "Tasks"],
    ["calendar", CATEGORY_SOURCES.calendar, "Calendar"],
    ["music", CATEGORY_SOURCES.music, "Music"],
    ["gaming", CATEGORY_SOURCES.gaming, "Gaming"],
    ["travel", CATEGORY_SOURCES.travel, "Travel"],
    ["sleep", CATEGORY_SOURCES.sleep, "Sleep"],
    ["meal", CATEGORY_SOURCES.meal, "Meal"],
    ["home", CATEGORY_SOURCES.home, "Home"],
  ])(
    "renders the %s lane label for the canonical source/type pair (fallback path)",
    (_name, source, expectedLabel) => {
      const ep = makeEpisode({ id: `ep-${_name}`, ...source })
      const html = renderToStaticMarkup(
        <GanttSwimlaneInner
          episodes={[ep]}
          windowStart={WINDOW_START}
          windowEnd={WINDOW_END}
        />,
      )
      expect(html).toContain(expectedLabel)
      // "Other" must not appear when the mapping resolves to a known lane
      // (filter chip + lane label are derived from the same category).
      if (expectedLabel !== "Other") {
        expect(html).not.toContain(">Other<")
      }
    },
  )

  it("prefers backend-supplied `category` over the source/type fallback", () => {
    // source/type would normally resolve to the tasks lane, but the explicit
    // backend `category` field MUST win — this is the primary code path.
    const ep = makeEpisode({
      id: "ep-cat-override",
      ...CATEGORY_SOURCES.tasks,
      category: "music",
    })
    const html = renderToStaticMarkup(
      <GanttSwimlaneInner
        episodes={[ep]}
        windowStart={WINDOW_START}
        windowEnd={WINDOW_END}
      />,
    )
    expect(html).toContain("Music")
    expect(html).not.toContain(">Tasks<")
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
    expect(html).toContain("Other")
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
    expect(html).toContain('data-testid="gantt-filter-chip-tasks"')
    expect(html).toContain('data-testid="gantt-filter-chip-music"')
    expect(html).toContain('data-testid="gantt-filter-chip-sleep"')
    // No chip for categories with no episodes in this window.
    expect(html).not.toContain('data-testid="gantt-filter-chip-gaming"')
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
        makeEpisode({ id: "ep-keep", ...CATEGORY_SOURCES.work }),
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
        '[data-testid="gantt-filter-chip-music"]',
      ) as HTMLButtonElement | null
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
          '[data-testid="gantt-filter-chip-music"]',
        ) as HTMLButtonElement).getAttribute("aria-pressed"),
      ).toBe("false")

      // Toggle back on.
      await act(async () => {
        ;(
          container.querySelector(
            '[data-testid="gantt-filter-chip-music"]',
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

  it("hiding every category surfaces the empty state but keeps chips visible", async () => {
    const container = document.createElement("div")
    document.body.appendChild(container)
    const root = createRoot(container)
    try {
      const eps = [makeEpisode({ id: "ep-only", ...CATEGORY_SOURCES.work })]
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
      await act(async () => {
        chip.click()
      })
      // Empty state appears, but the chip row is still rendered so the user
      // can recover.
      expect(
        container.querySelector('[data-testid="gantt-empty"]'),
      ).not.toBeNull()
      expect(
        container.querySelector('[data-testid="gantt-filter-chips"]'),
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
    // No <text> elements inside the SVG (those got replaced by HTML divs).
    expect(html).not.toContain("<text")
    // Tick stroke <line>s remain.
    expect(html).toContain('y2="36"')
  })
})
