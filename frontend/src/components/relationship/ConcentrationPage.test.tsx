// @vitest-environment jsdom
/**
 * Component tests for ConcentrationPage (§8.4).
 *
 * Covers:
 * - Route mounts at /entities/concentration (SubpageTabs rendered, Concentration tab active)
 * - Predicate tab strip rendered from predicate_tabs in response
 * - Clicking a predicate tab updates ?predicate= URL param
 * - Entity rows rendered from items list
 * - URL round-trip: ?predicate= param drives the API query key
 * - Loading state shows skeleton placeholders
 * - Empty state when items list is empty
 * - Error state with retry button
 * - Rollup header renders total and top-3 share
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

// ---------------------------------------------------------------------------
// Mock hooks — must appear before component imports
// ---------------------------------------------------------------------------

const mockNavigate = vi.fn();

vi.mock("react-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router")>();
  return {
    ...actual,
    // Keep MemoryRouter + useSearchParams real; only intercept navigation.
    useNavigate: () => mockNavigate,
  };
});

vi.mock("@/hooks/use-entities", () => ({
  useEntityConcentration: vi.fn(),
  // Re-export everything else as passthrough stubs
  useEntityNeighbours: vi.fn(),
  useEntityLinkedContacts: vi.fn(),
  useEntityGifts: vi.fn(),
  useEntityLoans: vi.fn(),
  useEntityTimeline: vi.fn(),
  useEntityMessageThreads: vi.fn(),
  useEntityDates: vi.fn(),
  useEntityFinderSearch: vi.fn(),
  useUpdateEntityDunbarTier: vi.fn(),
  useRelationshipEntities: vi.fn(),
  useRelationshipEntityQueue: vi.fn(),
}));

import { useEntityConcentration } from "@/hooks/use-entities";
import type { ConcentrationResponse } from "@/api/types";
import ConcentrationPage from "./ConcentrationPage";

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const PREDICATE_TABS = [
  { predicate: "knows", label: "Knows", description: null },
  { predicate: "family-of", label: "Family Of", description: "Family relationship" },
  { predicate: "works-with", label: "Works With", description: null },
];

const KNOWS_RESPONSE: ConcentrationResponse = {
  predicate: "knows",
  items: [
    {
      entity_id: "ent-alice-001",
      canonical_name: "Alice Smith",
      weight_sum: 12,
      fact_count: 4,
      share: 0.48,
      last_seen: "2026-05-01T10:00:00Z",
      src: "relationship",
      conf: 1.0,
      verified: false,
      primary: null,
    },
    {
      entity_id: "ent-bob-002",
      canonical_name: "Bob Jones",
      weight_sum: 8,
      fact_count: 2,
      share: 0.32,
      last_seen: "2026-04-15T08:00:00Z",
      src: "relationship",
      conf: 0.9,
      verified: true,
      primary: null,
    },
  ],
  rollup: { total: 25, top3_share: 0.88 },
  predicate_tabs: PREDICATE_TABS,
  total: 2,
};

const FAMILY_OF_RESPONSE: ConcentrationResponse = {
  predicate: "family-of",
  items: [
    {
      entity_id: "ent-carol-003",
      canonical_name: "Carol Doe",
      weight_sum: 5,
      fact_count: 1,
      share: 1.0,
      last_seen: null,
      src: "relationship",
      conf: 1.0,
      verified: false,
      primary: null,
    },
  ],
  rollup: { total: 5, top3_share: 1.0 },
  predicate_tabs: PREDICATE_TABS,
  total: 1,
};

const EMPTY_RESPONSE: ConcentrationResponse = {
  predicate: "knows",
  items: [],
  rollup: { total: 0, top3_share: null },
  predicate_tabs: PREDICATE_TABS,
  total: 0,
};

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
}

// ---------------------------------------------------------------------------
// Render helper
// ---------------------------------------------------------------------------

let container: HTMLDivElement;
let root: Root;

function renderPage(initialEntry = "/entities/concentration") {
  const qc = makeQueryClient();
  act(() => {
    root.render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={[initialEntry]}>
          <ConcentrationPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );
  });
}

beforeEach(() => {
  vi.resetAllMocks();

  // Default: data loaded with knows response
  vi.mocked(useEntityConcentration).mockReturnValue({
    data: KNOWS_RESPONSE,
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  } as unknown as ReturnType<typeof useEntityConcentration>);

  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => {
    root.unmount();
  });
  container.remove();
  document.body.innerHTML = "";
  vi.restoreAllMocks();
});

// ---------------------------------------------------------------------------
// Route mount tests
// ---------------------------------------------------------------------------

describe("ConcentrationPage — route mount", () => {
  it("renders the SubpageTabs nav strip", () => {
    renderPage("/entities/concentration");
    const nav = container.querySelector("nav[aria-label='Entity views']");
    expect(nav).toBeTruthy();
  });

  it("renders Index, Hop, Columns, Concentration, Social map tabs", () => {
    renderPage("/entities/concentration");
    const nav = container.querySelector("nav[aria-label='Entity views']");
    const links = nav?.querySelectorAll("a") ?? [];
    const labels = Array.from(links).map((a) => a.textContent?.trim());
    expect(labels).toContain("Index");
    expect(labels).toContain("Hop");
    expect(labels).toContain("Columns");
    expect(labels).toContain("Concentration");
    expect(labels).toContain("Social map");
  });

  it("renders the page title", () => {
    renderPage("/entities/concentration");
    expect(container.textContent).toContain("Concentration");
  });
});

// ---------------------------------------------------------------------------
// Predicate tab strip tests
// ---------------------------------------------------------------------------

describe("ConcentrationPage — predicate tab strip", () => {
  it("renders predicate tabs from the registry", () => {
    renderPage("/entities/concentration");
    const strip = container.querySelector("[data-testid='predicate-tab-strip']");
    expect(strip).toBeTruthy();

    const tabs = strip?.querySelectorAll("button[data-predicate]") ?? [];
    const predicates = Array.from(tabs).map((b) => b.getAttribute("data-predicate"));
    expect(predicates).toContain("knows");
    expect(predicates).toContain("family-of");
    expect(predicates).toContain("works-with");
  });

  it("marks the active predicate tab with aria-pressed=true", () => {
    renderPage("/entities/concentration?predicate=knows");
    const strip = container.querySelector("[data-testid='predicate-tab-strip']");
    const knowsBtn = strip?.querySelector("[data-predicate='knows']");
    expect(knowsBtn?.getAttribute("aria-pressed")).toBe("true");

    const familyBtn = strip?.querySelector("[data-predicate='family-of']");
    expect(familyBtn?.getAttribute("aria-pressed")).toBe("false");
  });

  it("clicking a tab updates ?predicate= in the URL (hook called with new predicate)", async () => {
    // First render with 'knows'
    vi.mocked(useEntityConcentration).mockReturnValue({
      data: KNOWS_RESPONSE,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useEntityConcentration>);

    renderPage("/entities/concentration?predicate=knows");

    const strip = container.querySelector("[data-testid='predicate-tab-strip']");
    const familyBtn = strip?.querySelector("[data-predicate='family-of']") as HTMLButtonElement | null;
    expect(familyBtn).toBeTruthy();

    // Switch mock to family-of response before click
    vi.mocked(useEntityConcentration).mockReturnValue({
      data: FAMILY_OF_RESPONSE,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useEntityConcentration>);

    await act(async () => {
      familyBtn?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    // Hook should have been called with 'family-of' (after URL param update)
    const calls = vi.mocked(useEntityConcentration).mock.calls;
    const familyCalls = calls.filter((c) => c[0] === "family-of");
    expect(familyCalls.length).toBeGreaterThan(0);
  });
});

// ---------------------------------------------------------------------------
// Entity row rendering tests
// ---------------------------------------------------------------------------

describe("ConcentrationPage — entity rows", () => {
  it("renders concentration rows from items list", () => {
    renderPage("/entities/concentration");
    const rows = container.querySelectorAll("[data-testid='concentration-row']");
    expect(rows.length).toBe(2);
  });

  it("renders entity names in rows", () => {
    renderPage("/entities/concentration");
    expect(container.textContent).toContain("Alice Smith");
    expect(container.textContent).toContain("Bob Jones");
  });

  it("renders weight_sum for each row (bare numeral, no 'w=' prefix)", () => {
    renderPage("/entities/concentration");
    const aliceRow = container.querySelector("[data-entity-id='ent-alice-001']");
    // The 'w=123' text presentation was replaced by a proportional bar; the
    // numeral itself still renders for readout.
    const weightSum = aliceRow?.querySelector("[data-testid='weight-sum']");
    expect(weightSum?.textContent).toBe("12");
    expect(aliceRow?.textContent).not.toContain("w=");
  });

  it("renders a proportional weight bar per row (bar of half-weight is half-width)", () => {
    renderPage("/entities/concentration");
    // Alice weight_sum=12 (max), Bob weight_sum=8.
    const bars = container.querySelectorAll("[data-testid='weight-bar']");
    expect(bars.length).toBe(2);
    // Alice is the max → 100%; Bob is 8/12 ≈ 67%.
    const aliceFill = bars[0].querySelector("span") as HTMLElement | null;
    const bobFill = bars[1].querySelector("span") as HTMLElement | null;
    expect(aliceFill?.style.width).toBe("100%");
    expect(bobFill?.style.width).toBe("67%");
  });

  it("navigates to the entity detail page when a row is clicked", async () => {
    renderPage("/entities/concentration");
    const aliceRow = container.querySelector("[data-entity-id='ent-alice-001']");
    const btn = aliceRow?.querySelector("button") as HTMLButtonElement | null;
    expect(btn).toBeTruthy();
    await act(async () => {
      btn?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(mockNavigate).toHaveBeenCalledWith("/entities/ent-alice-001");
  });

  it("renders share badge for each row", () => {
    renderPage("/entities/concentration");
    const badges = container.querySelectorAll("[data-testid='share-badge']");
    expect(badges.length).toBeGreaterThan(0);
    // Alice: 48.0% share
    expect(badges[0]?.textContent).toContain("48.0%");
  });

  it("renders src and verified provenance marks per row", () => {
    // Spec ("Provenance rendering in the UI"): each Concentration row carries
    // its `src` and `verified` marks.
    renderPage("/entities/concentration");
    const marks = container.querySelectorAll(
      "[data-testid='concentration-provenance']",
    );
    expect(marks.length).toBe(2);
    // Alice: src=relationship, verified=false → unverified mark.
    const alice = container.querySelector("[data-entity-id='ent-alice-001']");
    const aliceMark = alice?.querySelector(
      "[data-testid='concentration-provenance']",
    );
    expect(aliceMark?.textContent).toContain("relationship");
    expect(
      aliceMark?.querySelector("[data-verified='false']"),
    ).toBeTruthy();
    // Bob: verified=true → verified mark.
    const bob = container.querySelector("[data-entity-id='ent-bob-002']");
    const bobMark = bob?.querySelector(
      "[data-testid='concentration-provenance']",
    );
    expect(bobMark?.querySelector("[data-verified='true']")).toBeTruthy();
  });

  it("applies a stale dim treatment on last_seen for stale rows", () => {
    // Spec: "a staleness dim treatment on `last_seen`". A row last seen well
    // over the stale threshold (>180 days) receives the dim treatment; a
    // recently-seen row does not.
    const STALE_RESPONSE: ConcentrationResponse = {
      ...KNOWS_RESPONSE,
      items: [
        { ...KNOWS_RESPONSE.items[0], last_seen: "2020-01-01T00:00:00Z" },
        { ...KNOWS_RESPONSE.items[1], last_seen: new Date().toISOString() },
      ],
    };
    vi.mocked(useEntityConcentration).mockReturnValue({
      data: STALE_RESPONSE,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useEntityConcentration>);

    renderPage("/entities/concentration");
    const staleRow = container.querySelector("[data-entity-id='ent-alice-001']");
    expect(staleRow?.querySelector("[data-stale='true']")).toBeTruthy();
    const freshRow = container.querySelector("[data-entity-id='ent-bob-002']");
    expect(freshRow?.querySelector("[data-stale='true']")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Rollup header tests
// ---------------------------------------------------------------------------

describe("ConcentrationPage — rollup header", () => {
  it("renders the rollup header with total and top-3 share", () => {
    renderPage("/entities/concentration");
    const rollup = container.querySelector("[data-testid='rollup-header']");
    expect(rollup).toBeTruthy();
    expect(rollup?.textContent).toContain("25");   // total
    expect(rollup?.textContent).toContain("88.0%"); // top3_share
  });

  it("omits top-3 share when null", () => {
    vi.mocked(useEntityConcentration).mockReturnValue({
      data: EMPTY_RESPONSE,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useEntityConcentration>);

    renderPage("/entities/concentration");
    const rollup = container.querySelector("[data-testid='rollup-header']");
    expect(rollup?.textContent).not.toContain("Top-3 share");
  });
});

// ---------------------------------------------------------------------------
// Footer KPI strip tests
// ---------------------------------------------------------------------------

describe("ConcentrationPage — footer KPI strip", () => {
  it("renders the KPI strip with total touches, entity count, and top entity", () => {
    renderPage("/entities/concentration");
    const strip = container.querySelector("[data-testid='concentration-kpi-strip']");
    expect(strip).toBeTruthy();
    // total touches = rollup.total (25), entities = total (2), top entity = Alice.
    expect(strip?.textContent).toContain("25");
    expect(strip?.textContent).toContain("Alice Smith");
  });

  it("computes the tail-<1% share from the items", () => {
    // Add a long-tail entity below the 1% threshold.
    const withTail: ConcentrationResponse = {
      ...KNOWS_RESPONSE,
      items: [
        ...KNOWS_RESPONSE.items,
        {
          entity_id: "ent-tail-009",
          canonical_name: "Tail Entity",
          weight_sum: 1,
          fact_count: 1,
          share: 0.005, // 0.5% < 1% threshold
          last_seen: null,
          src: "relationship",
          conf: 1.0,
          verified: false,
          primary: null,
        },
      ],
      total: 3,
    };
    vi.mocked(useEntityConcentration).mockReturnValue({
      data: withTail,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useEntityConcentration>);

    renderPage("/entities/concentration");
    const strip = container.querySelector("[data-testid='concentration-kpi-strip']");
    // Only the 0.5% entity is below threshold → tail share 0.5%.
    expect(strip?.textContent).toContain("0.5%");
  });

  it("does not render the KPI strip when there are no items", () => {
    vi.mocked(useEntityConcentration).mockReturnValue({
      data: EMPTY_RESPONSE,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useEntityConcentration>);

    renderPage("/entities/concentration");
    expect(container.querySelector("[data-testid='concentration-kpi-strip']")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// URL round-trip test
// ---------------------------------------------------------------------------

describe("ConcentrationPage — URL round-trip", () => {
  it("calls useEntityConcentration with the predicate from ?predicate= param", () => {
    renderPage("/entities/concentration?predicate=family-of");
    const calls = vi.mocked(useEntityConcentration).mock.calls;
    expect(calls.some((c) => c[0] === "family-of")).toBe(true);
  });

  it("calls useEntityConcentration with empty string when no ?predicate= param", () => {
    renderPage("/entities/concentration");
    const calls = vi.mocked(useEntityConcentration).mock.calls;
    // Empty string is passed when no param; hook treats undefined/empty → backend defaults
    expect(calls.some((c) => c[0] === "" || c[0] == null || c[0] === undefined)).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Loading state tests
// ---------------------------------------------------------------------------

describe("ConcentrationPage — loading state", () => {
  it("renders skeleton placeholders while loading", () => {
    vi.mocked(useEntityConcentration).mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useEntityConcentration>);

    renderPage("/entities/concentration");

    const loading = container.querySelector("[data-testid='concentration-loading']");
    expect(loading).toBeTruthy();
    const panel = container.querySelector("[data-testid='concentration-panel']");
    expect(panel).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Empty state tests
// ---------------------------------------------------------------------------

describe("ConcentrationPage — empty state", () => {
  it("renders empty state when items list is empty", () => {
    vi.mocked(useEntityConcentration).mockReturnValue({
      data: EMPTY_RESPONSE,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useEntityConcentration>);

    renderPage("/entities/concentration");

    const list = container.querySelector("[data-testid='concentration-list']");
    expect(list).toBeNull();
    expect(container.textContent).toContain("No entities yet.");
  });
});

// ---------------------------------------------------------------------------
// Error state tests
// ---------------------------------------------------------------------------

describe("ConcentrationPage — error state", () => {
  it("renders error state with retry button on fetch failure", () => {
    vi.mocked(useEntityConcentration).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("403 Forbidden"),
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useEntityConcentration>);

    renderPage("/entities/concentration");

    const errorDiv = container.querySelector("[data-testid='concentration-error']");
    expect(errorDiv).toBeTruthy();
    expect(container.textContent).toContain("Could not load concentration data");
    const retryBtn = errorDiv?.querySelector("button");
    expect(retryBtn?.textContent).toContain("Retry");
  });
});
