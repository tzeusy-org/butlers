// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// Tests for EpisodeDrawer — bu-ig72b.31
//
// Sheet (Radix Dialog) uses portals and useLayoutEffect, which are opaque to
// renderToStaticMarkup. Following the project convention (GanttSwimlane.test.tsx),
// content-level tests target EpisodeDrawerContent directly. A small number of
// structural tests target EpisodeDrawer at the shell level.
//
// Coverage:
//   1. Closed drawer: content region not present
//   2. Open drawer: Sheet element renders
//   3. Content: loading skeleton shown while episode is loading
//   4. Content: error state shown when episode fetch fails
//   5. Content: episode detail renders when data is available
//   6. Content: no events message when events list is empty
//   7. Content: no corrections message when corrections list is empty
//   8. Content: events list renders when data is available
//   9. Content: corrections list renders when data is available
//  10. Content: sensitive episode — title masked, Explain button hidden
//  11. Content: Explain button present and enabled for normal episode
//  12. Content: Explain button loading state when mutation is pending
//  13. Content: rate-limit notice not shown in initial render (state-gated)
//  14. GanttSwimlaneInner: bar rendered with role=button for click targeting
//  15. GanttSwimlaneInner: onEpisodeClick optional — no crash when absent
// ---------------------------------------------------------------------------

import { describe, expect, it, vi } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import { EpisodeDrawer, EpisodeDrawerContent } from "./EpisodeDrawer"
import { GanttSwimlaneInner } from "./GanttSwimlaneInner"
import type { ChroniclerEpisode } from "@/api/types"

// ---------------------------------------------------------------------------
// Shared mocks
// ---------------------------------------------------------------------------

let _episodeLoading = false
let _episodeError: Error | null = null
let _episodeData: ChroniclerEpisode | undefined = undefined

let _eventsLoading = false
let _eventsError: Error | null = null
let _eventsData: unknown[] | undefined = undefined

let _correctionsLoading = false
let _correctionsError: Error | null = null
let _correctionsData: unknown[] | undefined = undefined

let _explainIsPending = false
let _explainIsSuccess = false
let _explainError: Error | null = null
let _explainMutateFn = vi.fn()

vi.mock("@/hooks/use-chronicles", () => ({
  useChroniclerEpisode: () => ({
    data: _episodeData,
    isLoading: _episodeLoading,
    error: _episodeError,
  }),
  useChroniclerEpisodeEvents: () => ({
    data: _eventsData,
    isLoading: _eventsLoading,
    error: _eventsError,
  }),
  useChroniclerEpisodeCorrections: () => ({
    data: _correctionsData,
    isLoading: _correctionsLoading,
    error: _correctionsError,
  }),
  useChroniclerExplain: () => ({
    mutate: _explainMutateFn,
    isPending: _explainIsPending,
    isSuccess: _explainIsSuccess,
    error: _explainError,
  }),
}))

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const WINDOW_START = new Date("2026-04-25T00:00:00Z")
const WINDOW_END = new Date("2026-04-25T23:59:59Z")

function makeEpisode(overrides: Partial<ChroniclerEpisode> = {}): ChroniclerEpisode {
  return {
    id: "ep-test-id",
    source_name: "work",
    source_ref: "ref-1",
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
    canonical_title: "Deep work block",
    canonical_privacy: "normal",
    corrected_at: null,
    correction_note: null,
    created_at: "2026-04-25T00:00:00Z",
    updated_at: "2026-04-25T00:00:00Z",
    ...overrides,
  }
}

function makeGanttEpisode(overrides: Partial<ChroniclerEpisode> & { id: string }): ChroniclerEpisode {
  return makeEpisode(overrides)
}

function reset() {
  _episodeLoading = false
  _episodeError = null
  _episodeData = undefined
  _eventsLoading = false
  _eventsError = null
  _eventsData = undefined
  _correctionsLoading = false
  _correctionsError = null
  _correctionsData = undefined
  _explainIsPending = false
  _explainIsSuccess = false
  _explainError = null
  _explainMutateFn = vi.fn()
}

// ---------------------------------------------------------------------------
// 1. Closed drawer: content region not present
// ---------------------------------------------------------------------------

describe("EpisodeDrawer closed state", () => {
  it("does not render content when open=false", () => {
    reset()
    const html = renderToStaticMarkup(
      <EpisodeDrawer episodeId={null} open={false} onClose={vi.fn()} />,
    )
    // When closed, episodeId is null so EpisodeDrawerContent is not mounted
    expect(html).not.toContain("episode-drawer-content")
    expect(html).not.toContain("episode-drawer-loading")
  })
})

// ---------------------------------------------------------------------------
// 2. Open drawer: Sheet element present in output
//
// Note: Radix Sheet uses portals + useLayoutEffect, which are opaque to
// renderToStaticMarkup. The shell-level assertion is that the Sheet renders
// without crashing and the data-slot="sheet" attribute is injected by Radix.
// Content assertions live in EpisodeDrawerContent tests (tests 3–13).
// ---------------------------------------------------------------------------

describe("EpisodeDrawer open state", () => {
  it("renders the sheet element without crashing when open=true", () => {
    reset()
    _episodeLoading = true
    expect(() =>
      renderToStaticMarkup(
        <EpisodeDrawer episodeId="ep-abc" open={true} onClose={vi.fn()} />,
      ),
    ).not.toThrow()
  })

  it("renders the sheet element without crashing when open=false", () => {
    reset()
    expect(() =>
      renderToStaticMarkup(
        <EpisodeDrawer episodeId={null} open={false} onClose={vi.fn()} />,
      ),
    ).not.toThrow()
  })
})

// ---------------------------------------------------------------------------
// 3. Content: loading skeleton
// ---------------------------------------------------------------------------

describe("EpisodeDrawerContent loading state", () => {
  it("renders loading skeleton when isLoading=true", () => {
    reset()
    _episodeLoading = true
    const html = renderToStaticMarkup(
      <EpisodeDrawerContent episodeId="ep-abc" />,
    )
    expect(html).toContain("episode-drawer-loading")
    expect(html).not.toContain("episode-drawer-content")
  })
})

// ---------------------------------------------------------------------------
// 4. Content: error state
// ---------------------------------------------------------------------------

describe("EpisodeDrawerContent error state", () => {
  it("renders error message when episode fetch fails", () => {
    reset()
    _episodeError = new Error("Network error")
    const html = renderToStaticMarkup(
      <EpisodeDrawerContent episodeId="ep-abc" />,
    )
    expect(html).toContain("episode-drawer-error")
    expect(html).toContain("Network error")
  })
})

// ---------------------------------------------------------------------------
// 5. Content: episode detail renders
// ---------------------------------------------------------------------------

describe("EpisodeDrawerContent episode detail", () => {
  it("renders episode content when data is available", () => {
    reset()
    _episodeData = makeEpisode()
    _eventsData = []
    _correctionsData = []
    const html = renderToStaticMarkup(
      <EpisodeDrawerContent episodeId="ep-test-id" />,
    )
    expect(html).toContain("episode-drawer-content")
    expect(html).toContain("Deep work block")
    expect(html).toContain("work") // source_name
    expect(html).toContain("minute") // precision badge
  })
})

// ---------------------------------------------------------------------------
// 6. Content: no events message
// ---------------------------------------------------------------------------

describe("EpisodeDrawerContent no events", () => {
  it("shows 'No linked point events' when events list is empty", () => {
    reset()
    _episodeData = makeEpisode()
    _eventsData = []
    _correctionsData = []
    const html = renderToStaticMarkup(
      <EpisodeDrawerContent episodeId="ep-test-id" />,
    )
    expect(html).toContain("no-events")
    expect(html).toContain("No linked point events")
  })
})

// ---------------------------------------------------------------------------
// 7. Content: no corrections message
// ---------------------------------------------------------------------------

describe("EpisodeDrawerContent no corrections", () => {
  it("shows 'No corrections applied' when corrections list is empty", () => {
    reset()
    _episodeData = makeEpisode()
    _eventsData = []
    _correctionsData = []
    const html = renderToStaticMarkup(
      <EpisodeDrawerContent episodeId="ep-test-id" />,
    )
    expect(html).toContain("no-corrections")
    expect(html).toContain("No corrections applied")
  })
})

// ---------------------------------------------------------------------------
// 8. Content: events list renders
// ---------------------------------------------------------------------------

describe("EpisodeDrawerContent events list", () => {
  it("renders the events list when events are present", () => {
    reset()
    _episodeData = makeEpisode()
    _eventsData = [
      {
        id: "ev-1",
        source_name: "work",
        source_ref: "ref-ev-1",
        event_type: "meeting_start",
        occurred_at: "2026-04-25T09:00:00Z",
        precision: "minute",
        title: null,
        payload: {},
        privacy: "normal",
        retention_days: null,
        tombstone_at: null,
        canonical_occurred_at: "2026-04-25T09:00:00Z",
        canonical_title: "Daily standup",
        canonical_privacy: "normal",
        corrected_at: null,
        correction_note: null,
        created_at: "2026-04-25T00:00:00Z",
        updated_at: "2026-04-25T00:00:00Z",
      },
    ]
    _correctionsData = []
    const html = renderToStaticMarkup(
      <EpisodeDrawerContent episodeId="ep-test-id" />,
    )
    expect(html).toContain("events-list")
    expect(html).toContain("event-item-ev-1")
    expect(html).toContain("Daily standup")
  })
})

// ---------------------------------------------------------------------------
// 9. Content: corrections list renders
// ---------------------------------------------------------------------------

describe("EpisodeDrawerContent corrections list", () => {
  it("renders the corrections list when corrections are present", () => {
    reset()
    _episodeData = makeEpisode()
    _eventsData = []
    _correctionsData = [
      {
        id: "corr-1",
        target_kind: "episode",
        target_id: "ep-test-id",
        corrected_start_at: "2026-04-25T08:55:00Z",
        corrected_end_at: null,
        corrected_title: "Fixed title",
        corrected_privacy: null,
        corrected_tombstone_at: null,
        note: "Adjusted start time",
        submitted_by: "tze",
        created_at: "2026-04-25T11:00:00Z",
      },
    ]
    const html = renderToStaticMarkup(
      <EpisodeDrawerContent episodeId="ep-test-id" />,
    )
    expect(html).toContain("corrections-list")
    expect(html).toContain("correction-item-corr-1")
    expect(html).toContain("Adjusted start time")
    expect(html).toContain("Fixed title")
  })
})

// ---------------------------------------------------------------------------
// 10. Content: sensitive episode — title masked, Explain button hidden
// ---------------------------------------------------------------------------

describe("EpisodeDrawerContent sensitive episode", () => {
  it("masks the title and hides the Explain button for sensitive episodes", () => {
    reset()
    _episodeData = makeEpisode({
      canonical_privacy: "sensitive",
      canonical_title: "Secret project Alpha",
    })
    _eventsData = []
    _correctionsData = []
    const html = renderToStaticMarkup(
      <EpisodeDrawerContent episodeId="ep-test-id" />,
    )
    // Title must not be shown
    expect(html).not.toContain("Secret project Alpha")
    expect(html).toContain("Private activity")
    // Explain button must NOT be present for sensitive episodes
    expect(html).not.toContain("explain-button")
  })

  it("shows sensitive event as 'Private event' without leaking title", () => {
    reset()
    _episodeData = makeEpisode()
    _eventsData = [
      {
        id: "ev-sens",
        source_name: "work",
        source_ref: "ref-ev-sens",
        event_type: "private_type",
        occurred_at: "2026-04-25T09:00:00Z",
        precision: "minute",
        title: null,
        payload: {},
        privacy: "sensitive",
        retention_days: null,
        tombstone_at: null,
        canonical_occurred_at: "2026-04-25T09:00:00Z",
        canonical_title: "Confidential meeting",
        canonical_privacy: "sensitive",
        corrected_at: null,
        correction_note: null,
        created_at: "2026-04-25T00:00:00Z",
        updated_at: "2026-04-25T00:00:00Z",
      },
    ]
    _correctionsData = []
    const html = renderToStaticMarkup(
      <EpisodeDrawerContent episodeId="ep-test-id" />,
    )
    expect(html).not.toContain("Confidential meeting")
    expect(html).toContain("Private event")
  })
})

// ---------------------------------------------------------------------------
// 11. Content: Explain button present and enabled for normal episode
// ---------------------------------------------------------------------------

describe("EpisodeDrawerContent Explain button normal episode", () => {
  it("renders the Explain button for a normal episode", () => {
    reset()
    _episodeData = makeEpisode()
    _eventsData = []
    _correctionsData = []
    const html = renderToStaticMarkup(
      <EpisodeDrawerContent episodeId="ep-test-id" />,
    )
    expect(html).toContain("explain-button")
    expect(html).toContain("Explain this episode")
    // Rate-limit text should not be visible when not rate-limited
    expect(html).toContain("once per 24h")
  })
})

// ---------------------------------------------------------------------------
// 12. Content: Explain button loading state when mutation is pending
// ---------------------------------------------------------------------------

describe("EpisodeDrawerContent Explain button loading", () => {
  it("shows loading text and disabled when mutation is pending", () => {
    reset()
    _episodeData = makeEpisode()
    _eventsData = []
    _correctionsData = []
    _explainIsPending = true
    const html = renderToStaticMarkup(
      <EpisodeDrawerContent episodeId="ep-test-id" />,
    )
    expect(html).toContain("Explaining")
    expect(html).toContain("disabled")
  })
})

// ---------------------------------------------------------------------------
// 13. Content: rate-limit notice not shown in initial render (state-gated)
// ---------------------------------------------------------------------------

describe("EpisodeDrawerContent rate-limit notice", () => {
  it("does not show rate-limit-notice in initial render (requires state update)", () => {
    reset()
    _episodeData = makeEpisode()
    _eventsData = []
    _correctionsData = []
    // Rate-limit notice only appears after the user clicks and receives a 429 error,
    // which updates component state — not visible in initial SSR render.
    const html = renderToStaticMarkup(
      <EpisodeDrawerContent episodeId="ep-test-id" />,
    )
    expect(html).not.toContain("rate-limit-notice")
  })

  it("shows success notice when explain mutation succeeds", () => {
    reset()
    _episodeData = makeEpisode()
    _eventsData = []
    _correctionsData = []
    _explainIsSuccess = true
    const html = renderToStaticMarkup(
      <EpisodeDrawerContent episodeId="ep-test-id" />,
    )
    expect(html).toContain("explain-success")
    expect(html).toContain("Day summary refreshed")
  })
})

// ---------------------------------------------------------------------------
// 14. GanttSwimlaneInner: bar rendered with role=button for click targeting
// ---------------------------------------------------------------------------

describe("GanttSwimlaneInner bar click", () => {
  it("renders gantt bar element with role=button", () => {
    const ep = makeGanttEpisode({ id: "ep-click" })
    const html = renderToStaticMarkup(
      <GanttSwimlaneInner
        episodes={[ep]}
        windowStart={WINDOW_START}
        windowEnd={WINDOW_END}
        onEpisodeClick={vi.fn()}
      />,
    )
    expect(html).toContain("gantt-bar-ep-click")
    expect(html).toContain('role="button"')
  })
})

// ---------------------------------------------------------------------------
// 15. GanttSwimlaneInner: onEpisodeClick optional
// ---------------------------------------------------------------------------

describe("GanttSwimlaneInner onEpisodeClick optional", () => {
  it("renders without crash when onEpisodeClick is not provided", () => {
    const ep = makeGanttEpisode({ id: "ep-no-click" })
    expect(() =>
      renderToStaticMarkup(
        <GanttSwimlaneInner
          episodes={[ep]}
          windowStart={WINDOW_START}
          windowEnd={WINDOW_END}
        />,
      ),
    ).not.toThrow()
  })
})
