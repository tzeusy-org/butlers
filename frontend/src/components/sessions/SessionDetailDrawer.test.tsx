// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react-dom/test-utils";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";

import { SessionDetailDrawer } from "@/components/sessions/SessionDetailDrawer";
import { useSessionDetail } from "@/hooks/use-sessions";

vi.mock("@/hooks/use-sessions", () => ({
  useSessionDetail: vi.fn(),
}));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

type UseSessionDetailResult = ReturnType<typeof useSessionDetail>;

const SESSION_DETAIL = {
  id: "sess-123",
  butler: "switchboard",
  prompt: "Summarize today's routing failures",
  trigger_source: "telegram",
  result: "Done",
  tool_calls: [],
  duration_ms: 1530,
  trace_id: "trace-xyz",
  cost: { usd: 0.01 },
  started_at: "2026-02-13T00:00:00Z",
  completed_at: "2026-02-13T00:00:02Z",
  success: true,
  error: null,
  model: "claude-3-5-sonnet",
  input_tokens: 100,
  output_tokens: 200,
  parent_session_id: null,
};

function setQueryState(state: Partial<UseSessionDetailResult>) {
  vi.mocked(useSessionDetail).mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: false,
    error: null,
    ...state,
  } as UseSessionDetailResult);
}

function findCloseButton(container: HTMLElement): HTMLButtonElement | undefined {
  return Array.from(container.querySelectorAll("button")).find((button) =>
    button.textContent?.includes("Close"),
  );
}

describe("SessionDetailDrawer", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.resetAllMocks();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
    vi.restoreAllMocks();
  });

  it("renders overlay on open, keeps close behavior, and emits no ref warning", () => {
    setQueryState({
      data: {
        data: SESSION_DETAIL,
        meta: {},
      },
    });
    const onClose = vi.fn();
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});

    act(() => {
      root.render(
        <MemoryRouter>
          <SessionDetailDrawer butler="switchboard" sessionId="sess-123" onClose={onClose} />
        </MemoryRouter>,
      );
    });

    expect(document.body.querySelector('[data-slot="sheet-overlay"]')).not.toBeNull();

    const closeButton = findCloseButton(document.body);
    expect(closeButton).toBeDefined();

    act(() => {
      closeButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(onClose).toHaveBeenCalledTimes(1);

    const refWarnings = consoleError.mock.calls.filter(([message]) =>
      typeof message === "string" && message.includes("Function components cannot be given refs"),
    );
    expect(refWarnings).toHaveLength(0);

    consoleError.mockRestore();
  });
});
