// @vitest-environment jsdom
/**
 * ButlerDomainEventsPanel -- RTL tests (bu-317s5).
 *
 * Tests:
 *  - Renders the panel container
 *  - Empty state copy for both lists when no rows
 *  - Loading state does not render empty-state copy
 *  - Error state renders a distinct degraded note per list (never a
 *    fabricated empty list)
 *  - Subscriptions/deliveries lists filter by subscriber_butler
 *  - Delivery status renders a visually distinct tone badge
 */

import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, cleanup } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"

import type { SubscriptionEntry, DeliveryEntry } from "@/api/types"
import { ButlerDomainEventsPanel } from "./ButlerDomainEventsPanel"

vi.mock("@/hooks/use-domain-events", () => ({
  useDomainEventSubscriptions: vi.fn(),
  useDomainEventDeliveries: vi.fn(),
}))

vi.mock("@/components/ui/time", () => ({
  Time: ({ value }: { value: string }) => <time dateTime={value}>{value}</time>,
}))

import { useDomainEventSubscriptions, useDomainEventDeliveries } from "@/hooks/use-domain-events"

const mockUseSubscriptions = useDomainEventSubscriptions as unknown as ReturnType<typeof vi.fn>
const mockUseDeliveries = useDomainEventDeliveries as unknown as ReturnType<typeof vi.fn>

function makeSubscription(overrides: Partial<SubscriptionEntry> = {}): SubscriptionEntry {
  return {
    id: "11111111-1111-1111-1111-111111111111",
    subscriber_butler: "finance",
    event_type: "travel.trip_booked",
    active: true,
    created_at: "2026-07-25T10:00:00Z",
    updated_at: "2026-07-25T10:00:00Z",
    ...overrides,
  }
}

function makeDelivery(overrides: Partial<DeliveryEntry> = {}): DeliveryEntry {
  return {
    id: "22222222-2222-2222-2222-222222222222",
    event_id: "33333333-3333-3333-3333-333333333333",
    subscriber_butler: "finance",
    status: "delivered",
    task_id: null,
    task_name: null,
    error_message: null,
    delivered_at: "2026-07-25T10:05:00Z",
    created_at: "2026-07-25T10:00:00Z",
    updated_at: "2026-07-25T10:05:00Z",
    event_type: "travel.trip_booked",
    source_butler: "travel",
    occurred_at: "2026-07-25T10:00:00Z",
    ...overrides,
  }
}

function renderPanel(butlerName = "finance") {
  const queryClient = new QueryClient()
  return render(
    <QueryClientProvider client={queryClient}>
      <ButlerDomainEventsPanel butlerName={butlerName} />
    </QueryClientProvider>,
  )
}

function stubQueries({
  subscriptions = { data: undefined, isLoading: false, isError: false },
  deliveries = { data: undefined, isLoading: false, isError: false },
}: {
  subscriptions?: { data: unknown; isLoading: boolean; isError: boolean }
  deliveries?: { data: unknown; isLoading: boolean; isError: boolean }
} = {}) {
  mockUseSubscriptions.mockReturnValue(subscriptions)
  mockUseDeliveries.mockReturnValue(deliveries)
}

afterEach(() => {
  cleanup()
  mockUseSubscriptions.mockReset()
  mockUseDeliveries.mockReset()
})

describe("ButlerDomainEventsPanel", () => {
  it("renders the panel container", () => {
    stubQueries()
    renderPanel()
    expect(screen.getByTestId("panel-domain-events")).toBeDefined()
  })

  it("renders empty-state copy for both lists when no rows", () => {
    stubQueries()
    renderPanel()
    expect(screen.getByText("no standing subscriptions")).toBeDefined()
    expect(screen.getByText("no recent deliveries")).toBeDefined()
  })

  it("does not render empty-state copy while loading", () => {
    stubQueries({
      subscriptions: { data: undefined, isLoading: true, isError: false },
      deliveries: { data: undefined, isLoading: true, isError: false },
    })
    renderPanel()
    expect(screen.queryByText("no standing subscriptions")).toBeNull()
    expect(screen.queryByText("no recent deliveries")).toBeNull()
  })

  it("renders a distinct degraded note when subscriptions fail, without touching deliveries", () => {
    stubQueries({
      subscriptions: { data: undefined, isLoading: false, isError: true },
      deliveries: { data: { data: [] }, isLoading: false, isError: false },
    })
    renderPanel()
    expect(screen.getByTestId("subscriptions-error")).toBeDefined()
    expect(screen.getByText("no recent deliveries")).toBeDefined()
  })

  it("renders a distinct degraded note when deliveries fail, without touching subscriptions", () => {
    stubQueries({
      subscriptions: { data: { data: [] }, isLoading: false, isError: false },
      deliveries: { data: undefined, isLoading: false, isError: true },
    })
    renderPanel()
    expect(screen.getByTestId("deliveries-error")).toBeDefined()
    expect(screen.getByText("no standing subscriptions")).toBeDefined()
  })

  it("renders subscription rows filtered by subscriber_butler", () => {
    const entry = makeSubscription({ event_type: "travel.trip_active" })
    stubQueries({ subscriptions: { data: { data: [entry] }, isLoading: false, isError: false } })
    renderPanel("health")
    expect(screen.getByText("travel.trip_active")).toBeDefined()
    expect(mockUseSubscriptions).toHaveBeenCalledWith(
      expect.objectContaining({ subscriber_butler: "health" }),
    )
  })

  it("renders delivery rows filtered by subscriber_butler", () => {
    const entry = makeDelivery({ event_type: "travel.trip_active", source_butler: "travel" })
    stubQueries({ deliveries: { data: { data: [entry] }, isLoading: false, isError: false } })
    renderPanel("health")
    expect(screen.getByText("travel.trip_active")).toBeDefined()
    expect(mockUseDeliveries).toHaveBeenCalledWith(
      expect.objectContaining({ subscriber_butler: "health" }),
    )
  })

  it("renders a distinct tone badge for a failed delivery", () => {
    const entry = makeDelivery({ status: "failed" })
    stubQueries({ deliveries: { data: { data: [entry] }, isLoading: false, isError: false } })
    renderPanel()
    const badge = screen.getByTestId("delivery-status-badge")
    expect(badge.textContent).toContain("failed")
  })

  it("renders inactive subscriptions with a distinct label", () => {
    const entry = makeSubscription({ active: false })
    stubQueries({
      subscriptions: { data: { data: [entry] }, isLoading: false, isError: false },
    })
    renderPanel()
    expect(screen.getByText("inactive")).toBeDefined()
  })
})
