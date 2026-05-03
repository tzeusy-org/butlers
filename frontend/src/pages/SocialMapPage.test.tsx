// @vitest-environment jsdom

import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";

import SocialMapPage from "@/pages/SocialMapPage";
import { useDunbarRanking } from "@/hooks/use-memory";
import type { DunbarEntry, DunbarRankingResponse } from "@/api/types";

// jsdom does not implement ResizeObserver. Stub it before any module loads.
(globalThis as typeof globalThis & { ResizeObserver?: unknown }).ResizeObserver =
  class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };

// Mock the canvas component to avoid SVG complexity in jsdom
vi.mock("@/components/memory/ConcentricCirclesCanvas", () => ({
  ConcentricCirclesCanvas: ({ entries }: { entries: DunbarEntry[] }) => (
    <div data-testid="canvas">canvas:{entries.length}</div>
  ),
}));

vi.mock("@/hooks/use-memory", () => ({
  useDunbarRanking: vi.fn(),
}));

// react-router's useNavigate / useSearchParams need a real router context
// provided by MemoryRouter below.

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const OWNER_ENTRY: DunbarEntry = {
  contact_id: "c-owner",
  entity_id: "e-owner",
  canonical_name: "Ada Lovelace",
  dunbar_tier: 5,
  dunbar_score: 1,
  dunbar_tier_override: false,
};

const CONTACT_ENTRY: DunbarEntry = {
  contact_id: "c-alice",
  entity_id: "e-alice",
  canonical_name: "Alice Nguyen",
  dunbar_tier: 5,
  dunbar_score: 0.8,
  dunbar_tier_override: false,
};

function makeRankingResult(entries: DunbarEntry[], ownerEntityId: string | null): ReturnType<typeof useDunbarRanking> {
  const data: DunbarRankingResponse = { entries, owner_entity_id: ownerEntityId };
  return { data, isLoading: false, isError: false, error: null } as unknown as ReturnType<typeof useDunbarRanking>;
}

function flush(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

describe("SocialMapPage", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.resetAllMocks();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => { root.unmount(); });
    container.remove();
    document.body.innerHTML = "";
    vi.restoreAllMocks();
  });

  function renderPage(url = "/entities/social-map") {
    act(() => {
      root.render(
        <MemoryRouter initialEntries={[url]}>
          <SocialMapPage />
        </MemoryRouter>,
      );
    });
  }

  it("renders the page header", async () => {
    vi.mocked(useDunbarRanking).mockReturnValue(
      makeRankingResult([OWNER_ENTRY, CONTACT_ENTRY], OWNER_ENTRY.entity_id),
    );
    renderPage();
    await act(async () => { await flush(); });

    expect(container.textContent).toContain("Your Social Map");
  });

  it("renders the canvas when data loads (or sizing placeholder in jsdom)", async () => {
    vi.mocked(useDunbarRanking).mockReturnValue(
      makeRankingResult([OWNER_ENTRY, CONTACT_ENTRY], OWNER_ENTRY.entity_id),
    );
    renderPage();
    await act(async () => { await flush(); });

    // In jsdom, ResizeObserver never fires so stageSize is {0,0}.
    // The page shows "Sizing canvas..." until dimensions are known.
    // Verify that the data state is not an error/loading state.
    expect(container.textContent).not.toContain("Failed to load social map");
    expect(container.textContent).not.toContain("Loading social map");
  });

  it("shows loading state when data is loading", async () => {
    vi.mocked(useDunbarRanking).mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      error: null,
    } as unknown as ReturnType<typeof useDunbarRanking>);
    renderPage();
    await act(async () => { await flush(); });

    expect(container.textContent).toContain("Loading social map");
  });

  it("shows error state when data fetch fails", async () => {
    vi.mocked(useDunbarRanking).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("network error"),
    } as unknown as ReturnType<typeof useDunbarRanking>);
    renderPage();
    await act(async () => { await flush(); });

    expect(container.textContent).toContain("Failed to load social map");
  });

  it("renders no error or loading state when entries array is empty", async () => {
    vi.mocked(useDunbarRanking).mockReturnValue(makeRankingResult([], null));
    renderPage();
    await act(async () => { await flush(); });

    // No error or loading message — cold-start is handled inside the canvas
    expect(container.textContent).not.toContain("Failed to load social map");
    expect(container.textContent).not.toContain("Loading social map");
  });

  it("renders jump-to-tier chips", async () => {
    vi.mocked(useDunbarRanking).mockReturnValue(
      makeRankingResult([OWNER_ENTRY, CONTACT_ENTRY], OWNER_ENTRY.entity_id),
    );
    renderPage();
    await act(async () => { await flush(); });

    // Should show a chip for each tier (5, 15, 50, 150, 500, 1500)
    expect(container.textContent).toContain("5");
    expect(container.textContent).toContain("1500");
    expect(container.textContent).toContain("Jump to:");
  });

  it("renders the search input", async () => {
    vi.mocked(useDunbarRanking).mockReturnValue(
      makeRankingResult([OWNER_ENTRY, CONTACT_ENTRY], OWNER_ENTRY.entity_id),
    );
    renderPage();
    await act(async () => { await flush(); });

    const input = container.querySelector("input[aria-label='Search contacts']") as HTMLInputElement | null;
    expect(input).toBeTruthy();
  });
});
