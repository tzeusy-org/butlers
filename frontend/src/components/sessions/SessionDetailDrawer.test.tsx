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
  request_id: null,
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

function toolOutcomeDots(): Element[] {
  return Array.from(document.body.querySelectorAll("[data-tool-call-outcome]"));
}

describe("SessionDetailDrawer", () => {
  let container: HTMLDivElement;
  let root: Root;

  function renderDrawer() {
    act(() => {
      root.render(
        <MemoryRouter>
          <SessionDetailDrawer butler="switchboard" sessionId="sess-123" onClose={() => {}} />
        </MemoryRouter>,
      );
    });
  }

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

  it("shows tool-call arguments for runtime records using input payloads", () => {
    setQueryState({
      data: {
        data: {
          ...SESSION_DETAIL,
          tool_calls: [
            {
              id: "tc-1",
              name: "route_to_butler",
              input: { butler: "general", prompt: "Roll up stats" },
            },
          ],
        },
        meta: {},
      },
    });

    renderDrawer();

    expect(document.body.textContent).toContain("route_to_butler");
    expect(document.body.textContent).toContain("Arguments");
  });

  it("normalizes nested mcp tool-call payloads", () => {
    setQueryState({
      data: {
        data: {
          ...SESSION_DETAIL,
          tool_calls: [
            {
              type: "mcp_tool_call",
              call: {
                id: "mcp-1",
                name: "route_to_butler",
                arguments: JSON.stringify({ butler: "general", prompt: "Roll up stats" }),
              },
              output: { status: "accepted", butler: "general" },
            },
          ],
        },
        meta: {},
      },
    });

    renderDrawer();

    expect(document.body.textContent).toContain("route_to_butler");
    expect(document.body.textContent).toContain("Arguments");
    expect(document.body.textContent).toContain("Result");
  });

  it("shows tool name when payload nests it under tool object", () => {
    setQueryState({
      data: {
        data: {
          ...SESSION_DETAIL,
          tool_calls: [
            {
              tool: { name: "route_to_butler" },
              args: { butler: "general", prompt: "Roll up stats" },
            },
          ],
        },
        meta: {},
      },
    });

    renderDrawer();

    expect(document.body.textContent).toContain("route_to_butler");
    expect(document.body.textContent).not.toContain("Tool #1");
  });

  it("uses session result summary to label unnamed tool calls", () => {
    setQueryState({
      data: {
        data: {
          ...SESSION_DETAIL,
          result: [
            "MCP tools called:",
            "- `route_to_butler({\"butler\":\"general\",\"prompt\":\"Roll up stats\"})`",
            "- `route_to_butler({\"butler\":\"general\",\"prompt\":\"Roll up stats\"})`",
          ].join("\n"),
          tool_calls: [
            { args: { butler: "general", prompt: "Roll up stats" } },
            { args: { butler: "general", prompt: "Roll up stats" } },
          ],
        },
        meta: {},
      },
    });

    renderDrawer();

    expect(document.body.textContent).toContain("route_to_butler");
    expect(document.body.textContent).not.toContain("Tool #1");
    expect(document.body.textContent).not.toContain("Tool #2");
  });

  it("uses colon summary format to label unnamed tool calls", () => {
    setQueryState({
      data: {
        data: {
          ...SESSION_DETAIL,
          result: [
            "MCP tools called:",
            "- `memory_store_fact`: `subject=...`",
            "- `notify`: `channel=telegram`",
          ].join("\n"),
          tool_calls: [
            { args: { subject: "Chloe", predicate: "birthday" } },
            { args: { channel: "telegram", intent: "reply" } },
          ],
        },
        meta: {},
      },
    });

    renderDrawer();

    expect(document.body.textContent).toContain("memory_store_fact");
    expect(document.body.textContent).toContain("notify");
    expect(document.body.textContent).not.toContain("Tool #1");
    expect(document.body.textContent).not.toContain("Tool #2");
  });

  it("colorizes tool-call dots by deterministic outcome state with accessible labels", () => {
    setQueryState({
      data: {
        data: {
          ...SESSION_DETAIL,
          tool_calls: [
            { name: "state_get", success: true, result: { value: "ok" } },
            { name: "state_set", error: "write failed" },
            { name: "route_to_butler", status: "pending" },
            { name: "state_list", args: { prefix: "x" } },
          ],
        },
        meta: {},
      },
    });

    renderDrawer();

    const dots = toolOutcomeDots();
    expect(dots).toHaveLength(4);

    expect(dots[0]?.getAttribute("data-tool-call-outcome")).toBe("success");
    expect(dots[0]?.className).toContain("bg-emerald-500");
    expect(dots[0]?.getAttribute("aria-label")).toBe("Tool call outcome: Success");

    expect(dots[1]?.getAttribute("data-tool-call-outcome")).toBe("failed");
    expect(dots[1]?.className).toContain("bg-destructive");
    expect(dots[1]?.getAttribute("aria-label")).toBe("Tool call outcome: Failed");

    expect(dots[2]?.getAttribute("data-tool-call-outcome")).toBe("pending");
    expect(dots[2]?.className).toContain("bg-amber-500");
    expect(dots[2]?.getAttribute("aria-label")).toBe("Tool call outcome: Pending");

    expect(dots[3]?.getAttribute("data-tool-call-outcome")).toBe("unknown");
    expect(dots[3]?.className).toContain("bg-muted-foreground/40");
    expect(dots[3]?.getAttribute("aria-label")).toBe("Tool call outcome: Unknown");
  });

  it("surfaces failed-then-retried tool call outcomes as separate timeline entries", () => {
    setQueryState({
      data: {
        data: {
          ...SESSION_DETAIL,
          tool_calls: [
            {
              name: "route_to_butler",
              input: { butler: "relationship", prompt: "Store birthday" },
              outcome: "error",
              error: "TimeoutError: target unavailable",
            },
            {
              name: "route_to_butler",
              input: { butler: "relationship", prompt: "Store birthday" },
              outcome: "success",
              result: { status: "accepted", butler: "relationship" },
            },
          ],
        },
        meta: {},
      },
    });

    renderDrawer();

    expect(document.body.textContent).toContain("Outcome:");
    expect(document.body.textContent).toContain("failed");
    expect(document.body.textContent).toContain("success");
    expect(document.body.textContent).toContain("Error");
    expect(document.body.textContent).toContain("Result");
    expect(document.body.textContent?.match(/route_to_butler/g)?.length).toBe(2);
  });
});
