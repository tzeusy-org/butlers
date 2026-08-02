// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

import type { SessionSummary } from "@/api/types";

const mockGetSession = vi.fn();

vi.mock("@/api/index.ts", () => ({
  getSession: (...args: unknown[]) => mockGetSession(...args),
}));

import { useSessionErrorExcerpts } from "./use-sessions";

const session: SessionSummary = {
  id: "failed-session",
  butler: "health",
  prompt: "Run health summary",
  trigger_source: "schedule:health_summary",
  request_id: null,
  success: false,
  started_at: "2026-07-28T00:00:00Z",
  completed_at: "2026-07-28T00:01:00Z",
  duration_ms: 60_000,
  input_tokens: null,
  output_tokens: null,
  cancelled_by_owner: false,
  model: null,
  complexity: null,
};

function wrapper({ children }: { children: ReactNode }) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("useSessionErrorExcerpts", () => {
  it("distinguishes a pending detail query from a loaded null error", () => {
    mockGetSession.mockReturnValue(new Promise(() => undefined));

    const { result } = renderHook(() => useSessionErrorExcerpts([session]), { wrapper });

    expect(result.current.get(session.id)).toMatchObject({ kind: "loading" });
  });

  it("exposes a retryable unavailable state for one failed detail query", async () => {
    mockGetSession.mockRejectedValue(new Error("session detail unavailable"));

    const { result } = renderHook(() => useSessionErrorExcerpts([session]), { wrapper });

    await waitFor(() => expect(result.current.get(session.id)).toMatchObject({ kind: "error" }));
    expect(result.current.get(session.id)).toHaveProperty("retry");
  });

  it("marks a successful null error detail as loaded", async () => {
    mockGetSession.mockResolvedValue({ data: { error: null } });

    const { result } = renderHook(() => useSessionErrorExcerpts([session]), { wrapper });

    await waitFor(() =>
      expect(result.current.get(session.id)).toMatchObject({ kind: "loaded", error: null }),
    );
  });
});
