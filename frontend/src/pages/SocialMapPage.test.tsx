// @vitest-environment jsdom

import { act } from "react";
import { afterAll, afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";

import SocialMapPage from "@/pages/SocialMapPage";
import { useDunbarRanking } from "@/hooks/use-memory";
import type { DunbarEntry, DunbarRankingResponse } from "@/api/types";

// jsdom does not implement ResizeObserver. Stub it so useElementSize fires
// immediately and returns non-zero dimensions before the component mounts.
const _originalResizeObserver = (globalThis as typeof globalThis & { ResizeObserver?: unknown }).ResizeObserver;
(globalThis as typeof globalThis & { ResizeObserver?: unknown }).ResizeObserver =
  class {
    private _cb: ResizeObserverCallback;
    constructor(cb: ResizeObserverCallback) { this._cb = cb; }
    observe() {
      // Trigger immediately so useElementSize can set a non-zero size.
      this._cb(
        [{ contentRect: { width: 800, height: 600 } } as ResizeObserverEntry],
        this as unknown as ResizeObserver,
      );
    }
    unobserve() {}
    disconnect() {}
  };

// Return 800x600 for all element size queries so the canvas renders in jsdom.
const _originalClientWidthDescriptor = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "clientWidth");
const _originalClientHeightDescriptor = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "clientHeight");
Object.defineProperty(HTMLElement.prototype, "clientWidth", { configurable: true, get: () => 800 });
Object.defineProperty(HTMLElement.prototype, "clientHeight", { configurable: true, get: () => 600 });

afterAll(() => {
  // Restore globals so this file's module-scope stubs don't leak into other workers.
  (globalThis as typeof globalThis & { ResizeObserver?: unknown }).ResizeObserver = _originalResizeObserver;
  if (_originalClientWidthDescriptor) {
    Object.defineProperty(HTMLElement.prototype, "clientWidth", _originalClientWidthDescriptor);
  } else {
    Reflect.deleteProperty(HTMLElement.prototype, "clientWidth");
  }
  if (_originalClientHeightDescriptor) {
    Object.defineProperty(HTMLElement.prototype, "clientHeight", _originalClientHeightDescriptor);
  } else {
    Reflect.deleteProperty(HTMLElement.prototype, "clientHeight");
  }
});

import type { Tier } from "@/components/memory/concentric-circles-constants";

// Stores the last onTierExpand callback provided to the canvas mock,
// so tests can simulate tier-badge clicks without needing real DOM dimensions.
let capturedOnTierExpand: ((tier: Tier) => void) | null = null;

// Mock the canvas components to avoid SVG complexity in jsdom.
// Captures onTierExpand so tests can invoke it directly.
vi.mock("@/components/memory/ConcentricCirclesCanvas", () => ({
  ConcentricCirclesCanvas: ({
    entries,
    onTierExpand,
  }: {
    entries: DunbarEntry[];
    expandedTiers: Set<Tier>;
    onTierExpand: (tier: Tier) => void;
  }) => {
    capturedOnTierExpand = onTierExpand;
    return <div data-testid="canvas">canvas:{entries.length}</div>;
  },
}));

vi.mock("@/components/memory/HorizontalStrataCanvas", () => ({
  HorizontalStrataCanvas: ({
    entries,
    onTierExpand,
  }: {
    entries: DunbarEntry[];
    expandedTiers: Set<Tier>;
    onTierExpand: (tier: Tier) => void;
  }) => {
    capturedOnTierExpand = onTierExpand;
    return <div data-testid="strata-canvas">strata:{entries.length}</div>;
  },
}));

vi.mock("@/hooks/use-memory", () => ({
  useDunbarRanking: vi.fn(),
}));

// Mock useViewport so we can control isMobile in tests.
// Default: desktop (isMobile = false). Individual tests override via mockReturnValue.
const mockUseViewport = vi.fn(() => ({ width: 1280, isMobile: false }));
vi.mock("@/hooks/use-viewport", () => ({
  useViewport: () => mockUseViewport(),
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
    // Default viewport: desktop.
    mockUseViewport.mockReturnValue({ width: 1280, isMobile: false });
    capturedOnTierExpand = null;
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

    // No error or loading message -- cold-start shows the actionable EmptyStatePanel instead
    expect(container.textContent).not.toContain("Failed to load social map");
    expect(container.textContent).not.toContain("Loading social map");
  });

  // ---------------------------------------------------------------------------
  // Cold-start empty state (scoredCount < 5)
  // ---------------------------------------------------------------------------

  it("shows EmptyStatePanel with CTA when scoredCount is 0", async () => {
    vi.mocked(useDunbarRanking).mockReturnValue(makeRankingResult([], null));
    renderPage();
    await act(async () => { await flush(); });

    expect(container.querySelector("[data-testid='empty-state-panel']")).toBeTruthy();
    const ctaLink = container.querySelector("a[href='/ingestion?tab=connectors']") as HTMLAnchorElement | null;
    expect(ctaLink).toBeTruthy();
    expect(ctaLink?.textContent).toContain("Connect a service");
  });

  it("shows EmptyStatePanel when scoredCount is 4 (below threshold)", async () => {
    // Owner + 4 scored contacts = scoredCount 4 (< 5)
    const scored = Array.from({ length: 4 }, (_, i) => ({
      contact_id: `c-${i}`,
      entity_id: `e-${i}`,
      canonical_name: `Contact ${i}`,
      dunbar_tier: 50 as const,
      dunbar_score: 0.5,
      dunbar_tier_override: false,
    }));
    vi.mocked(useDunbarRanking).mockReturnValue(
      makeRankingResult([OWNER_ENTRY, ...scored], OWNER_ENTRY.entity_id),
    );
    renderPage();
    await act(async () => { await flush(); });

    expect(container.querySelector("[data-testid='empty-state-panel']")).toBeTruthy();
  });

  it("hides EmptyStatePanel when scoredCount reaches 5", async () => {
    // Owner + 5 scored contacts = scoredCount 5 (at threshold, panel disappears)
    const scored = Array.from({ length: 5 }, (_, i) => ({
      contact_id: `c-${i}`,
      entity_id: `e-${i}`,
      canonical_name: `Contact ${i}`,
      dunbar_tier: 50 as const,
      dunbar_score: 0.5,
      dunbar_tier_override: false,
    }));
    vi.mocked(useDunbarRanking).mockReturnValue(
      makeRankingResult([OWNER_ENTRY, ...scored], OWNER_ENTRY.entity_id),
    );
    renderPage();
    await act(async () => { await flush(); });

    expect(container.querySelector("[data-testid='empty-state-panel']")).toBeNull();
  });

  it("applies aria-hidden and pointerEvents:none to canvas wrapper when cold-start", async () => {
    // scoredCount 0 => isColdStart; the canvas wrapper must be removed from
    // the a11y tree and made non-interactive so focus lands on the CTA instead.
    vi.mocked(useDunbarRanking).mockReturnValue(makeRankingResult([], null));
    renderPage();
    await act(async () => { await flush(); });

    const canvasWrapper = container.querySelector("[data-testid='canvas']")?.parentElement;
    expect(canvasWrapper).toBeTruthy();
    expect(canvasWrapper?.getAttribute("aria-hidden")).toBe("true");
    expect((canvasWrapper as HTMLElement | null)?.style.pointerEvents).toBe("none");
  });

  it("does not apply aria-hidden to canvas wrapper when not cold-start", async () => {
    // 5 scored contacts => isColdStart false; canvas must be accessible.
    const scored = Array.from({ length: 5 }, (_, i) => ({
      contact_id: `c-${i}`,
      entity_id: `e-${i}`,
      canonical_name: `Contact ${i}`,
      dunbar_tier: 50 as const,
      dunbar_score: 0.5,
      dunbar_tier_override: false,
    }));
    vi.mocked(useDunbarRanking).mockReturnValue(
      makeRankingResult([OWNER_ENTRY, ...scored], OWNER_ENTRY.entity_id),
    );
    renderPage();
    await act(async () => { await flush(); });

    const canvasWrapper = container.querySelector("[data-testid='canvas']")?.parentElement;
    expect(canvasWrapper).toBeTruthy();
    expect(canvasWrapper?.getAttribute("aria-hidden")).toBeNull();
    expect((canvasWrapper as HTMLElement | null)?.style.pointerEvents).toBe("");
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

  it("pill bar is hidden when no tiers are expanded", async () => {
    vi.mocked(useDunbarRanking).mockReturnValue(
      makeRankingResult([OWNER_ENTRY, CONTACT_ENTRY], OWNER_ENTRY.entity_id),
    );
    renderPage();
    await act(async () => { await flush(); });

    // No "Showing all:" text should appear when nothing is expanded
    expect(container.textContent).not.toContain("Showing all:");
  });

  it("pill bar shows tier pill when a tier is expanded via badge click", async () => {
    vi.mocked(useDunbarRanking).mockReturnValue(
      makeRankingResult([OWNER_ENTRY, CONTACT_ENTRY], OWNER_ENTRY.entity_id),
    );
    renderPage();
    await act(async () => { await flush(); });

    // Simulate the canvas calling onTierExpand(50) (as if the user clicked the +N badge)
    expect(capturedOnTierExpand).toBeTruthy();
    act(() => { capturedOnTierExpand!(50); });
    await act(async () => { await flush(); });

    // Pill bar should now show "Good Friends" tier
    expect(container.textContent).toContain("Showing all:");
    expect(container.textContent).toContain("Good Friends");
  });

  it("expanding two tiers shows two pills and a Reset all button", async () => {
    vi.mocked(useDunbarRanking).mockReturnValue(
      makeRankingResult([OWNER_ENTRY, CONTACT_ENTRY], OWNER_ENTRY.entity_id),
    );
    renderPage();
    await act(async () => { await flush(); });

    act(() => { capturedOnTierExpand!(50); });
    act(() => { capturedOnTierExpand!(150); });
    await act(async () => { await flush(); });

    expect(container.textContent).toContain("Good Friends");
    expect(container.textContent).toContain("Dunbar's Number");
    expect(container.textContent).toContain("Reset all");
  });

  it("clicking ✕ on one pill collapses that tier but leaves the other expanded", async () => {
    vi.mocked(useDunbarRanking).mockReturnValue(
      makeRankingResult([OWNER_ENTRY, CONTACT_ENTRY], OWNER_ENTRY.entity_id),
    );
    renderPage();
    await act(async () => { await flush(); });

    // Expand both tiers 50 and 150
    act(() => { capturedOnTierExpand!(50); });
    act(() => { capturedOnTierExpand!(150); });
    await act(async () => { await flush(); });

    // Verify both pills are present in the pill bar
    expect(container.querySelector("[aria-label='Collapse Good Friends']")).toBeTruthy();
    expect(container.querySelector("[aria-label='Collapse Dunbar\\'s Number']")).toBeTruthy();

    // Click the collapse button for "Good Friends" (tier 50)
    const collapseGoodFriends = container.querySelector(
      "[aria-label='Collapse Good Friends']",
    ) as HTMLButtonElement;
    act(() => { collapseGoodFriends.click(); });
    await act(async () => { await flush(); });

    // Tier 50 pill should be gone; tier 150 pill should remain
    expect(container.querySelector("[aria-label='Collapse Good Friends']")).toBeNull();
    expect(container.querySelector("[aria-label='Collapse Dunbar\\'s Number']")).toBeTruthy();
    // With only one tier expanded, "Reset all" should be hidden
    expect(container.querySelector("[aria-label='Collapse all expanded tiers']")).toBeNull();
  });

  it("Reset all clears all expanded tiers and hides the pill bar", async () => {
    vi.mocked(useDunbarRanking).mockReturnValue(
      makeRankingResult([OWNER_ENTRY, CONTACT_ENTRY], OWNER_ENTRY.entity_id),
    );
    renderPage();
    await act(async () => { await flush(); });

    // Expand both tiers
    act(() => { capturedOnTierExpand!(50); });
    act(() => { capturedOnTierExpand!(150); });
    await act(async () => { await flush(); });

    const resetAll = container.querySelector(
      "[aria-label='Collapse all expanded tiers']",
    ) as HTMLButtonElement | null;
    expect(resetAll).toBeTruthy();
    act(() => { resetAll!.click(); });
    await act(async () => { await flush(); });

    // Pill bar should disappear entirely (no tier collapse pills remain)
    expect(container.querySelector("[aria-label='Collapse Good Friends']")).toBeNull();
    expect(container.querySelector("[aria-label='Collapse Dunbar\\'s Number']")).toBeNull();
    expect(container.querySelector("[aria-label='Collapse all expanded tiers']")).toBeNull();
    expect(container.textContent).not.toContain("Showing all:");
  });

  // ---------------------------------------------------------------------------
  // Responsive layout switching
  // ---------------------------------------------------------------------------

  it("renders ConcentricCirclesCanvas (rings) on desktop viewport (>640px)", async () => {
    mockUseViewport.mockReturnValue({ width: 1280, isMobile: false });
    vi.mocked(useDunbarRanking).mockReturnValue(
      makeRankingResult([OWNER_ENTRY, CONTACT_ENTRY], OWNER_ENTRY.entity_id),
    );
    renderPage();
    await act(async () => { await flush(); });

    // On desktop the rings canvas is rendered, not the strata canvas.
    expect(container.querySelector("[data-testid='canvas']")).toBeTruthy();
    expect(container.querySelector("[data-testid='strata-canvas']")).toBeNull();
  });

  it("renders HorizontalStrataCanvas (strata) on mobile viewport (≤640px)", async () => {
    mockUseViewport.mockReturnValue({ width: 390, isMobile: true });
    vi.mocked(useDunbarRanking).mockReturnValue(
      makeRankingResult([OWNER_ENTRY, CONTACT_ENTRY], OWNER_ENTRY.entity_id),
    );
    renderPage();
    await act(async () => { await flush(); });

    // On mobile the strata canvas is rendered, not the rings canvas.
    expect(container.querySelector("[data-testid='strata-canvas']")).toBeTruthy();
    expect(container.querySelector("[data-testid='canvas']")).toBeNull();
  });

  it("preserves search state when switching from desktop rings to mobile strata", async () => {
    // Start in desktop mode with a search active.
    mockUseViewport.mockReturnValue({ width: 1280, isMobile: false });
    vi.mocked(useDunbarRanking).mockReturnValue(
      makeRankingResult([OWNER_ENTRY, CONTACT_ENTRY], OWNER_ENTRY.entity_id),
    );
    renderPage("/entities/social-map?q=alice");
    await act(async () => { await flush(); });

    // Verify search input is populated from URL.
    const input = container.querySelector("input[aria-label='Search contacts']") as HTMLInputElement;
    expect(input).toBeTruthy();
    expect(input.value).toBe("alice");

    // Simulate viewport switch to mobile.
    mockUseViewport.mockReturnValue({ width: 390, isMobile: true });
    act(() => {
      root.render(
        <MemoryRouter initialEntries={["/entities/social-map?q=alice"]}>
          <SocialMapPage />
        </MemoryRouter>,
      );
    });
    await act(async () => { await flush(); });

    // Strata is now shown and search input is still populated.
    expect(container.querySelector("[data-testid='strata-canvas']")).toBeTruthy();
    const inputAfter = container.querySelector("input[aria-label='Search contacts']") as HTMLInputElement;
    expect(inputAfter.value).toBe("alice");
  });
});
