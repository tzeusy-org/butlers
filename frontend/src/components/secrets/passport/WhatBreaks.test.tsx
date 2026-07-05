// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// WhatBreaks tests — bu-qo3sf
//
// Coverage:
//   - Loading state renders "loading…"
//   - Error state renders "unavailable"
//   - Empty catalogue renders "usage not tracked" (bu-xzaxm honesty fix — an
//     empty catalogue result is a coverage gap, never a verified "nothing
//     depends on this credential")
//   - meta.catalogue_available === false renders the same "unavailable" state
//   - Entries are rendered sorted by severity DESC (high → medium → low)
//   - WhatBreaks fetches from /api/secrets/breaks-catalogue (mocked)
//   - High-severity entries appear before medium, medium before low
//
// Uses @testing-library/react with a real QueryClient (staleTime=0, retries=0)
// and vi.mock to stub getBreaksCatalogue.
// ---------------------------------------------------------------------------

import { describe, expect, it, vi, beforeEach, afterEach } from "vitest"
import { cleanup, render, screen, waitFor } from "@testing-library/react"
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
  return {
    data: entries,
    meta,
  }
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
    defaultOptions: {
      queries: {
        retry: false,
        staleTime: 0,
        gcTime: 0,
      },
    },
  })
  return render(
    <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>,
  )
}

// ---------------------------------------------------------------------------
// Import component after mocks are established
// ---------------------------------------------------------------------------

import { WhatBreaks } from "./WhatBreaks"

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

beforeEach(() => {
  vi.clearAllMocks()
})

// This suite's new not-tracked/unavailable pair (bu-xzaxm) queries the whole
// document by exact text, so a prior test's un-unmounted render would leak
// into the next assertion — explicit cleanup keeps each `it` isolated.
afterEach(() => {
  cleanup()
})

describe("WhatBreaks: loading state", () => {
  it("renders loading… while fetching", () => {
    // Never resolves — stays in loading state
    mockGetBreaksCatalogue.mockReturnValue(new Promise(() => {}))
    renderWithQuery(<WhatBreaks provider="google" />)
    expect(screen.getByText("loading…")).toBeTruthy()
  })
})

describe("WhatBreaks: error state", () => {
  it("renders 'unavailable' on fetch error", async () => {
    mockGetBreaksCatalogue.mockRejectedValue(new Error("network error"))
    renderWithQuery(<WhatBreaks provider="google" />)
    await waitFor(() => {
      expect(screen.getByText("unavailable")).toBeTruthy()
    })
  })
})

describe("WhatBreaks: empty catalogue", () => {
  it("renders 'usage not tracked' (never a confident 'nothing depends') when no entries", async () => {
    mockGetBreaksCatalogue.mockResolvedValue(makeApiResponse([]))
    renderWithQuery(<WhatBreaks provider="google" />)
    await waitFor(() => {
      expect(screen.getByText("usage not tracked")).toBeTruthy()
    })
    expect(screen.queryByText("Nothing depends on this credential.")).toBeNull()
  })

  it("renders 'unavailable' (not 'usage not tracked') when meta.catalogue_available is false", async () => {
    mockGetBreaksCatalogue.mockResolvedValue(
      makeApiResponse([], { catalogue_available: false }),
    )
    renderWithQuery(<WhatBreaks provider="google" />)
    await waitFor(() => {
      expect(screen.getByText("unavailable")).toBeTruthy()
    })
    expect(screen.queryByText("usage not tracked")).toBeNull()
  })
})

describe("WhatBreaks: entry rendering", () => {
  it("renders each entry's feature name", async () => {
    mockGetBreaksCatalogue.mockResolvedValue(
      makeApiResponse([
        makeBreakEntry({ butler: "health", feature: "symptom sync", severity: "high" }),
        makeBreakEntry({ butler: "calendar", feature: "event polling", severity: "medium" }),
      ]),
    )
    renderWithQuery(<WhatBreaks provider="google" />)
    await waitFor(() => {
      expect(screen.getByText("symptom sync")).toBeTruthy()
      expect(screen.getByText("event polling")).toBeTruthy()
    })
  })

  it("renders each entry's butler name", async () => {
    mockGetBreaksCatalogue.mockResolvedValue(
      makeApiResponse([
        makeBreakEntry({ butler: "health", feature: "symptom sync", severity: "high" }),
      ]),
    )
    renderWithQuery(<WhatBreaks provider="google" />)
    await waitFor(() => {
      expect(screen.getAllByText("health").length).toBeGreaterThan(0)
    })
  })
})

describe("WhatBreaks: severity DESC ordering", () => {
  it("renders high before medium before low", async () => {
    // Intentionally out-of-order from the server
    mockGetBreaksCatalogue.mockResolvedValue(
      makeApiResponse([
        makeBreakEntry({ butler: "lifestyle", feature: "step-export-low",  severity: "low"    }),
        makeBreakEntry({ butler: "calendar",  feature: "event-sync-med",   severity: "medium" }),
        makeBreakEntry({ butler: "health",    feature: "heart-rate-high",  severity: "high"   }),
      ]),
    )
    renderWithQuery(<WhatBreaks provider="google" />)
    await waitFor(() => {
      expect(screen.getByText("heart-rate-high")).toBeTruthy()
    })

    const highEl   = screen.getByText("heart-rate-high")
    const mediumEl = screen.getByText("event-sync-med")
    const lowEl    = screen.getByText("step-export-low")

    // A.compareDocumentPosition(B) & 4 means B follows A (B comes after A)
    expect(highEl.compareDocumentPosition(mediumEl) & Node.DOCUMENT_POSITION_FOLLOWING).toBeGreaterThan(0)
    expect(mediumEl.compareDocumentPosition(lowEl) & Node.DOCUMENT_POSITION_FOLLOWING).toBeGreaterThan(0)
  })

  it("groups all high entries before any medium entries", async () => {
    mockGetBreaksCatalogue.mockResolvedValue(
      makeApiResponse([
        makeBreakEntry({ butler: "calendar",  feature: "event-sync-m",  severity: "medium" }),
        makeBreakEntry({ butler: "health",    feature: "heart-rate-h1", severity: "high"   }),
        makeBreakEntry({ butler: "lifestyle", feature: "step-export-h2", severity: "high"   }),
      ]),
    )
    renderWithQuery(<WhatBreaks provider="google" />)
    await waitFor(() => {
      expect(screen.getByText("event-sync-m")).toBeTruthy()
    })

    // Use getAllByText to get the DOM nodes in document order
    const highEl1   = screen.getByText("heart-rate-h1")
    const highEl2   = screen.getByText("step-export-h2")
    const mediumEl  = screen.getByText("event-sync-m")

    // compareDocumentPosition: DOCUMENT_POSITION_FOLLOWING = 4
    // A.compareDocumentPosition(B) & 4 means B follows A
    const h1BeforeM = (highEl1.compareDocumentPosition(mediumEl) & Node.DOCUMENT_POSITION_FOLLOWING) > 0
    const h2BeforeM = (highEl2.compareDocumentPosition(mediumEl) & Node.DOCUMENT_POSITION_FOLLOWING) > 0

    expect(h1BeforeM).toBe(true)
    expect(h2BeforeM).toBe(true)
  })
})

describe("WhatBreaks: API call", () => {
  it("calls getBreaksCatalogue with the provider slug", async () => {
    mockGetBreaksCatalogue.mockResolvedValue(makeApiResponse([]))
    renderWithQuery(<WhatBreaks provider="spotify" />)
    await waitFor(() => {
      expect(mockGetBreaksCatalogue).toHaveBeenCalledWith({ provider: "spotify" })
    })
  })

  it("calls getBreaksCatalogue without provider when omitted", async () => {
    mockGetBreaksCatalogue.mockResolvedValue(makeApiResponse([]))
    renderWithQuery(<WhatBreaks />)
    await waitFor(() => {
      expect(mockGetBreaksCatalogue).toHaveBeenCalledWith(undefined)
    })
  })
})
