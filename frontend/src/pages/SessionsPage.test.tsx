// @vitest-environment jsdom
//
// SessionsPage redesign contract:
// - URL round-trips filters + cursor (shareable, refresh-safe; state from URL).
// - Keyset Newer/Older controls with correct disabled states (no "Page X of N").
// - A failed cross-butler fetch renders the Page error region, NOT the empty state.

import { afterEach, describe, expect, it, vi, beforeEach } from "vitest";
import { render, fireEvent, cleanup } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router";

import type { KeysetResponse, SessionSummary } from "@/api/types";

const mockUseSessions = vi.fn();

vi.mock("@/hooks/use-sessions", () => ({
  useSessions: (...args: unknown[]) => mockUseSessions(...args),
}));
vi.mock("@/hooks/use-butlers", () => ({
  useButlers: () => ({ data: { data: [] } }),
}));
// Stub the data-heavy children so the page-logic tests stay focused.
vi.mock("@/components/dashboard/SessionStripeChart", () => ({
  SessionStripeChart: () => <div data-testid="stripe-stub" />,
}));
vi.mock("@/components/sessions/SessionsKpiStrip", () => ({
  SessionsKpiStrip: () => <div data-testid="kpi-stub" />,
}));
vi.mock("@/components/sessions/SessionDetailDrawer", () => ({
  SessionDetailDrawer: () => <div data-testid="drawer-stub" />,
}));

import SessionsPage from "@/pages/SessionsPage";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeSession(overrides: Partial<SessionSummary> = {}): SessionSummary {
  return {
    id: "sess-1",
    butler: "health",
    prompt: "Roll up stats",
    trigger_source: "cron",
    request_id: null,
    success: true,
    started_at: "2026-03-12T00:00:00Z",
    completed_at: "2026-03-12T00:00:02Z",
    duration_ms: 2000,
    input_tokens: 100,
    output_tokens: 200,
    model: null,
    complexity: null,
    ...overrides,
  };
}

function keysetResponse(
  data: SessionSummary[],
  hasMore: boolean,
  nextCursor: string | null,
): KeysetResponse<SessionSummary> {
  return { data, meta: { limit: 20, next_cursor: nextCursor, has_more: hasMore } };
}

function setSessions(result: {
  data?: KeysetResponse<SessionSummary>;
  isLoading?: boolean;
  isError?: boolean;
  error?: unknown;
}) {
  mockUseSessions.mockReturnValue({
    data: result.data,
    isLoading: result.isLoading ?? false,
    isError: result.isError ?? false,
    error: result.error ?? null,
    refetch: vi.fn(),
  });
}

/** Surfaces the current querystring so URL state can be asserted. */
function LocationProbe() {
  const loc = useLocation();
  return <div data-testid="location-search">{loc.search}</div>;
}

function renderPage(initialEntry = "/sessions") {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <SessionsPage />
      <LocationProbe />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(cleanup);

// ---------------------------------------------------------------------------
// URL state — read (initialise from URL)
// ---------------------------------------------------------------------------

describe("SessionsPage — URL state round-trip", () => {
  it("initialises the list query from the URL filters + cursor", () => {
    setSessions({ data: keysetResponse([makeSession()], false, null) });
    renderPage(
      "/sessions?butler=health&status=running&trigger=cron&request=req-1&since=2026-01-01&until=2026-02-01&cursor=abc",
    );

    const params = mockUseSessions.mock.calls.at(-1)?.[0];
    expect(params).toMatchObject({
      limit: 20,
      cursor: "abc",
      butler: "health",
      status: "running",
      trigger_source: "cron",
      request_id: "req-1",
      since: "2026-01-01",
      until: "2026-02-01",
    });
  });

  it("writes filter changes back into the querystring and clears the cursor", () => {
    setSessions({ data: keysetResponse([makeSession()], false, null) });
    const { getByTestId, getByLabelText } = renderPage("/sessions?cursor=abc");

    fireEvent.change(getByLabelText("Trigger"), { target: { value: "telegram" } });

    const search = getByTestId("location-search").textContent ?? "";
    expect(search).toContain("trigger=telegram");
    expect(search).not.toContain("cursor=abc");
  });
});

// ---------------------------------------------------------------------------
// Keyset pagination — Newer / Older
// ---------------------------------------------------------------------------

describe("SessionsPage — keyset pagination", () => {
  it("disables Newer on the first page and enables Older when more rows exist", () => {
    setSessions({ data: keysetResponse([makeSession()], true, "next-1") });
    const { getByTestId } = renderPage();

    expect((getByTestId("sessions-newer") as HTMLButtonElement).disabled).toBe(true);
    expect((getByTestId("sessions-older") as HTMLButtonElement).disabled).toBe(false);
  });

  it("Older advances the cursor in the URL and then Newer is enabled", () => {
    setSessions({ data: keysetResponse([makeSession()], true, "next-1") });
    const { getByTestId } = renderPage();

    fireEvent.click(getByTestId("sessions-older"));

    expect(getByTestId("location-search").textContent).toContain("cursor=next-1");
    expect((getByTestId("sessions-newer") as HTMLButtonElement).disabled).toBe(false);

    // Newer pops back to the first page (cursor removed).
    fireEvent.click(getByTestId("sessions-newer"));
    expect(getByTestId("location-search").textContent).not.toContain("cursor=next-1");
  });

  it("disables Older when there are no more rows", () => {
    setSessions({ data: keysetResponse([makeSession()], false, null) });
    const { getByTestId } = renderPage();
    expect((getByTestId("sessions-older") as HTMLButtonElement).disabled).toBe(true);
  });

  it("renders no 'Page X of N' counter", () => {
    setSessions({ data: keysetResponse([makeSession()], true, "next-1") });
    const { container } = renderPage();
    expect(container.textContent).not.toMatch(/Page \d+ of \d+/);
  });
});

// ---------------------------------------------------------------------------
// Error state
// ---------------------------------------------------------------------------

describe("SessionsPage — error state", () => {
  it("renders the Page error region (not the empty state) on a failed fetch", () => {
    setSessions({ isError: true, error: new Error("upstream unavailable"), data: undefined });
    const { container, queryByRole } = renderPage();

    expect(container.textContent).toContain("Something went wrong");
    expect(container.textContent).toContain("upstream unavailable");
    // The in-card empty state must NOT appear in the error branch.
    expect(container.textContent).not.toContain("No sessions found.");
    expect(queryByRole("alert")).not.toBeNull();
  });
});
