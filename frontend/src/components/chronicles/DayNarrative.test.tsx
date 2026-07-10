import { describe, expect, it } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import type {
  ChroniclerRollupDay,
  ChroniclerRollupFlagRow,
  ChroniclerRollupsResponse,
} from "@/api/types"
import { DayNarrative } from "./DayNarrative"

function makeFlag(overrides: Partial<ChroniclerRollupFlagRow> = {}): ChroniclerRollupFlagRow {
  return {
    flag_type: "routine_break",
    severity: "info",
    detail: {},
    narrative: null,
    ...overrides,
  }
}

function makeDay(overrides: Partial<ChroniclerRollupDay> = {}): ChroniclerRollupDay {
  return {
    local_date: "2026-07-05",
    timezone: "Asia/Singapore",
    status: "materialized",
    lanes: [],
    flags: [],
    narrative: null,
    ...overrides,
  }
}

function makeResponse(
  day: ChroniclerRollupDay | null,
  overrides: Partial<ChroniclerRollupsResponse> = {},
): ChroniclerRollupsResponse {
  return {
    start_date: "2026-07-05",
    end_date: "2026-07-05",
    tz: "Asia/Singapore",
    days: day ? [day] : [],
    rollups_source_error: false,
    ...overrides,
  }
}

describe("DayNarrative — states", () => {
  it("renders a skeleton while loading", () => {
    const html = renderToStaticMarkup(<DayNarrative data={undefined} isLoading />)
    expect(html).toContain("day-narrative-skeleton")
  })

  it("renders a degraded note on source error (never truthful-empty)", () => {
    const html = renderToStaticMarkup(
      <DayNarrative data={makeResponse(makeDay({ status: "unknown" }), { rollups_source_error: true })} />,
    )
    expect(html).toContain("Day summary")
    expect(html).toContain("role=\"alert\"")
  })

  it("renders a degraded note when isError even without data", () => {
    const html = renderToStaticMarkup(<DayNarrative data={undefined} isError />)
    expect(html).toContain("Day summary")
    expect(html).toContain("role=\"alert\"")
  })

  it("renders the day prose summary when present", () => {
    const html = renderToStaticMarkup(
      <DayNarrative data={makeResponse(makeDay({ narrative: "A focused work day." }))} />,
    )
    expect(html).toContain("day-narrative")
    expect(html).toContain("A focused work day.")
  })

  it("renders flag labels when present", () => {
    const html = renderToStaticMarkup(
      <DayNarrative
        data={makeResponse(
          makeDay({
            narrative: "A focused work day.",
            flags: [makeFlag({ flag_type: "routine_break", narrative: "Skipped the gym." })],
          }),
        )}
      />,
    )
    expect(html).toContain("day-narrative-flag-routine_break")
    expect(html).toContain("Skipped the gym.")
  })

  it("renders nothing when narration is absent (normal, not an error)", () => {
    // Materialized day, real rows, but the labeling pass never ran.
    const html = renderToStaticMarkup(
      <DayNarrative data={makeResponse(makeDay({ narrative: null, flags: [makeFlag()] }))} />,
    )
    expect(html).toBe("")
  })

  it("renders nothing when narrative is blank/whitespace only", () => {
    const html = renderToStaticMarkup(
      <DayNarrative data={makeResponse(makeDay({ narrative: "   " }))} />,
    )
    expect(html).toBe("")
  })

  it("renders nothing (not an error) for a not-yet-materialized day", () => {
    const html = renderToStaticMarkup(
      <DayNarrative data={makeResponse(makeDay({ status: "not_yet_materialized", narrative: null }))} />,
    )
    expect(html).toBe("")
  })

  it("renders nothing when data is undefined and not loading/error", () => {
    const html = renderToStaticMarkup(<DayNarrative data={undefined} />)
    expect(html).toBe("")
  })

  it("renders the summary even if a flag carries no label", () => {
    const html = renderToStaticMarkup(
      <DayNarrative
        data={makeResponse(
          makeDay({
            narrative: "A quiet day.",
            flags: [makeFlag({ narrative: null })],
          }),
        )}
      />,
    )
    expect(html).toContain("A quiet day.")
    expect(html).not.toContain("day-narrative-flags")
  })
})
