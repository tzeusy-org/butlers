// @vitest-environment jsdom
/**
 * Caller wiring for the unified navigation-intent hook in the shell and the
 * sessions table: Sidebar activation, SessionTable row focus, and the
 * EntityFinder palette highlight each warm their destination chunk. The real
 * hook runs; the chunk registry records requested paths.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act } from "react"
import { createRoot, type Root } from "react-dom/client"
import { MemoryRouter } from "react-router"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, fireEvent, render } from "@testing-library/react"

const warmed: string[] = []

vi.mock("@/lib/route-chunk-registry", () => ({
  resolveRouteChunkLoader: (path: string) => () => {
    warmed.push(path)
    return Promise.resolve({})
  },
}))
vi.mock("@/lib/prefetch-registry", () => ({ resolvePrefetchTarget: () => null }))

vi.mock("@/hooks/use-butlers", () => ({ useButlers: vi.fn() }))
vi.mock("@/hooks/use-spend", () => ({ useSpendSummary: vi.fn() }))
vi.mock("@/hooks/use-qa-badge", () => ({ useBadgeCounts: vi.fn(() => ({})) }))
vi.mock("@/hooks/use-entities", () => ({
  useEntityFinderSearch: vi.fn(),
  useEntityNeighbours: vi.fn(),
  useEntityLinkedContacts: vi.fn(),
  useEntityGifts: vi.fn(),
  useEntityLoans: vi.fn(),
  useEntityTimeline: vi.fn(),
  useEntityMessageThreads: vi.fn(),
  useEntityDates: vi.fn(),
  useUpdateEntityDunbarTier: vi.fn(),
}))
vi.mock("@/hooks/use-search", () => ({ useSearch: vi.fn() }))
vi.mock("@/api/client.ts", () => ({
  getOwnerSetupStatus: vi.fn(async () => ({ entity_id: null })),
  getSession: vi.fn(() => Promise.resolve({ data: {} })),
  getApprovalDetail: vi.fn(),
  getIngestionEvent: vi.fn(),
  getTimeline: vi.fn(),
}))
vi.mock("radix-ui", async (importOriginal) => {
  const actual = await importOriginal<typeof import("radix-ui")>()
  return {
    ...actual,
    Tooltip: {
      ...actual.Tooltip,
      Portal: ({ children }: { children: React.ReactNode }) => children,
    },
  }
})

import EntityFinder from "@/components/layout/EntityFinder"
import Sidebar from "@/components/layout/Sidebar"
import { SessionTable } from "@/components/sessions/SessionTable"
import { dispatchOpenEntityFinder } from "@/lib/entity-finder"
import { useButlers } from "@/hooks/use-butlers"
import { useSpendSummary } from "@/hooks/use-spend"
import { useSearch } from "@/hooks/use-search"
import { useEntityFinderSearch, useEntityNeighbours } from "@/hooks/use-entities"
import { resetUseButlersMock, setUseButlersState } from "@/test-utils/use-butlers"
import type { SessionSummary } from "@/api/types"

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true

beforeEach(() => {
  warmed.length = 0
})

afterEach(cleanup)

describe("Sidebar activation warmup", () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => {
    vi.mocked(useSpendSummary).mockReturnValue({
      data: { data: { total_cost_usd: 1 } },
      isLoading: false,
    } as ReturnType<typeof useSpendSummary>)
    resetUseButlersMock()
    setUseButlersState({ data: { data: [], meta: {} } })
    container = document.createElement("div")
    document.body.appendChild(container)
    root = createRoot(container)
  })

  afterEach(() => {
    act(() => root.unmount())
    container.remove()
  })

  it("clicking a flat link warms its destination", () => {
    act(() => {
      root.render(
        <MemoryRouter>
          <Sidebar collapsed />
        </MemoryRouter>,
      )
    })
    const link = container.querySelector('a[href="/sessions"]')
    expect(link).toBeInstanceOf(HTMLAnchorElement)
    expect(warmed).toEqual([])
    fireEvent.click(link!)
    expect(warmed).toEqual(["/sessions"])
  })
})

describe("SessionTable row focus warmup", () => {
  it("focusing an interactive row warms the session detail chunk", () => {
    const session = {
      id: "sess-abc123",
      butler: "health",
      prompt: "p",
      trigger_source: "telegram",
      request_id: null,
      success: true,
      started_at: "2026-03-12T00:00:00Z",
      completed_at: "2026-03-12T00:00:02Z",
      duration_ms: 2000,
      input_tokens: 1,
      output_tokens: 1,
      cancelled_by_owner: false,
      model: null,
      complexity: null,
    } as SessionSummary
    const { container } = render(
      <QueryClientProvider client={new QueryClient()}>
        <SessionTable sessions={[session]} isLoading={false} onSessionClick={vi.fn()} />
      </QueryClientProvider>,
    )
    const row = container.querySelector<HTMLTableRowElement>('[data-testid="session-row"]')!
    fireEvent.focus(row)
    expect(warmed).toEqual(["/sessions/sess-abc123"])
  })
})

describe("EntityFinder highlight warmup", () => {
  it("warms the chunk of the highlighted page row (controlled cmdk value)", async () => {
    vi.mocked(useEntityFinderSearch).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: false,
    } as ReturnType<typeof useEntityFinderSearch>)
    vi.mocked(useEntityNeighbours).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: false,
    } as ReturnType<typeof useEntityNeighbours>)
    vi.mocked(useSearch).mockReturnValue({
      data: undefined,
      isFetching: false,
    } as unknown as ReturnType<typeof useSearch>)
    vi.mocked(useButlers).mockReturnValue({
      data: { data: [], meta: {} },
    } as unknown as ReturnType<typeof useButlers>)

    const container = document.createElement("div")
    document.body.appendChild(container)
    const root = createRoot(container)
    await act(async () => {
      root.render(
        <QueryClientProvider client={new QueryClient()}>
          <MemoryRouter>
            <EntityFinder />
          </MemoryRouter>
        </QueryClientProvider>,
      )
      await new Promise((resolve) => setTimeout(resolve, 0))
    })
    await act(async () => {
      dispatchOpenEntityFinder()
      await new Promise((resolve) => setTimeout(resolve, 0))
    })
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50))
    })
    const input = document.body.querySelector<HTMLInputElement>(
      "[data-testid='entity-finder-input']",
    )!
    warmed.length = 0
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        "value",
      )?.set
      setter?.call(input, "Sessions")
      input.dispatchEvent(new Event("input", { bubbles: true }))
      await new Promise((resolve) => setTimeout(resolve, 50))
    })
    expect(warmed).toContain("/sessions")
    act(() => root.unmount())
    container.remove()
  })
})
