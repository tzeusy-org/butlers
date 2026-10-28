// @vitest-environment jsdom
/**
 * RowLink wiring for the unified navigation-intent primitive: a pointer dwell
 * on a row to /sessions/abc warms both the route chunk and the mapped query.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"

const loader = vi.fn(() => Promise.resolve({}))
vi.mock("@/lib/route-chunk-registry", () => ({
  resolveRouteChunkLoader: () => loader,
}))
vi.mock("@/api/index.ts", () => ({
  getSession: vi.fn((id: string) => Promise.resolve({ data: { id } })),
}))

import { RowLink } from "./RowLink"
import { NAV_INTENT_DELAY_MS } from "@/hooks/use-nav-intent"

beforeEach(() => {
  vi.useFakeTimers()
  loader.mockClear()
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe("RowLink: unified navigation intent", () => {
  it("warms both the chunk and the query after a pointer dwell on a session row", () => {
    const client = new QueryClient()
    const prefetchSpy = vi.spyOn(client, "prefetchQuery")
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <RowLink to="/sessions/abc" data-testid="row">
            session row
          </RowLink>
        </MemoryRouter>
      </QueryClientProvider>,
    )

    fireEvent.pointerEnter(screen.getByTestId("row"))
    expect(loader).not.toHaveBeenCalled()
    expect(prefetchSpy).not.toHaveBeenCalled()

    act(() => {
      vi.advanceTimersByTime(NAV_INTENT_DELAY_MS)
    })

    expect(loader).toHaveBeenCalledTimes(1)
    expect(prefetchSpy).toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: ["session-detail-global", "abc"] }),
    )
  })
})
