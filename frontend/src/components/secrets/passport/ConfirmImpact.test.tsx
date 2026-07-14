// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// ConfirmImpact tests — bu-cyyi3
//
// Coverage:
//   - Loading state renders "checking impact…"
//   - Fetch failure renders "impact unavailable — ..." (never a silent empty)
//   - meta.catalogue_available === false renders the same "unavailable" state
//   - Empty (tracked-but-zero-or-untracked) catalogue renders "impact not
//     tracked for this credential." — NEVER "nothing depends on this" wording,
//     since an empty confirm-time result must never read as an all-clear.
//   - Non-empty catalogue renders entries sorted severity DESC, reusing
//     WhatBreaksRow's exact vocabulary (severity pip + butler letter-mark).
// ---------------------------------------------------------------------------

import { describe, expect, it, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import * as React from "react"

import type { BreakEntry } from "@/api/types"
import type { ApiResponse } from "@/api/types"

// ---------------------------------------------------------------------------
// Mock the API client function
// ---------------------------------------------------------------------------

vi.mock("@/api/client", () => ({
  getBreaksCatalogue: vi.fn(),
}))

import { getBreaksCatalogue } from "@/api/client"
const mockGetBreaksCatalogue = vi.mocked(getBreaksCatalogue)

// ---------------------------------------------------------------------------
// Test helpers
// ---------------------------------------------------------------------------

function makeApiResponse(
  entries: BreakEntry[],
  meta: ApiResponse<BreakEntry[]>["meta"] = {},
): ApiResponse<BreakEntry[]> {
  return { data: entries, meta }
}

function makeBreakEntry(overrides: Partial<BreakEntry> = {}): BreakEntry {
  return {
    butler: "health",
    feature: "symptom sync",
    severity: "high",
    required_scopes: [],
    ...overrides,
  }
}

function renderWithQuery(ui: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0, gcTime: 0 } },
  })
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>)
}

import { ConfirmImpact } from "./WhatBreaks"

beforeEach(() => {
  vi.clearAllMocks()
})

describe("ConfirmImpact: loading state", () => {
  it("renders 'checking impact…' while fetching", () => {
    mockGetBreaksCatalogue.mockReturnValue(new Promise(() => {}))
    renderWithQuery(<ConfirmImpact provider="google" />)
    expect(screen.getByText("checking impact…")).toBeTruthy()
  })
})

describe("ConfirmImpact: unavailable state", () => {
  it("renders 'impact unavailable' on fetch error", async () => {
    mockGetBreaksCatalogue.mockRejectedValue(new Error("network error"))
    renderWithQuery(<ConfirmImpact provider="google" />)
    await waitFor(() => {
      expect(
        screen.getByText("impact unavailable: could not reach the dependency catalogue."),
      ).toBeTruthy()
    })
  })

  it("renders 'impact unavailable' when meta.catalogue_available is false", async () => {
    mockGetBreaksCatalogue.mockResolvedValue(makeApiResponse([], { catalogue_available: false }))
    renderWithQuery(<ConfirmImpact provider="google" />)
    await waitFor(() => {
      expect(
        screen.getByText("impact unavailable: could not reach the dependency catalogue."),
      ).toBeTruthy()
    })
  })
})

describe("ConfirmImpact: not-tracked state (honesty rule)", () => {
  it("renders 'impact not tracked' — never the WhatBreaks 'nothing depends' wording", async () => {
    mockGetBreaksCatalogue.mockResolvedValue(makeApiResponse([]))
    renderWithQuery(<ConfirmImpact provider="email" />)
    await waitFor(() => {
      expect(screen.getByText("impact not tracked for this credential.")).toBeTruthy()
    })
    expect(screen.queryByText(/nothing depends/i)).toBeNull()
  })
})

describe("ConfirmImpact: tracked entries", () => {
  it("renders each entry's feature and butler, sorted severity DESC", async () => {
    mockGetBreaksCatalogue.mockResolvedValue(
      makeApiResponse([
        makeBreakEntry({ butler: "lifestyle", feature: "step-export-low", severity: "low" }),
        makeBreakEntry({ butler: "health", feature: "heart-rate-high", severity: "high" }),
      ]),
    )
    renderWithQuery(<ConfirmImpact provider="google" />)

    await waitFor(() => {
      expect(screen.getByText("heart-rate-high")).toBeTruthy()
    })
    expect(screen.getByText("step-export-low")).toBeTruthy()

    const highEl = screen.getByText("heart-rate-high")
    const lowEl = screen.getByText("step-export-low")
    expect(highEl.compareDocumentPosition(lowEl) & Node.DOCUMENT_POSITION_FOLLOWING).toBeGreaterThan(0)
  })

  it("calls getBreaksCatalogue with the given provider slug", async () => {
    mockGetBreaksCatalogue.mockResolvedValue(makeApiResponse([]))
    renderWithQuery(<ConfirmImpact provider="spotify" />)
    await waitFor(() => {
      expect(mockGetBreaksCatalogue).toHaveBeenCalledWith({ provider: "spotify" })
    })
  })
})

describe("ConfirmImpact: onStateChange callback", () => {
  // The enclosing destructive-confirm panel (PageSystem/PageUser/PageCli/
  // GoogleAccountRow) relies on this callback to keep "yes, …" disabled
  // while impact is still loading — an uninformed confirm would defeat the
  // whole point of this component. This locks down the callback contract
  // independent of any one call site.
  it("reports 'loading' first, then the resolved state, and never fires 'loading' again", async () => {
    mockGetBreaksCatalogue.mockResolvedValue(makeApiResponse([]))
    const states: string[] = []
    renderWithQuery(<ConfirmImpact provider="email" onStateChange={(s) => states.push(s)} />)

    expect(states[0]).toBe("loading")
    await waitFor(() => {
      expect(states[states.length - 1]).toBe("not-tracked")
    })
    expect(states.filter((s) => s === "loading")).toHaveLength(1)
  })

  it("reports 'unavailable' on fetch error", async () => {
    mockGetBreaksCatalogue.mockRejectedValue(new Error("network error"))
    const states: string[] = []
    renderWithQuery(<ConfirmImpact provider="google" onStateChange={(s) => states.push(s)} />)

    await waitFor(() => {
      expect(states[states.length - 1]).toBe("unavailable")
    })
  })

  it("reports 'tracked' when entries are present", async () => {
    mockGetBreaksCatalogue.mockResolvedValue(makeApiResponse([makeBreakEntry()]))
    const states: string[] = []
    renderWithQuery(<ConfirmImpact provider="google" onStateChange={(s) => states.push(s)} />)

    await waitFor(() => {
      expect(states[states.length - 1]).toBe("tracked")
    })
  })
})
