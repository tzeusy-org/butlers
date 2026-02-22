// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";

import { useKeyboardShortcuts } from "@/hooks/use-keyboard-shortcuts";
import { OPEN_COMMAND_PALETTE_EVENT } from "@/lib/command-palette";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

function Harness() {
  useKeyboardShortcuts();
  return <div>shortcuts</div>;
}

describe("useKeyboardShortcuts", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
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

  it("dispatches open-search on Ctrl+K outside editable fields", () => {
    const listener = vi.fn();
    window.addEventListener(OPEN_COMMAND_PALETTE_EVENT, listener);

    act(() => {
      root.render(
        <MemoryRouter>
          <Harness />
        </MemoryRouter>,
      );
    });

    act(() => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "k", ctrlKey: true, bubbles: true }));
    });

    expect(listener).toHaveBeenCalledTimes(1);

    window.removeEventListener(OPEN_COMMAND_PALETTE_EVENT, listener);
  });

  it("ignores Ctrl+K inside editable fields", () => {
    const listener = vi.fn();
    window.addEventListener(OPEN_COMMAND_PALETTE_EVENT, listener);

    act(() => {
      root.render(
        <MemoryRouter>
          <Harness />
        </MemoryRouter>,
      );
    });

    const input = document.createElement("input");
    document.body.appendChild(input);

    act(() => {
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "k", ctrlKey: true, bubbles: true }));
    });

    expect(listener).toHaveBeenCalledTimes(0);

    input.remove();
    window.removeEventListener(OPEN_COMMAND_PALETTE_EVENT, listener);
  });
});
