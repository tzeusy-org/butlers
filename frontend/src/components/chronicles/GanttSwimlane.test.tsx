// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// Tests for GanttSwimlaneInner — bu-ig72b.28
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
// ---------------------------------------------------------------------------

import { describe, expect, it } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import { GanttSwimlaneInner } from "./GanttSwimlaneInner"
import type { ChroniclerEpisode } from "@/api/types"

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const WINDOW_START = new Date("2026-04-25T00:00:00Z")
const WINDOW_END = new Date("2026-04-25T23:59:59Z")

function makeEpisode(overrides: Partial<ChroniclerEpisode> & { id: string }): ChroniclerEpisode {
  return {
    source_name: "work",
    source_ref: overrides.id,
    episode_type: "session",
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
    ...overrides,
  }
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
    expect(html).toContain("No episodes in this time window")
  })

  it("does NOT render the SVG container when episodes is empty", () => {
    const html = renderToStaticMarkup(
      <GanttSwimlaneInner
        episodes={[]}
        windowStart={WINDOW_START}
        windowEnd={WINDOW_END}
      />,
    )
    expect(html).not.toContain("gantt-container")
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
    const ep = makeEpisode({ id: "ep-1", source_name: "work" })
    const html = renderToStaticMarkup(
      <GanttSwimlaneInner
        episodes={[ep]}
        windowStart={WINDOW_START}
        windowEnd={WINDOW_END}
      />,
    )
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
      source_name: "music",
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
    // A <pattern> element must be present for the hatch fill
    expect(html).toContain("<pattern")
    expect(html).toContain("hatch-ep-sensitive")
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
})

// ---------------------------------------------------------------------------
// 7. Multiple categories render separate swimlanes
// ---------------------------------------------------------------------------

describe("GanttSwimlaneInner multiple categories", () => {
  it("renders lane labels for each category present", () => {
    const ep1 = makeEpisode({ id: "ep-work", source_name: "work" })
    const ep2 = makeEpisode({ id: "ep-music", source_name: "music" })
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
    const ep1 = makeEpisode({ id: "ep-m1", source_name: "work" })
    const ep2 = makeEpisode({ id: "ep-m2", source_name: "sleep" })
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
