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
const mockUseSessionAggregate = vi.fn();

vi.mock("@/hooks/use-sessions", () => ({
  useSessions: (...args: unknown[]) => mockUseSessions(...args),
  useSessionAggregate: (...args: unknown[]) => mockUseSessionAggregate(...args),
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
  SessionDetailDrawer: ({
    sessionId,
    butler,
    onClose,
  }: {
    sessionId: string | null;
    butler: string;
    onClose: () => void;
  }) => (
    <div data-testid="drawer-stub" data-session-id={sessionId ?? ""} data-butler={butler}>
      <button type="button" data-testid="drawer-stub-close" onClick={onClose}>
        Close
      </button>
    </div>
  ),
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
  // SessionsVerdictOpener's data — not the focus of these page-mechanics
  // tests, so stub a safe default (all-clear, not loading/erroring).
  mockUseSessionAggregate.mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: false,
  });
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

    // The page's own list query is always the FIRST useSessions call — the
    // verdict opener's "nearest running session" query (status=running,
    // limit=1) is a separate, later call (see SessionsVerdictOpener wiring).
    const params = mockUseSessions.mock.calls[0]?.[0];
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

// ---------------------------------------------------------------------------
// ?selected= URL mirroring (bu-qvnce.5, pursuit move 5 slice 4)
// ---------------------------------------------------------------------------

describe("SessionsPage — ?selected= URL mirroring", () => {
  it("clicking a row writes ?selected=<id> and passes it + the row's butler to the drawer", () => {
    setSessions({
      data: keysetResponse(
        [makeSession({ id: "sess-1", butler: "health" }), makeSession({ id: "sess-2", butler: "spend" })],
        false,
        null,
      ),
    });
    const { getByTestId, getAllByTestId } = renderPage();

    fireEvent.click(getAllByTestId("session-row")[1]);

    expect(getByTestId("location-search").textContent).toContain("selected=sess-2");
    expect(getByTestId("drawer-stub").getAttribute("data-session-id")).toBe("sess-2");
    expect(getByTestId("drawer-stub").getAttribute("data-butler")).toBe("spend");
  });

  it("initializes selection from ?selected= on the URL (shareable/reloadable)", () => {
    setSessions({
      data: keysetResponse([makeSession({ id: "sess-1", butler: "health" })], false, null),
    });
    const { getByTestId } = renderPage("/sessions?selected=sess-1");

    expect(getByTestId("drawer-stub").getAttribute("data-session-id")).toBe("sess-1");
  });

  it("closing the drawer clears ?selected= from the URL", () => {
    setSessions({
      data: keysetResponse([makeSession({ id: "sess-1" })], false, null),
    });
    const { getByTestId } = renderPage("/sessions?selected=sess-1");

    expect(getByTestId("location-search").textContent).toContain("selected=sess-1");
    fireEvent.click(getByTestId("drawer-stub-close"));
    expect(getByTestId("location-search").textContent).not.toContain("selected=sess-1");
  });
});

// ---------------------------------------------------------------------------
// j/k/[/]/y keyboard loop (bu-qvnce.5, pursuit move 5 slice 4)
// ---------------------------------------------------------------------------

describe("SessionsPage — j/k/[/]/y keyboard loop", () => {
  it("j selects the first row, then advances through subsequent rows", () => {
    setSessions({
      data: keysetResponse(
        [makeSession({ id: "sess-1" }), makeSession({ id: "sess-2" }), makeSession({ id: "sess-3" })],
        false,
        null,
      ),
    });
    const { getByTestId } = renderPage();

    fireEvent.keyDown(window, { key: "j" });
    expect(getByTestId("location-search").textContent).toContain("selected=sess-1");

    fireEvent.keyDown(window, { key: "j" });
    expect(getByTestId("location-search").textContent).toContain("selected=sess-2");
  });

  it("k moves selection back to the previous row", () => {
    setSessions({
      data: keysetResponse(
        [makeSession({ id: "sess-1" }), makeSession({ id: "sess-2" })],
        false,
        null,
      ),
    });
    const { getByTestId } = renderPage("/sessions?selected=sess-2");

    fireEvent.keyDown(window, { key: "k" });
    expect(getByTestId("location-search").textContent).toContain("selected=sess-1");
  });

  it("[ steps Older and ] steps back Newer", () => {
    setSessions({ data: keysetResponse([makeSession()], true, "next-1") });
    const { getByTestId } = renderPage();

    fireEvent.keyDown(window, { key: "[" });
    expect(getByTestId("location-search").textContent).toContain("cursor=next-1");

    fireEvent.keyDown(window, { key: "]" });
    expect(getByTestId("location-search").textContent).not.toContain("cursor=next-1");
  });

  it("y copies the selected session's id to the clipboard", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });

    setSessions({
      data: keysetResponse([makeSession({ id: "sess-1" })], false, null),
    });
    renderPage("/sessions?selected=sess-1");

    fireEvent.keyDown(window, { key: "y" });

    expect(writeText).toHaveBeenCalledWith("sess-1");
  });

  it("registers no y binding when nothing is selected", () => {
    setSessions({ data: keysetResponse([makeSession({ id: "sess-1" })], false, null) });
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });

    renderPage();
    fireEvent.keyDown(window, { key: "y" });

    expect(writeText).not.toHaveBeenCalled();
  });
});
