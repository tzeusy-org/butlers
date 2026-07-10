import { describe, expect, it } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import type { ChroniclerWhoYouWereWithResponse } from "@/api/types"
import { WhoYouWereWithPanel } from "./WhoYouWereWithPanel"

function makeResponse(
  overrides: Partial<ChroniclerWhoYouWereWithResponse> = {},
): ChroniclerWhoYouWereWithResponse {
  return {
    start_at: "2026-04-25T00:00:00Z",
    end_at: "2026-04-26T00:00:00Z",
    tz: "Asia/Singapore",
    companions: [
      {
        entity_id: "ent-1",
        display_name: "Alice",
        unattributed: false,
        channel: "Telegram",
        co_present_seconds: 2 * 3600,
        episode_count: 3,
      },
    ],
    companion_names_unavailable: false,
    who_you_were_with_source_error: false,
    ...overrides,
  }
}

describe("WhoYouWereWithPanel — states", () => {
  it("renders a skeleton while loading", () => {
    const html = renderToStaticMarkup(<WhoYouWereWithPanel data={undefined} isLoading />)
    expect(html).toContain("who-skeleton")
  })

  it("renders a degraded note on source error (never truthful-empty)", () => {
    const html = renderToStaticMarkup(
      <WhoYouWereWithPanel data={makeResponse({ who_you_were_with_source_error: true, companions: [] })} />,
    )
    expect(html).toContain("Who you were with")
    expect(html).toContain("role=\"alert\"")
  })

  it("renders a resolved companion with name, channel, and duration", () => {
    const html = renderToStaticMarkup(<WhoYouWereWithPanel data={makeResponse()} />)
    expect(html).toContain("who-you-were-with")
    expect(html).toContain("Alice")
    expect(html).toContain("Telegram")
    expect(html).toContain("2h")
  })

  it("renders an unattributed participant rather than dropping it", () => {
    const html = renderToStaticMarkup(
      <WhoYouWereWithPanel
        data={makeResponse({
          companions: [
            {
              entity_id: null,
              display_name: null,
              unattributed: true,
              channel: "in-person",
              co_present_seconds: 1800,
              episode_count: 1,
            },
          ],
        })}
      />,
    )
    expect(html).toContain("Someone (unattributed)")
  })

  it("surfaces a degraded names note but still lists entries when names are unavailable", () => {
    const html = renderToStaticMarkup(
      <WhoYouWereWithPanel
        data={makeResponse({
          companion_names_unavailable: true,
          companions: [
            {
              entity_id: "ent-2",
              display_name: null,
              unattributed: false,
              channel: "email",
              co_present_seconds: 600,
              episode_count: 1,
            },
          ],
        })}
      />,
    )
    expect(html).toContain("Companion names")
    expect(html).toContain("Name unavailable")
    expect(html).toContain("email")
  })

  it("renders a friendly empty state when no companions", () => {
    const html = renderToStaticMarkup(
      <WhoYouWereWithPanel data={makeResponse({ companions: [] })} />,
    )
    expect(html).toContain("who-empty")
  })
})
