// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";

import CommandPalette from "@/components/layout/CommandPalette";
import { dispatchOpenCommandPalette } from "@/lib/command-palette";
import { useSearch } from "@/hooks/use-search";

vi.mock("@/hooks/use-search", () => ({
  useSearch: vi.fn(),
}));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

function flush(): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, 0);
  });
}

describe("CommandPalette", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(useSearch).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: false,
    } as ReturnType<typeof useSearch>);

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

  it("opens from shared event and focuses search input", async () => {
    await act(async () => {
      root.render(
        <MemoryRouter>
          <CommandPalette />
        </MemoryRouter>,
      );
      await flush();
    });

    await act(async () => {
      dispatchOpenCommandPalette();
      await flush();
    });

    const input = document.body.querySelector(
      'input[placeholder="Search sessions, state, contacts..."]',
    );

    expect(input).toBeInstanceOf(HTMLInputElement);
    expect(document.activeElement).toBe(input);
  });
});
