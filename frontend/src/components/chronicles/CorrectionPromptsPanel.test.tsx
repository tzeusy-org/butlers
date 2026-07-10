import { describe, expect, it } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import type { ChroniclerCorrectionPrompts } from "@/api/types"
import { CorrectionPromptsPanel } from "./CorrectionPromptsPanel"

function makeResponse(
  overrides: Partial<ChroniclerCorrectionPrompts> = {},
): ChroniclerCorrectionPrompts {
  return {
    start_at: "2026-04-25T00:00:00Z",
    end_at: "2026-04-26T00:00:00Z",
    tz: "Asia/Singapore",
    prompts: [
      {
        episode_id: "ep-low",
        source_name: "owntracks.points",
        episode_type: "movement_episode",
        title: "Unknown stop",
        start_at: "2026-04-25T14:00:00Z",
        end_at: "2026-04-25T14:30:00Z",
        best_guess_lane: "travel",
        confidence: "low",
        evidence_refs: ["pe-1"],
        evidence_count: 1,
      },
    ],
    ...overrides,
  }
}

describe("CorrectionPromptsPanel — states", () => {
  it("renders a skeleton while loading", () => {
    const html = renderToStaticMarkup(<CorrectionPromptsPanel data={undefined} isLoading />)
    expect(html).toContain("prompts-skeleton")
  })

  it("renders a degraded note on error", () => {
    const html = renderToStaticMarkup(<CorrectionPromptsPanel data={undefined} isError />)
    expect(html).toContain("Correction prompts")
    expect(html).toContain("role=\"alert\"")
  })

  it("renders a low-confidence prompt with its best-guess lane and a confirm action", () => {
    const html = renderToStaticMarkup(<CorrectionPromptsPanel data={makeResponse()} />)
    expect(html).toContain("correction-prompts")
    expect(html).toContain("correction-prompt-ep-low")
    expect(html).toContain("travel")
    expect(html).toContain("correction-prompt-confirm-ep-low")
    expect(html).toContain("1 signal")
  })

  it("renders a reassuring empty state when nothing is low-confidence", () => {
    const html = renderToStaticMarkup(
      <CorrectionPromptsPanel data={makeResponse({ prompts: [] })} />,
    )
    expect(html).toContain("prompts-empty")
    expect(html).toContain("Nothing to confirm")
  })
})
