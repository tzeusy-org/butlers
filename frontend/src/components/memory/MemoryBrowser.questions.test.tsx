// @vitest-environment jsdom
/** The Questions register pill on MemoryBrowser (bu-q7vx1q.73). */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, useLocation } from "react-router";

import MemoryBrowser from "@/components/memory/MemoryBrowser";

vi.mock("@/hooks/use-memory", () => ({
  useEpisodes: () => ({ data: { data: [], meta: { total: 0, offset: 0, limit: 50, has_more: false } } }),
  useFacts: () => ({ data: { data: [], meta: { total: 0, offset: 0, limit: 50, has_more: false } } }),
  useRules: () => ({ data: { data: [], meta: { total: 0, offset: 0, limit: 50, has_more: false } } }),
  useMemoryInspect: () => ({ data: { data: [], meta: { total: 0, offset: 0, limit: 50, has_more: false } } }),
  useRetryEpisodeConsolidation: () => ({ mutate: vi.fn(), isPending: false, isError: false }),
}));

vi.mock("@/components/memory/QuestionsRegister", () => ({
  default: () => <div data-testid="questions-register-stub" />,
}));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

let search = "";
function LocationProbe({ onRead }: { onRead: (search: string) => void }) {
  onRead(useLocation().search);
  return null;
}
const readSearch = (next: string) => {
  search = next;
};

describe("MemoryBrowser questions pill", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  function render(initial = "/memory") {
    act(() => {
      root.render(
        <MemoryRouter initialEntries={[initial]}>
          <LocationProbe onRead={readSearch} />
          <MemoryBrowser />
        </MemoryRouter>,
      );
    });
  }

  /** The register pill row follows the search scope pills, so take the last match. */
  function pill(label: string): HTMLElement {
    return Array.from(container.querySelectorAll("button")).filter((b) => b.textContent === label).at(-1)!;
  }

  it("selecting Questions writes register=questions to the URL and shows the register", () => {
    render();
    expect(container.querySelector('[data-testid="questions-register-stub"]')).toBeNull();

    act(() => pill("Questions").click());

    expect(new URLSearchParams(search).get("register")).toBe("questions");
    expect(container.querySelector('[data-testid="questions-register-stub"]')).not.toBeNull();
  });

  it("deep-links straight to the register, and switching away resets paging", () => {
    render("/memory?register=questions&offset=50");
    expect(container.querySelector('[data-testid="questions-register-stub"]')).not.toBeNull();

    act(() => pill("Rules").click());

    expect(new URLSearchParams(search).get("register")).toBe("rules");
    expect(new URLSearchParams(search).has("offset")).toBe(false);
  });
});
