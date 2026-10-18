// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// EgressCatalogTile audit-log drill-down -- bu-vbznk
//
// A registered actor row links to /audit-log filtered by the audit action the server names;
// the "other" bucket (audit_action null) and rows from an older server stay plain text.
// ---------------------------------------------------------------------------

import { describe, expect, it, vi } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"
import { MemoryRouter } from "react-router"

import type { EgressActor } from "@/api/types"
import { EgressCatalogTile } from "./EgressCatalogTile"

type Actor = EgressActor & { audit_action?: string | null }

let mockActors: Actor[] = []

vi.mock("@/hooks/use-system", () => ({
  useEgressFacts: () => ({
    isPending: false,
    isError: false,
    isForbidden: false,
    data: { data: { actors: mockActors, catalog_covers_from: null }, meta: {} },
  }),
}))

vi.mock("@/components/ui/time", () => ({
  Time: ({ value }: { value: string }) => <time dateTime={value}>{value}</time>,
}))

function actor(overrides: Partial<Actor>): Actor {
  return {
    actor_id: "anthropic.claude",
    display_name: "Anthropic Claude API",
    last_seen_at: "2026-05-03T10:00:00Z",
    total_calls: 3,
    data_types: [],
    audit_action: "llm_api_call",
    ...overrides,
  }
}

function render(actors: Actor[]): string {
  mockActors = actors
  return renderToStaticMarkup(
    <MemoryRouter>
      <EgressCatalogTile />
    </MemoryRouter>,
  )
}

describe("EgressCatalogTile -- audit-log links", () => {
  it("links a registered actor to the audit log filtered by its action, with noise=all", () => {
    const html = render([actor({})])
    expect(html).toContain('href="/audit-log?action=llm_api_call&amp;noise=all"')
    expect(html).toContain('aria-label="View Anthropic Claude API audit events"')
  })

  it("URL-encodes the action", () => {
    const html = render([actor({ audit_action: "weird action/&x" })])
    expect(html).toContain("action=weird%20action%2F%26x&amp;noise=all")
  })

  it("keeps the other bucket as plain text", () => {
    const html = render([
      actor({ actor_id: "other", display_name: "Other / Unrecognized", audit_action: null }),
    ])
    expect(html).toContain("Other / Unrecognized")
    expect(html).not.toContain("<a ")
    expect(html).not.toContain("/audit-log")
  })

  it("keeps a row plain when the server sent no audit_action", () => {
    const html = render([actor({ audit_action: undefined })])
    expect(html).toContain("Anthropic Claude API")
    expect(html).not.toContain("/audit-log")
  })

  it("links only the rows that name an action", () => {
    const html = render([
      actor({}),
      actor({ actor_id: "other", display_name: "Other / Unrecognized", audit_action: null }),
    ])
    expect(html.match(/href="\/audit-log/g)).toHaveLength(1)
  })
})
