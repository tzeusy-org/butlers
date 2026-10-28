// @vitest-environment jsdom
//
// Search paging lives in the URL (bu-kqnum.14.5): the offset reaches the inspect
// query, next/prev write only the offset, and changing q or kind resets it.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, useLocation, useNavigate } from "react-router";

import MemoryBrowser from "@/components/memory/MemoryBrowser";
import { useMemoryInspect } from "@/hooks/use-memory";

vi.mock("@/hooks/use-memory", () => ({
  useEpisodes: vi.fn(),
  useFacts: vi.fn(),
  useRules: vi.fn(),
  useMemoryInspect: vi.fn(),
  useRetryEpisodeConsolidation: vi.fn(),
}));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let lastSearch = "";
function Probe() {
  const search = useLocation().search;
  const navigate = useNavigate();
  useEffect(() => {
    lastSearch = search;
  }, [search]);
  return <>
    <button onClick={() => navigate(-1)}>history back</button>
    <button onClick={() => navigate(1)}>history forward</button>
  </>;
}

const episode = {
  id: "e1",
  kind: "episode",
  content: "episode e1",
  butler: "general",
  created_at: "2026-02-19T01:00:00Z",
  metadata: {},
  episode: {
    id: "e1",
    butler: "general",
    session_id: null,
    content: "episode e1",
    importance: 5,
    reference_count: 0,
    consolidated: false,
    consolidation_status: "pending",
    created_at: "2026-02-19T01:00:00Z",
    last_referenced_at: null,
    expires_at: null,
    metadata: {},
  },
};

describe("MemoryBrowser search paging", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.resetAllMocks();
    lastSearch = "";
    vi.mocked(useMemoryInspect).mockReturnValue({
      data: {
        data: [episode],
        meta: { total: 312, offset: 50, limit: 50, has_more: true },
      },
    } as unknown as ReturnType<typeof useMemoryInspect>);
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  function render(entry: string) {
    act(() => {
      root.render(
        <MemoryRouter initialEntries={[entry]}>
          <MemoryBrowser />
          <Probe />
        </MemoryRouter>,
      );
    });
  }

  const button = (label: string) =>
    Array.from(container.querySelectorAll<HTMLButtonElement>("button")).find(
      (b) => b.textContent === label,
    )!;

  it("passes the URL offset to the inspect query", () => {
    render("/memory?q=fatigue&offset=50");

    expect(vi.mocked(useMemoryInspect).mock.calls[0][0]).toMatchObject({
      q: "fatigue",
      offset: 50,
    });
  });

  it("next and prev write only the offset, keeping q and kind", () => {
    render("/memory?q=fatigue&kind=episode&offset=50");

    act(() => button("next").click());
    expect(lastSearch).toContain("offset=100");
    expect(lastSearch).toContain("q=fatigue");
    expect(lastSearch).toContain("kind=episode");

    act(() => button("prev").click());
    expect(lastSearch).toContain("offset=50");

    act(() => button("history back").click());
    expect(lastSearch).toContain("offset=100");
    expect(lastSearch).toContain("q=fatigue");
    expect(lastSearch).toContain("kind=episode");
    expect(vi.mocked(useMemoryInspect).mock.lastCall?.[0]).toMatchObject({
      q: "fatigue", kind: "episode", offset: 100,
    });

    act(() => button("history forward").click());
    expect(lastSearch).toContain("offset=50");
    expect(lastSearch).toContain("q=fatigue");
    expect(lastSearch).toContain("kind=episode");
    expect(vi.mocked(useMemoryInspect).mock.lastCall?.[0]).toMatchObject({
      q: "fatigue", kind: "episode", offset: 50,
    });
  });

  it("resets the offset when the scope pill changes", () => {
    render("/memory?q=fatigue&offset=100");

    act(() => button("Facts").click());

    expect(lastSearch).toContain("kind=fact");
    expect(lastSearch).not.toContain("offset=");
  });

  it("resets the offset when a new query is submitted", () => {
    render("/memory?q=fatigue&offset=100");
    const input = container.querySelector<HTMLInputElement>('[aria-label="Search memory"]')!;
    const setter = Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype,
      "value",
    )?.set;

    act(() => {
      setter?.call(input, "sleep");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    act(() => {
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    });

    expect(lastSearch).toContain("q=sleep");
    expect(lastSearch).not.toContain("offset=");
  });
});
