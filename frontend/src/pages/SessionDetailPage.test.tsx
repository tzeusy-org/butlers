// @vitest-environment jsdom

import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import SessionDetailPage from "@/pages/SessionDetailPage";
import { useGlobalSessionDetail } from "@/hooks/use-sessions";
import type { SessionDetail } from "@/api/types";

vi.mock("react-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router")>();
  return {
    ...actual,
    useParams: vi.fn(() => ({ id: "sess-abc123" })),
  };
});

vi.mock("@/hooks/use-sessions", () => ({
  useGlobalSessionDetail: vi.fn(),
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

type UseGlobalSessionDetailResult = ReturnType<typeof useGlobalSessionDetail>;

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
  complexity: null,
  resolution_source: null,
  process_log: null,
};

function setSessionState(
  session: SessionDetail | null,
  opts: Partial<UseGlobalSessionDetailResult> = {},
) {
  vi.mocked(useGlobalSessionDetail).mockReturnValue({
    data: session ? { data: session } : undefined,
    isLoading: false,
    isError: false,
    error: null,
    ...opts,
  } as UseGlobalSessionDetailResult);
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
    setSessionState(null, { isLoading: true });
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

  it("fetches via the global useGlobalSessionDetail hook keyed on the route id", () => {
    setSessionState(BASE_SESSION);
    renderPage();
    expect(useGlobalSessionDetail).toHaveBeenCalledWith("sess-abc123");
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

  it("renders trigger source", () => {
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

  // The ?butler= dual-fetch path is gone: the butler link is derived from
  // session.butler on the fetched SessionDetail, not a query-string param.
  it("renders butler link derived from session.butler, with no ?butler= param in the URL", () => {
    setSessionState(BASE_SESSION);
    const html = renderPage();
    expect(html).toContain("/butlers/general");
  });

  it("renders token counts when present", () => {
    setSessionState(BASE_SESSION);
    const html = renderPage();
    expect(html).toContain("200");
    expect(html).toContain("50");
    expect(html).toContain("Input Tokens");
    expect(html).toContain("Output Tokens");
  });

  it("renders the ToolCallTimeline component", () => {
    setSessionState(BASE_SESSION);
    const html = renderPage();
    expect(html).toContain("tool-call-timeline");
  });

  it("renders trace_id, request_id, and parent_session_id links (previously omitted, types.ts:221-268)", () => {
    setSessionState({ ...BASE_SESSION, parent_session_id: "sess-parent-1" });
    const html = renderPage();
    expect(html).toContain(`href="/timeline?trace=trace-001"`);
    expect(html).toContain(`href="/sessions?request=req-001"`);
    expect(html).toContain(`href="/sessions/sess-parent-1"`);
  });

  it("renders process_log stderr/exit_code as root evidence for a failed session", () => {
    setSessionState({
      ...BASE_SESSION,
      success: false,
      error: "boom",
      process_log: {
        pid: 1,
        exit_code: 137,
        command: null,
        stderr: "OOMKilled",
        runtime_type: "claude_code",
        created_at: null,
        expires_at: null,
      },
    });
    const html = renderPage();
    expect(html).toContain("Root Evidence");
    expect(html).toContain("137");
  });
});

// ---------------------------------------------------------------------------
// Error / empty / no-id states
// ---------------------------------------------------------------------------

describe("SessionDetailPage — async states", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("shows the Page error region when fetch fails", () => {
    setSessionState(null, { isError: true, error: new Error("Not found") });
    const html = renderPage();
    expect(html).toContain("Something went wrong");
    expect(html).toContain("Not found");
  });

  it("shows an error region when session data is absent and not loading/erroring (session not found)", () => {
    setSessionState(null);
    const html = renderPage();
    expect(html).toContain("Something went wrong");
  });
});
