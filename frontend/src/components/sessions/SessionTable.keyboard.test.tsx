// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import { render, fireEvent, cleanup } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { act } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { axe, toHaveNoViolations } from "jest-axe";

import type { SessionSummary } from "@/api/types";
import { PREFETCH_INTENT_DELAY_MS } from "@/hooks/use-prefetch-on-intent";
import { SessionTable } from "@/components/sessions/SessionTable";

vi.mock("@/api/index.ts", () => ({
  getApprovalDetail: vi.fn(),
  getIngestionEvent: vi.fn(),
  getSession: vi.fn(() => Promise.resolve({ data: {} })),
  getTimeline: vi.fn(),
}));

import { getSession } from "@/api/index.ts";

expect.extend(toHaveNoViolations);

afterEach(cleanup);

function makeSession(overrides: Partial<SessionSummary> = {}): SessionSummary {
  return {
    id: "sess-abc123",
    butler: "health",
    prompt: "Summarize today's routing failures",
    trigger_source: "telegram",
    request_id: "req-12345678-1234-1234-1234-123456789abc",
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

describe("SessionTable keyboard accessibility", () => {
  it("uses separate native controls for session detail and request-ID filtering", async () => {
    const session = makeSession();
    const { container, getByRole } = render(
      <SessionTable
        sessions={[session]}
        isLoading={false}
        onSessionClick={vi.fn()}
        onRequestIdClick={vi.fn()}
      />,
    );
    const row = container.querySelector<HTMLTableRowElement>('[data-testid="session-row"]');
    const detailButton = getByRole("button", {
      name: "Open session detail for health: Summarize today's routing failures",
    });
    const requestIdButton = getByRole("button", {
      name: `Filter sessions by request ID ${session.request_id}`,
    });

    expect(row).not.toBeNull();
    expect(row?.getAttribute("role")).toBeNull();
    expect(row?.getAttribute("tabindex")).toBeNull();
    expect(detailButton.tagName).toBe("BUTTON");
    expect(requestIdButton.tagName).toBe("BUTTON");
    expect(detailButton.closest("tr")).toBe(row);
    expect(requestIdButton.closest("tr")).toBe(row);
    expect(detailButton.contains(requestIdButton)).toBe(false);
    expect(
      await axe(container, { rules: { "color-contrast": { enabled: false } } }),
    ).toHaveNoViolations();
  });

  it("opens the drawer through its native detail button with Enter and Space", async () => {
    const onSessionClick = vi.fn();
    const { getByRole } = render(
      <SessionTable sessions={[makeSession()]} isLoading={false} onSessionClick={onSessionClick} />,
    );
    const detailButton = getByRole("button", { name: /Open session detail for health/i });
    const user = userEvent.setup();

    detailButton.focus();
    await user.keyboard("{Enter}");
    expect(onSessionClick).toHaveBeenCalledTimes(1);

    detailButton.focus();
    await user.keyboard(" ");
    expect(onSessionClick).toHaveBeenCalledTimes(2);
  });

  it("filters by request ID without also opening the session detail", () => {
    const onSessionClick = vi.fn();
    const onRequestIdClick = vi.fn();
    const session = makeSession();
    const { getByRole } = render(
      <SessionTable
        sessions={[session]}
        isLoading={false}
        onSessionClick={onSessionClick}
        onRequestIdClick={onRequestIdClick}
      />,
    );

    fireEvent.click(
      getByRole("button", { name: `Filter sessions by request ID ${session.request_id}` }),
    );

    expect(onRequestIdClick).toHaveBeenCalledWith(session.request_id);
    expect(onSessionClick).not.toHaveBeenCalled();
  });

  it("keeps a request ID static when filtering is unavailable, so its cell opens the detail", () => {
    const onSessionClick = vi.fn();
    const session = makeSession();
    const { getByText, queryByRole } = render(
      <SessionTable sessions={[session]} isLoading={false} onSessionClick={onSessionClick} />,
    );

    expect(queryByRole("button", { name: /Filter sessions by request ID/i })).toBeNull();
    fireEvent.click(getByText("req-1234"));
    expect(onSessionClick).toHaveBeenCalledWith(session);
  });

  it("does not make rows interactive when no click handler is supplied", () => {
    const { queryByRole } = render(
      <SessionTable sessions={[makeSession()]} isLoading={false} />,
    );
    expect(queryByRole("button", { name: /Open session detail/i })).toBeNull();
  });

  it("prefetches the matching global detail query after deliberate row hover", () => {
    vi.useFakeTimers();
    const queryClient = new QueryClient();
    const onSessionClick = vi.fn();
    const { getByTestId, unmount } = render(
      <QueryClientProvider client={queryClient}>
        <SessionTable sessions={[makeSession()]} isLoading={false} onSessionClick={onSessionClick} />
      </QueryClientProvider>,
    );

    fireEvent.pointerEnter(getByTestId("session-row"));
    act(() => {
      vi.advanceTimersByTime(PREFETCH_INTENT_DELAY_MS);
    });

    expect(getSession).toHaveBeenCalledWith("sess-abc123");
    unmount();
    vi.useRealTimers();
  });
});
