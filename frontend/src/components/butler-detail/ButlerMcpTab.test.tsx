// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";

import ButlerMcpTab from "@/components/butler-detail/ButlerMcpTab";
import { callButlerMcpTool, getButlerMcpTools } from "@/api/index.ts";

vi.mock("@/api/index.ts", () => ({
  getButlerMcpTools: vi.fn(),
  callButlerMcpTool: vi.fn(),
}));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function flush(): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, 0);
  });
}

function findButton(container: HTMLElement, label: string): HTMLButtonElement | undefined {
  return Array.from(container.querySelectorAll("button")).find((button) =>
    button.textContent?.includes(label),
  );
}

function setTextareaValue(element: HTMLTextAreaElement, value: string) {
  const prototype = Object.getPrototypeOf(element) as typeof HTMLTextAreaElement.prototype;
  const descriptor = Object.getOwnPropertyDescriptor(prototype, "value");
  descriptor?.set?.call(element, value);
  element.dispatchEvent(new Event("input", { bubbles: true }));
}

describe("ButlerMcpTab", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(getButlerMcpTools).mockResolvedValue({
      data: [
        {
          name: "state_get",
          description: "Get a state value",
          input_schema: { type: "object" },
        },
      ],
      meta: {},
    });
    vi.mocked(callButlerMcpTool).mockResolvedValue({
      data: {
        tool_name: "state_get",
        arguments: { key: "dashboard.debug" },
        result: { ok: true },
        raw_text: '{"ok":true}',
        is_error: false,
      },
      meta: {},
    });

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

  it("loads tools and calls selected tool with parsed JSON arguments", async () => {
    await act(async () => {
      root.render(<ButlerMcpTab butlerName="general" />);
      await flush();
    });

    const argsInput = container.querySelector("#mcp-tool-arguments");
    expect(argsInput).not.toBeNull();

    await act(async () => {
      if (argsInput instanceof HTMLTextAreaElement) {
        setTextareaValue(argsInput, '{"key":"dashboard.debug"}');
      }
      await flush();
    });

    const callButton = findButton(container, "Call Tool");
    expect(callButton).toBeDefined();

    await act(async () => {
      callButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    expect(getButlerMcpTools).toHaveBeenCalledTimes(1);
    expect(getButlerMcpTools).toHaveBeenCalledWith("general");
    expect(callButlerMcpTool).toHaveBeenCalledTimes(1);
    expect(callButlerMcpTool).toHaveBeenCalledWith("general", {
      tool_name: "state_get",
      arguments: { key: "dashboard.debug" },
    });
    expect(container.textContent).toContain("Last Response");
  });
});
