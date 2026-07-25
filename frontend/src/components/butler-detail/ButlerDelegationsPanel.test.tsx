// @vitest-environment jsdom
/**
 * ButlerDelegationsPanel -- RTL tests (bu-ep4ks.3).
 *
 * Tests:
 *  - Renders the panel container
 *  - Empty state copy for both directions when no rows
 *  - Loading state does not render empty-state copy
 *  - Error state renders a distinct message
 *  - Outgoing/incoming lists filter by asking_butler / target_butler
 *  - callback_failed / task_conflict render a visually distinct wake badge;
 *    "not_applicable" renders no badge at all
 */

import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, cleanup } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"

import type { DelegationLedgerEntry } from "@/api/types"
import { ButlerDelegationsPanel } from "./ButlerDelegationsPanel"

vi.mock("@/hooks/use-delegation", () => ({
  useDelegationLedger: vi.fn(),
}))

vi.mock("@/components/ui/time", () => ({
  Time: ({ value }: { value: string }) => <time dateTime={value}>{value}</time>,
}))

import { useDelegationLedger } from "@/hooks/use-delegation"

const mockUseDelegationLedger = useDelegationLedger as unknown as ReturnType<typeof vi.fn>

function makeEntry(overrides: Partial<DelegationLedgerEntry> = {}): DelegationLedgerEntry {
  return {
    id: "11111111-1111-1111-1111-111111111111",
    asked_at: "2026-07-25T10:00:00Z",
    asking_butler: "finance",
    question: "Who is Alice's employer?",
    target_butler: "relationship",
    catalog_match_id: null,
    catalog_score: null,
    status: "answered",
    reason: null,
    answer: "Acme Corp.",
    answered_at: "2026-07-25T10:05:00Z",
    answering_butler: "relationship",
    answer_digest: null,
    wake_key: null,
    wake_state: "not_applicable",
    wake_task_id: null,
    wake_task_name: null,
    wake_updated_at: null,
    ...overrides,
  }
}

function renderPanel(butlerName = "finance") {
  const queryClient = new QueryClient()
  return render(
    <QueryClientProvider client={queryClient}>
      <ButlerDelegationsPanel butlerName={butlerName} />
    </QueryClientProvider>,
  )
}

function stubQueries({
  outgoing = { data: undefined, isLoading: false, isError: false },
  incoming = { data: undefined, isLoading: false, isError: false },
}: {
  outgoing?: { data: unknown; isLoading: boolean; isError: boolean }
  incoming?: { data: unknown; isLoading: boolean; isError: boolean }
} = {}) {
  mockUseDelegationLedger.mockImplementation((params: { asking_butler?: string }) => {
    return params.asking_butler ? outgoing : incoming
  })
}

afterEach(() => {
  cleanup()
  mockUseDelegationLedger.mockReset()
})

describe("ButlerDelegationsPanel", () => {
  it("renders the panel container", () => {
    stubQueries()
    renderPanel()
    expect(screen.getByTestId("panel-delegations")).toBeDefined()
  })

  it("renders empty-state copy for both directions when no rows", () => {
    stubQueries()
    renderPanel()
    expect(screen.getByText("no delegated questions asked")).toBeDefined()
    expect(screen.getByText("no delegated questions routed here")).toBeDefined()
  })

  it("does not render empty-state copy while loading", () => {
    stubQueries({
      outgoing: { data: undefined, isLoading: true, isError: false },
      incoming: { data: undefined, isLoading: true, isError: false },
    })
    renderPanel()
    expect(screen.queryByText("no delegated questions asked")).toBeNull()
    expect(screen.queryByText("no delegated questions routed here")).toBeNull()
  })

  it("renders a distinct error message when a query fails", () => {
    stubQueries({
      outgoing: { data: undefined, isLoading: false, isError: true },
      incoming: { data: undefined, isLoading: false, isError: false },
    })
    renderPanel()
    expect(screen.getByTestId("delegations-outgoing-error")).toBeDefined()
  })

  it("renders outgoing rows filtered by asking_butler", () => {
    const entry = makeEntry({ question: "Outgoing question" })
    stubQueries({ outgoing: { data: { data: [entry] }, isLoading: false, isError: false } })
    renderPanel()
    expect(screen.getByText("Outgoing question")).toBeDefined()
    expect(mockUseDelegationLedger).toHaveBeenCalledWith(
      expect.objectContaining({ asking_butler: "finance" }),
    )
  })

  it("renders incoming rows filtered by target_butler", () => {
    const entry = makeEntry({ question: "Incoming question", asking_butler: "relationship" })
    stubQueries({ incoming: { data: { data: [entry] }, isLoading: false, isError: false } })
    renderPanel()
    expect(screen.getByText("Incoming question")).toBeDefined()
    expect(mockUseDelegationLedger).toHaveBeenCalledWith(
      expect.objectContaining({ target_butler: "finance" }),
    )
  })

  it("renders a distinct wake badge for callback_failed", () => {
    const entry = makeEntry({ wake_state: "callback_failed" })
    stubQueries({ outgoing: { data: { data: [entry] }, isLoading: false, isError: false } })
    renderPanel()
    const badge = screen.getByTestId("delegation-wake-badge")
    expect(badge.textContent).toContain("callback failed")
  })

  it("renders a distinct wake badge for task_conflict", () => {
    const entry = makeEntry({ wake_state: "task_conflict" })
    stubQueries({ outgoing: { data: { data: [entry] }, isLoading: false, isError: false } })
    renderPanel()
    const badge = screen.getByTestId("delegation-wake-badge")
    expect(badge.textContent).toContain("task conflict")
  })

  it("renders no wake badge for not_applicable", () => {
    const entry = makeEntry({ wake_state: "not_applicable" })
    stubQueries({ outgoing: { data: { data: [entry] }, isLoading: false, isError: false } })
    renderPanel()
    expect(screen.queryByTestId("delegation-wake-badge")).toBeNull()
  })
})
