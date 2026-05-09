import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import SessionDetailPage from "@/pages/SessionDetailPage";
import { useSessionDetail } from "@/hooks/use-sessions";
import type { SessionDetail } from "@/api/types";

vi.mock("react-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router")>();
  return {
    ...actual,
    useParams: vi.fn(() => ({ id: "sess-abc123" })),
    useSearchParams: vi.fn(() => [new URLSearchParams("butler=general"), vi.fn()]),
  };
});

vi.mock("@/hooks/use-sessions", () => ({
  useSessionDetail: vi.fn(),
}));

// Stub complex child components to avoid deep dependency chains
vi.mock("@/components/sessions/ToolCallTimeline", () => ({
  CollapsibleJson: ({ label }: { label: string }) => (
    <div data-testid="collapsible-json">{label}</div>
  ),
  ToolCallTimeline: ({ toolCalls }: { toolCalls: unknown[] }) => (
    <div data-testid="tool-call-timeline">{toolCalls.length} tool calls</div>
  ),
}));

// Stub global getSession to prevent import errors (not invoked when butler is set)
vi.mock("@/api/index.ts", () => ({
  getSession: vi.fn(),
}));

type UseSessionDetailResult = ReturnType<typeof useSessionDetail>;

const BASE_SESSION: SessionDetail = {
  id: "sess-abc123",
  butler: "general",
  prompt: "What is the weather today?",
  trigger_source: "api",
  result: "It is sunny.",
  tool_calls: [],
  duration_ms: 1500,
  trace_id: "trace-001",
  request_id: "req-001",
  cost: null,
  started_at: "2025-03-01T10:00:00Z",
  completed_at: "2025-03-01T10:00:01Z",
  success: true,
  error: null,
  model: "claude-sonnet-4-6",
  input_tokens: 200,
  output_tokens: 50,
  parent_session_id: null,
};

function setSessionState(
  session: SessionDetail | null,
  opts: Partial<UseSessionDetailResult> = {},
) {
  vi.mocked(useSessionDetail).mockReturnValue({
    data: session ? { data: session } : undefined,
    isLoading: false,
    isError: false,
    error: null,
    ...opts,
  } as UseSessionDetailResult);
}

function renderPage(): string {
  const queryClient = new QueryClient();
  return renderToStaticMarkup(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <SessionDetailPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// ---------------------------------------------------------------------------
// Single-H1 contract — SessionDetailPage
// ---------------------------------------------------------------------------

describe("SessionDetailPage — single-H1 contract", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("renders exactly one H1 when session is loaded", () => {
    setSessionState(BASE_SESSION);
    const html = renderPage();
    expect(html.match(/<h1[^>]*>/g) ?? []).toHaveLength(1);
  });

  it("H1 contains 'Session Detail'", () => {
    setSessionState(BASE_SESSION);
    const html = renderPage();
    const h1 = html.match(/<h1[^>]*>(.*?)<\/h1>/s);
    expect(h1).not.toBeNull();
    expect(h1![1]).toContain("Session Detail");
  });

  it("renders zero H1s in loading state (skeleton, no heading)", () => {
    vi.mocked(useSessionDetail).mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      error: null,
    } as UseSessionDetailResult);
    const html = renderPage();
    expect(html.match(/<h1[^>]*>/g) ?? []).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// Content — SessionDetailPage
// ---------------------------------------------------------------------------

describe("SessionDetailPage — content", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("renders session ID in breadcrumbs (first 8 chars)", () => {
    setSessionState(BASE_SESSION);
    const html = renderPage();
    expect(html).toContain("sess-abc");
  });

  it("renders breadcrumbs link to /sessions", () => {
    setSessionState(BASE_SESSION);
    const html = renderPage();
    expect(html).toContain("/sessions");
  });

  it("renders trigger source badge", () => {
    setSessionState(BASE_SESSION);
    const html = renderPage();
    expect(html).toContain("api");
  });

  it("renders model when present", () => {
    setSessionState(BASE_SESSION);
    const html = renderPage();
    expect(html).toContain("claude-sonnet-4-6");
  });

  it("renders session prompt", () => {
    setSessionState(BASE_SESSION);
    const html = renderPage();
    expect(html).toContain("What is the weather today?");
  });

  it("renders session result when present", () => {
    setSessionState(BASE_SESSION);
    const html = renderPage();
    expect(html).toContain("It is sunny.");
  });

  it("renders success status badge for successful session", () => {
    setSessionState({ ...BASE_SESSION, success: true });
    const html = renderPage();
    expect(html).toContain("Success");
  });

  it("renders failed status badge for failed session", () => {
    setSessionState({ ...BASE_SESSION, success: false });
    const html = renderPage();
    expect(html).toContain("Failed");
  });

  it("renders running status badge when success is null", () => {
    setSessionState({ ...BASE_SESSION, success: null });
    const html = renderPage();
    expect(html).toContain("Running");
  });

  it("renders error card when session has an error", () => {
    setSessionState({ ...BASE_SESSION, error: "Something went wrong during execution" });
    const html = renderPage();
    expect(html).toContain("Something went wrong during execution");
  });

  it("does not render result card when result is null", () => {
    setSessionState({ ...BASE_SESSION, result: null });
    const html = renderPage();
    expect(html).not.toContain("It is sunny.");
  });

  it("renders butler link when butler name is provided via search params", () => {
    setSessionState(BASE_SESSION);
    const html = renderPage();
    // Butler name appears as a link in the Metadata card
    expect(html).toContain("/butlers/general");
  });

  it("renders token counts when present", () => {
    setSessionState(BASE_SESSION);
    const html = renderPage();
    // input_tokens=200, output_tokens=50 → "200 / 50" after formatting
    expect(html).toContain("200");
    expect(html).toContain("50");
    expect(html).toContain("Tokens (in/out)");
  });

  it("renders the ToolCallTimeline component", () => {
    setSessionState(BASE_SESSION);
    const html = renderPage();
    expect(html).toContain("tool-call-timeline");
  });
});

// ---------------------------------------------------------------------------
// Error / empty states
// ---------------------------------------------------------------------------

describe("SessionDetailPage — async states", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("shows error region when fetch fails", () => {
    vi.mocked(useSessionDetail).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("Not found"),
    } as UseSessionDetailResult);
    const html = renderPage();
    expect(html).toContain("Failed to load session details");
  });

  it("shows error region when session data is absent and not loading", () => {
    // No data, not loading, not error → falls into the !session branch
    vi.mocked(useSessionDetail).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: false,
      error: null,
    } as UseSessionDetailResult);
    const html = renderPage();
    expect(html).toContain("Failed to load session details");
  });
});

// ---------------------------------------------------------------------------
// Slot composition baseline — for Gate-A change tracking
// ---------------------------------------------------------------------------

describe("SessionDetailPage — slot composition baseline", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  // Breadcrumbs: Page does NOT own breadcrumbs (page uses raw <Breadcrumbs> directly)
  it("breadcrumbs are rendered directly by the page (not via Page archetype)", () => {
    setSessionState(BASE_SESSION);
    const html = renderPage();
    // No <Page archetype="detail"> — breadcrumbs component is rendered inline
    expect(html).toContain('aria-label="Breadcrumb"');
    // No max-w-5xl constraint (not using Page archetype=detail)
    expect(html).not.toContain("max-w-5xl");
  });

  // No Tier-2 hero (no PulseStrip, no DetailPage shell)
  it("does not render a Tier-2 hero or PulseStrip today (pre-redesign baseline)", () => {
    setSessionState(BASE_SESSION);
    const html = renderPage();
    // DetailPage/Page shell with archetype=detail would include max-w-5xl
    expect(html).not.toContain("max-w-5xl");
    // No pulse-strip or dunbar-tier metrics
    expect(html).not.toContain("Dunbar tier");
  });

  // Title slot: raw <h1> in the page body
  it("title is rendered as a raw h1 (not via Page HeadingBlock)", () => {
    setSessionState(BASE_SESSION);
    const html = renderPage();
    // Raw h1 class from the page body
    expect(html).toMatch(/<h1[^>]*>Session Detail<\/h1>/);
  });

  // Primary slot: Metadata and Tool Calls cards present
  it("primary content includes Metadata and Tool Calls sections", () => {
    setSessionState(BASE_SESSION);
    const html = renderPage();
    expect(html).toContain("Metadata");
    expect(html).toContain("Tool Calls");
  });
});
