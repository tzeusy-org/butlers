// @vitest-environment jsdom
/**
 * ButlerSessionsTab — RTL tests.
 *
 * Tests cover:
 *  - Root container renders with correct testid
 *  - Uses Panel-grid/Panel atoms (no Card wrappers)
 *  - SessionTable is rendered inside the panel
 *  - Loading state: isLoading passed through to SessionTable
 *  - Empty state: no pagination rendered when total = 0
 *  - Pagination renders when total > 0
 *  - Pagination prev/next button disabled states
 *  - No pid field, no em-dash, no hex/oklch literals in markup
 *
 * bead: bu-j7b5n (follow-up from epic bu-hdavr)
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";

import ButlerSessionsTab from "./ButlerSessionsTab";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-sessions", () => ({
  useButlerSessions: vi.fn(),
  useSessionDetail: vi.fn(() => ({ data: null, isLoading: false })),
}));

// Stub SessionTable to keep test output controllable
vi.mock("@/components/sessions/SessionTable", () => ({
  SessionTable: ({
    sessions,
    isLoading,
  }: {
    sessions: unknown[];
    isLoading: boolean;
    showButlerColumn?: boolean;
    onSessionClick?: (s: unknown) => void;
  }) => (
    <div
      data-testid="session-table-stub"
      data-loading={String(isLoading)}
      data-count={String(sessions.length)}
    />
  ),
}));

// Stub SessionDetailDrawer so it doesn't add noise
vi.mock("@/components/sessions/SessionDetailDrawer", () => ({
  SessionDetailDrawer: () => null,
}));

import { useButlerSessions } from "@/hooks/use-sessions";

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const SESSION_1 = {
  id: "sess-00000001-aaaa-bbbb-cccc-ddddeeeeff01",
  butler: "general",
  prompt: "What is the weather?",
  trigger_source: "schedule",
  success: true,
  started_at: "2026-05-13T10:00:00.000Z",
  completed_at: "2026-05-13T10:00:05.000Z",
  duration_ms: 5000,
  input_tokens: 100,
  output_tokens: 50,
};

const SESSION_2 = {
  id: "sess-00000002-aaaa-bbbb-cccc-ddddeeeeff02",
  butler: "general",
  prompt: "Summarize the news.",
  trigger_source: "manual",
  success: false,
  started_at: "2026-05-13T11:00:00.000Z",
  completed_at: "2026-05-13T11:00:10.000Z",
  duration_ms: 10000,
  input_tokens: 200,
  output_tokens: 80,
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function renderTab(butlerName = "general") {
  return render(
    <MemoryRouter>
      <QueryClientProvider client={makeQueryClient()}>
        <ButlerSessionsTab butlerName={butlerName} />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

function renderTabStatic(butlerName = "general") {
  const queryClient = makeQueryClient();
  return renderToStaticMarkup(
    <MemoryRouter>
      <QueryClientProvider client={queryClient}>
        <ButlerSessionsTab butlerName={butlerName} />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

// ---------------------------------------------------------------------------
// Default mock setups
// ---------------------------------------------------------------------------

function setupWithSessions() {
  vi.mocked(useButlerSessions).mockReturnValue({
    data: {
      data: [SESSION_1, SESSION_2],
      meta: { total: 2, offset: 0, limit: 20, has_more: false },
    },
    isLoading: false,
  } as unknown as ReturnType<typeof useButlerSessions>);
}

function setupEmpty() {
  vi.mocked(useButlerSessions).mockReturnValue({
    data: { data: [], meta: { total: 0, offset: 0, limit: 20, has_more: false } },
    isLoading: false,
  } as unknown as ReturnType<typeof useButlerSessions>);
}

function setupLoading() {
  vi.mocked(useButlerSessions).mockReturnValue({
    data: undefined,
    isLoading: true,
  } as unknown as ReturnType<typeof useButlerSessions>);
}

function setupWithPagination() {
  vi.mocked(useButlerSessions).mockReturnValue({
    data: {
      data: [SESSION_1],
      meta: { total: 45, offset: 0, limit: 20, has_more: true },
    },
    isLoading: false,
  } as unknown as ReturnType<typeof useButlerSessions>);
}

// ---------------------------------------------------------------------------
// Cleanup
// ---------------------------------------------------------------------------

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  cleanup();
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("ButlerSessionsTab — root container", () => {
  it("renders the root container with correct testid", () => {
    setupWithSessions();
    renderTab();
    expect(screen.getByTestId("butler-sessions-tab")).toBeDefined();
  });

  it("renders NO legacy Card wrappers (no data-slot=card)", () => {
    setupWithSessions();
    const html = renderTabStatic();
    expect(html).not.toContain('data-slot="card"');
    expect(html).not.toContain("CardHeader");
    expect(html).not.toContain("CardContent");
  });

  it("renders the panel-grid outer frame", () => {
    setupWithSessions();
    const html = renderTabStatic();
    // ButlerPanelGrid uses grid grid-cols-1 lg:grid-cols-4 border-t border-l
    expect(html).toContain("border-t");
    expect(html).toContain("border-l");
    expect(html).toContain("grid-cols-1");
  });

  it("renders the sessions Panel with correct testid", () => {
    setupWithSessions();
    const html = renderTabStatic();
    expect(html).toContain('data-testid="panel-sessions"');
  });
});

describe("ButlerSessionsTab — SessionTable integration", () => {
  it("renders SessionTable stub inside the panel", () => {
    setupWithSessions();
    renderTab();
    expect(screen.getByTestId("session-table-stub")).toBeDefined();
  });

  it("passes isLoading=true to SessionTable when loading", () => {
    setupLoading();
    renderTab();
    const stub = screen.getByTestId("session-table-stub");
    expect(stub.getAttribute("data-loading")).toBe("true");
  });

  it("passes isLoading=false to SessionTable when data is ready", () => {
    setupWithSessions();
    renderTab();
    const stub = screen.getByTestId("session-table-stub");
    expect(stub.getAttribute("data-loading")).toBe("false");
  });

  it("passes correct session count to SessionTable", () => {
    setupWithSessions();
    renderTab();
    const stub = screen.getByTestId("session-table-stub");
    expect(stub.getAttribute("data-count")).toBe("2");
  });
});

describe("ButlerSessionsTab — pagination", () => {
  it("renders NO pagination when total = 0", () => {
    setupEmpty();
    renderTab();
    expect(screen.queryByTestId("sessions-pagination")).toBeNull();
  });

  it("renders pagination when total > 0", () => {
    setupWithPagination();
    renderTab();
    expect(screen.getByTestId("sessions-pagination")).toBeDefined();
  });

  it("Previous button is disabled on page 1", () => {
    setupWithPagination();
    renderTab();
    const prevBtn = screen.getByTestId("sessions-prev");
    expect((prevBtn as HTMLButtonElement).disabled).toBe(true);
  });

  it("Next button is enabled when has_more=true", () => {
    setupWithPagination();
    renderTab();
    const nextBtn = screen.getByTestId("sessions-next");
    expect((nextBtn as HTMLButtonElement).disabled).toBe(false);
  });

  it("shows page count text", () => {
    setupWithPagination();
    renderTab();
    // total=45, PAGE_SIZE=20 => 3 pages
    expect(screen.getByText(/Page 1 of 3/)).toBeDefined();
  });
});

describe("ButlerSessionsTab — doctrine gates", () => {
  it("does NOT render a pid field", () => {
    setupWithSessions();
    const html = renderTabStatic();
    // pid must not appear as a field label or key in the output
    expect(html).not.toMatch(/\bpid\b/);
  });

  it("does NOT contain raw hex color literals", () => {
    setupWithSessions();
    const html = renderTabStatic();
    expect(html).not.toMatch(/#[0-9a-fA-F]{3,6}\b/);
  });

  it("does NOT contain raw oklch/rgb color literals", () => {
    setupWithSessions();
    const html = renderTabStatic();
    expect(html).not.toMatch(/oklch\s*\(|rgb\s*\(/);
  });

  it("does NOT contain user-visible em-dashes", () => {
    setupWithSessions();
    const html = renderTabStatic();
    expect(html).not.toContain("&mdash;");
    expect(html).not.toContain("—");
  });
});
