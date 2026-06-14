/**
 * SettingsSpendPage — /settings/spend  [bu-qu8ma.4]
 *
 * Covers the create-rule UI:
 *   - "+ Add rule" affordance renders and opens the create form
 *   - Submitting the form POSTs to /api/spend/rules with the evaluator-shaped
 *     payload (condition: {butler, complexity}, action: {model}) and refreshes
 *     the rules list
 *   - An empty condition (no butler / no tier) produces a catch-all rule
 *   - Submitting without any effect (no model, no cap) does not POST
 *   - The trigger condition dim and max_cost_per_call effect are threaded into
 *     the payload; a cap-only rule (no route-to model) is allowed  [bu-xclyn]
 */

// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { render, cleanup, fireEvent, screen, act, waitFor } from "@testing-library/react"
import { MemoryRouter } from "react-router"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"

import SettingsSpendPage from "@/pages/SettingsSpendPage"

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

const apiFetchMock = vi.fn()

vi.mock("@/api/client", () => ({
  apiFetch: (...args: unknown[]) => apiFetchMock(...args),
}))

vi.mock("@/hooks/use-spend-stream", () => ({
  useSpendStream: () => ({ streamedCostUsd: 0 }),
}))

vi.mock("@/hooks/use-model-catalog", () => ({
  useModelCatalog: () => ({
    data: {
      data: [
        { id: "1", model_id: "claude-haiku", complexity_tier: "cheap" },
        { id: "2", model_id: "claude-sonnet", complexity_tier: "workhorse" },
      ],
    },
  }),
}))

vi.mock("sonner", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  },
}))

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function defaultApiFetch(path: string) {
  if (path === "/spend/forecast") {
    return Promise.resolve({
      data: {
        days: [],
        projected_eom_usd: 0,
        days_in_month: 30,
        days_elapsed: 1,
        mtd_usd: 0,
        ceiling_usd: null,
      },
    })
  }
  if (path.startsWith("/spend/breakdown")) {
    return Promise.resolve({ data: { by: "butler", breakdown: {} } })
  }
  if (path === "/spend/rules") {
    return Promise.resolve({ data: [] })
  }
  return Promise.resolve({ data: {} })
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <SettingsSpendPage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("SettingsSpendPage create-rule UI", () => {
  beforeEach(() => {
    apiFetchMock.mockReset()
    apiFetchMock.mockImplementation((path: string) => defaultApiFetch(path))
  })

  afterEach(() => {
    cleanup()
  })

  it("shows an Add rule button that opens the create form", async () => {
    await act(async () => {
      renderPage()
    })

    const addBtn = await screen.findByTestId("add-rule-button")
    expect(addBtn).toBeTruthy()
    expect(screen.queryByTestId("create-rule-form")).toBeNull()

    await act(async () => {
      fireEvent.click(addBtn)
    })

    expect(screen.getByTestId("create-rule-form")).toBeTruthy()
  })

  it("POSTs an evaluator-shaped payload to /spend/rules and refreshes the list", async () => {
    await act(async () => {
      renderPage()
    })

    await act(async () => {
      fireEvent.click(await screen.findByTestId("add-rule-button"))
    })

    await act(async () => {
      fireEvent.change(screen.getByLabelText("Butler condition"), {
        target: { value: "general" },
      })
      fireEvent.change(screen.getByLabelText("Complexity condition"), {
        target: { value: "workhorse" },
      })
      fireEvent.change(screen.getByLabelText("Target model"), {
        target: { value: "claude-sonnet" },
      })
    })

    await act(async () => {
      fireEvent.submit(screen.getByTestId("create-rule-form"))
    })

    await waitFor(() => {
      const postCall = apiFetchMock.mock.calls.find(
        (c) => c[0] === "/spend/rules" && c[1]?.method === "POST",
      )
      expect(postCall).toBeTruthy()
      const body = JSON.parse(postCall![1].body as string)
      expect(body).toEqual({
        condition: { butler: "general", complexity: "workhorse" },
        action: { model: "claude-sonnet" },
      })
    })

    // List refresh — /spend/rules is re-fetched (GET) after the POST succeeds.
    await waitFor(() => {
      const getCalls = apiFetchMock.mock.calls.filter(
        (c) => c[0] === "/spend/rules" && c[1]?.method !== "POST",
      )
      expect(getCalls.length).toBeGreaterThan(1)
    })
  })

  it("produces a catch-all (empty condition) when no constraints are set", async () => {
    await act(async () => {
      renderPage()
    })

    await act(async () => {
      fireEvent.click(await screen.findByTestId("add-rule-button"))
    })

    await act(async () => {
      fireEvent.change(screen.getByLabelText("Target model"), {
        target: { value: "claude-haiku" },
      })
    })

    await act(async () => {
      fireEvent.submit(screen.getByTestId("create-rule-form"))
    })

    await waitFor(() => {
      const postCall = apiFetchMock.mock.calls.find(
        (c) => c[0] === "/spend/rules" && c[1]?.method === "POST",
      )
      expect(postCall).toBeTruthy()
      const body = JSON.parse(postCall![1].body as string)
      expect(body).toEqual({ condition: {}, action: { model: "claude-haiku" } })
    })
  })

  it("does not POST when no target model is chosen", async () => {
    await act(async () => {
      renderPage()
    })

    await act(async () => {
      fireEvent.click(await screen.findByTestId("add-rule-button"))
    })

    await act(async () => {
      fireEvent.submit(screen.getByTestId("create-rule-form"))
    })

    const postCall = apiFetchMock.mock.calls.find(
      (c) => c[0] === "/spend/rules" && c[1]?.method === "POST",
    )
    expect(postCall).toBeUndefined()
  })

  it("includes the trigger condition dim and max_cost_per_call effect in the payload", async () => {
    await act(async () => {
      renderPage()
    })

    await act(async () => {
      fireEvent.click(await screen.findByTestId("add-rule-button"))
    })

    await act(async () => {
      fireEvent.change(screen.getByLabelText("Trigger condition"), {
        target: { value: "healing" },
      })
      fireEvent.change(screen.getByLabelText("Target model"), {
        target: { value: "claude-haiku" },
      })
      fireEvent.change(screen.getByLabelText("Max cost per call"), {
        target: { value: "0.05" },
      })
    })

    await act(async () => {
      fireEvent.submit(screen.getByTestId("create-rule-form"))
    })

    await waitFor(() => {
      const postCall = apiFetchMock.mock.calls.find(
        (c) => c[0] === "/spend/rules" && c[1]?.method === "POST",
      )
      expect(postCall).toBeTruthy()
      const body = JSON.parse(postCall![1].body as string)
      expect(body).toEqual({
        condition: { trigger: "healing" },
        action: { model: "claude-haiku", max_cost_per_call: 0.05 },
      })
    })
  })

  it("allows a cap-only rule (no route-to model) when a per-call cap is set", async () => {
    await act(async () => {
      renderPage()
    })

    await act(async () => {
      fireEvent.click(await screen.findByTestId("add-rule-button"))
    })

    await act(async () => {
      fireEvent.change(screen.getByLabelText("Max cost per call"), {
        target: { value: "0.10" },
      })
    })

    await act(async () => {
      fireEvent.submit(screen.getByTestId("create-rule-form"))
    })

    await waitFor(() => {
      const postCall = apiFetchMock.mock.calls.find(
        (c) => c[0] === "/spend/rules" && c[1]?.method === "POST",
      )
      expect(postCall).toBeTruthy()
      const body = JSON.parse(postCall![1].body as string)
      expect(body).toEqual({ condition: {}, action: { max_cost_per_call: 0.1 } })
    })
  })

  it("does not POST when neither a model nor a cap is set", async () => {
    await act(async () => {
      renderPage()
    })

    await act(async () => {
      fireEvent.click(await screen.findByTestId("add-rule-button"))
    })

    await act(async () => {
      fireEvent.submit(screen.getByTestId("create-rule-form"))
    })

    const postCall = apiFetchMock.mock.calls.find(
      (c) => c[0] === "/spend/rules" && c[1]?.method === "POST",
    )
    expect(postCall).toBeUndefined()
  })
})
