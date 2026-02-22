// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";

import PageHeader from "@/components/layout/PageHeader";
import { useDarkMode } from "@/hooks/useDarkMode";
import { OPEN_COMMAND_PALETTE_EVENT } from "@/lib/command-palette";

vi.mock("@/hooks/useDarkMode", () => ({
  useDarkMode: vi.fn(),
}));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

describe("PageHeader", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(useDarkMode).mockReturnValue({
      theme: "light",
      setTheme: vi.fn(),
      resolvedTheme: "light",
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

  it("renders search trigger with Cmd/Ctrl+K hint and dispatches open event", () => {
    const openListener = vi.fn();
    window.addEventListener(OPEN_COMMAND_PALETTE_EVENT, openListener);

    act(() => {
      root.render(
        <MemoryRouter initialEntries={["/sessions"]}>
          <PageHeader title="Sessions" />
        </MemoryRouter>,
      );
    });

    const searchButton = Array.from(document.body.querySelectorAll("button")).find((button) =>
      button.getAttribute("aria-label")?.includes("Open command palette"),
    );

    expect(searchButton).toBeInstanceOf(HTMLButtonElement);
    expect(searchButton?.getAttribute("title")).toBe("Cmd/Ctrl+K");

    act(() => {
      searchButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(openListener).toHaveBeenCalledTimes(1);

    window.removeEventListener(OPEN_COMMAND_PALETTE_EVENT, openListener);
  });
});
