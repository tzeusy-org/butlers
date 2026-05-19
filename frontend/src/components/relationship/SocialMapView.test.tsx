// @vitest-environment jsdom

import { act } from "react";
import { afterAll, afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";

import { SocialMapView } from "@/components/relationship/SocialMapView";
import { useDunbarRanking } from "@/hooks/use-memory";
import type { DunbarEntry, DunbarRankingResponse } from "@/api/types";
import type { Tier } from "@/components/memory/concentric-circles-constants";

// Override ResizeObserver so useElementSize sees a non-zero stage size.
(globalThis as typeof globalThis & { ResizeObserver?: unknown }).ResizeObserver =
  class {
    private _cb: ResizeObserverCallback;
    constructor(cb: ResizeObserverCallback) { this._cb = cb; }
    observe() {
      this._cb(
        [{ contentRect: { width: 800, height: 600 } } as ResizeObserverEntry],
        this as unknown as ResizeObserver,
      );
    }
    unobserve() {}
    disconnect() {}
  };

const _originalClientWidthDescriptor = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "clientWidth");
const _originalClientHeightDescriptor = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "clientHeight");
Object.defineProperty(HTMLElement.prototype, "clientWidth", { configurable: true, get: () => 800 });
Object.defineProperty(HTMLElement.prototype, "clientHeight", { configurable: true, get: () => 600 });

afterAll(() => {
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

// Mock canvas components to avoid SVG complexity in jsdom.
vi.mock("@/components/memory/ConcentricCirclesCanvas", () => ({
  ConcentricCirclesCanvas: ({
    entries,
    onTierExpand,
  }: {
    entries: DunbarEntry[];
    expandedTiers: Set<Tier>;
    onTierExpand: (tier: Tier) => void;
  }) => {
    void onTierExpand;
    return <div data-testid="canvas">canvas:{entries.length}</div>;
  },
}));

vi.mock("@/components/memory/HorizontalStrataCanvas", () => ({
  HorizontalStrataCanvas: ({
    entries,
  }: {
    entries: DunbarEntry[];
  }) => {
    return <div data-testid="strata-canvas">strata:{entries.length}</div>;
  },
}));

vi.mock("@/hooks/use-memory", () => ({
  useDunbarRanking: vi.fn(),
}));

const mockUseViewport = vi.fn(() => ({ width: 1280, isMobile: false }));
vi.mock("@/hooks/use-viewport", () => ({
  useViewport: () => mockUseViewport(),
}));

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

describe("SocialMapView", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.resetAllMocks();
    mockUseViewport.mockReturnValue({ width: 1280, isMobile: false });
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

  function renderView(url = "/entities/social-map") {
    act(() => {
      root.render(
        <MemoryRouter initialEntries={[url]}>
          <SocialMapView />
        </MemoryRouter>,
      );
    });
  }

  it("renders without crashing given mocked deps", async () => {
    vi.mocked(useDunbarRanking).mockReturnValue(
      makeRankingResult([OWNER_ENTRY, CONTACT_ENTRY], OWNER_ENTRY.entity_id),
    );
    renderView();
    await act(async () => { await flush(); });

    // SocialMapView renders the controls bar (search, jump-to-tier).
    expect(container.textContent).toContain("Jump to:");
  });

  it("shows loading state", async () => {
    vi.mocked(useDunbarRanking).mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      error: null,
    } as unknown as ReturnType<typeof useDunbarRanking>);
    renderView();
    await act(async () => { await flush(); });

    expect(container.textContent).toContain("Loading social map");
  });

  it("shows error state when fetch fails", async () => {
    vi.mocked(useDunbarRanking).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("network error"),
    } as unknown as ReturnType<typeof useDunbarRanking>);
    renderView();
    await act(async () => { await flush(); });

    expect(container.textContent).toContain("Couldn't load your social map");
  });

  it("renders jump-to-tier chips", async () => {
    vi.mocked(useDunbarRanking).mockReturnValue(
      makeRankingResult([OWNER_ENTRY, CONTACT_ENTRY], OWNER_ENTRY.entity_id),
    );
    renderView();
    await act(async () => { await flush(); });

    expect(container.textContent).toContain("Jump to:");
    expect(container.textContent).toContain("1500");
  });

  it("renders the search input", async () => {
    vi.mocked(useDunbarRanking).mockReturnValue(
      makeRankingResult([OWNER_ENTRY, CONTACT_ENTRY], OWNER_ENTRY.entity_id),
    );
    renderView();
    await act(async () => { await flush(); });

    const input = container.querySelector("input[aria-label='Search contacts']") as HTMLInputElement | null;
    expect(input).toBeTruthy();
  });

  it("shows EmptyStatePanel when scoredCount is 0 (cold-start)", async () => {
    vi.mocked(useDunbarRanking).mockReturnValue(makeRankingResult([], null));
    renderView();
    await act(async () => { await flush(); });

    expect(container.querySelector("[data-testid='empty-state-panel']")).toBeTruthy();
  });

  it("renders ConcentricCirclesCanvas on desktop viewport", async () => {
    mockUseViewport.mockReturnValue({ width: 1280, isMobile: false });
    vi.mocked(useDunbarRanking).mockReturnValue(
      makeRankingResult([OWNER_ENTRY, CONTACT_ENTRY], OWNER_ENTRY.entity_id),
    );
    renderView();
    await act(async () => { await flush(); });

    expect(container.querySelector("[data-testid='canvas']")).toBeTruthy();
    expect(container.querySelector("[data-testid='strata-canvas']")).toBeNull();
  });

  it("renders HorizontalStrataCanvas on mobile viewport", async () => {
    mockUseViewport.mockReturnValue({ width: 390, isMobile: true });
    vi.mocked(useDunbarRanking).mockReturnValue(
      makeRankingResult([OWNER_ENTRY, CONTACT_ENTRY], OWNER_ENTRY.entity_id),
    );
    renderView();
    await act(async () => { await flush(); });

    expect(container.querySelector("[data-testid='strata-canvas']")).toBeTruthy();
    expect(container.querySelector("[data-testid='canvas']")).toBeNull();
  });
});
