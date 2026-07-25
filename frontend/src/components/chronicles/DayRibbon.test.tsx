import { describe, expect, it } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import type { ChroniclerEpisode } from "@/api/types"
import { DayRibbon } from "./DayRibbon"

const WINDOW_START = new Date("2026-04-25T00:00:00Z")
const WINDOW_END = new Date("2026-04-26T00:00:00Z")

function makeEpisode(overrides: Partial<ChroniclerEpisode> = {}): ChroniclerEpisode {
  const start = "2026-04-25T09:00:00Z"
  const end = "2026-04-25T10:00:00Z"
  return {
    id: "ep-1",
    source_name: "google_health.measurements",
    source_ref: "ref-1",
    episode_type: "workout_episode",
    start_at: start,
    end_at: end,
    precision: "minute",
    title: "Morning run",
    payload: {},
    privacy: "normal",
    retention_days: null,
    tombstone_at: null,
    canonical_start_at: start,
    canonical_end_at: end,
    canonical_title: "Morning run",
    canonical_privacy: "normal",
    corrected_at: null,
    correction_note: null,
    created_at: start,
    updated_at: start,
    category: "exercise",
    ...overrides,
  }
}

describe("DayRibbon — empty state", () => {
  it("renders the empty message when episodes is empty", () => {
    const html = renderToStaticMarkup(
      <DayRibbon episodes={[]} windowStart={WINDOW_START} windowEnd={WINDOW_END} />,
    )
    expect(html).toContain("day-ribbon-empty")
    expect(html).toContain("No activity recorded for this window")
  })

  it("renders a degraded note (not the quiet-day empty message) when the episodes fetch errored [bu-ep4ks.5]", () => {
    // An outage must not conflate with a genuinely quiet day (bu-ep4ks.5).
    const html = renderToStaticMarkup(
      <DayRibbon episodes={[]} windowStart={WINDOW_START} windowEnd={WINDOW_END} isError />,
    )
    expect(html).toContain("day-ribbon-degraded")
    expect(html).not.toContain("day-ribbon-empty")
    expect(html).not.toContain("No activity recorded for this window")
    expect(html).toContain("episodes could not be reached")
  })

  it("still renders the normal tracks when episodes is populated even if isError is true (stale cache)", () => {
    const html = renderToStaticMarkup(
      <DayRibbon
        episodes={[makeEpisode()]}
        windowStart={WINDOW_START}
        windowEnd={WINDOW_END}
        isError
      />,
    )
    expect(html).toContain("day-ribbon")
    expect(html).not.toContain("day-ribbon-degraded")
    expect(html).not.toContain("day-ribbon-empty")
  })
})

describe("DayRibbon — tracks", () => {
  it("renders a lived activity block for a non-calendar episode", () => {
    const html = renderToStaticMarkup(
      <DayRibbon
        episodes={[makeEpisode()]}
        windowStart={WINDOW_START}
        windowEnd={WINDOW_END}
      />,
    )
    expect(html).toContain("day-ribbon")
    expect(html).toContain("day-ribbon-activity-ep-1")
    expect(html).toContain("day-ribbon-activity-track")
  })

  it("renders calendar episodes on the ghost intent track, not as lived activity", () => {
    const intent = makeEpisode({
      id: "cal-1",
      source_name: "google_calendar.events",
      episode_type: "calendar_event",
      category: "other",
    })
    const html = renderToStaticMarkup(
      <DayRibbon episodes={[intent]} windowStart={WINDOW_START} windowEnd={WINDOW_END} />,
    )
    expect(html).toContain("day-ribbon-intent-cal-1")
    expect(html).not.toContain("day-ribbon-activity-cal-1")
  })

  it("does not leak a sensitive episode's title into the markup", () => {
    const sensitive = makeEpisode({
      id: "ep-sensitive",
      canonical_privacy: "sensitive",
      canonical_title: "Therapy session",
    })
    const html = renderToStaticMarkup(
      <DayRibbon episodes={[sensitive]} windowStart={WINDOW_START} windowEnd={WINDOW_END} />,
    )
    expect(html).toContain("day-ribbon-activity-ep-sensitive")
    expect(html).not.toContain("Therapy session")
  })
})
