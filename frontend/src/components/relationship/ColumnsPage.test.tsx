// @vitest-environment jsdom
/**
 * Component tests for ColumnsPage (§8.3).
 *
 * Covers:
 * - Route mount renders SubpageTabs (Columns tab present)
 * - Anchor column renders from ?path= param
 * - Owner fallback renders column 0 from owner entity when no ?path= given
 * - Clicking a neighbour appends a new column (URL state round-trip)
 * - Empty cascade state (no owner, no ?path=)
 * - Empty neighbours state within a column
 * - Loading state shows skeleton placeholders
 * - Reset button clears ?path= when a non-default path is active
 * - URL state round-trips: ?path=a,b renders two columns
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

// ---------------------------------------------------------------------------
// Mock hooks and queries — must appear before component imports
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-entities", () => ({
  useEntityNeighbours: vi.fn(),
  // Re-export everything else as passthrough stubs
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

vi.mock("@/api/index", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/api/index")>();
  return {
    ...original,
    getOwnerSetupStatus: vi.fn(),
  };
});

const mockNavigate = vi.fn();

// Keep MemoryRouter + useSearchParams real; only intercept navigation so the
// Enter-to-open-detail key can be asserted.
vi.mock("react-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router")>();
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

// Mock useQuery so we can control loading/data/error state for the owner
// status query directly (avoids async React-Query resolution complexity).
vi.mock("@tanstack/react-query", async (importOriginal) => {
  const original = await importOriginal<typeof import("@tanstack/react-query")>();
  return {
    ...original,
    useQuery: vi.fn(),
  };
});

import { useEntityNeighbours } from "@/hooks/use-entities";
import { useQuery } from "@tanstack/react-query";
import type { NeighboursResponse, OwnerSetupStatus } from "@/api/types";
import ColumnsPage from "./ColumnsPage";

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const OWNER_ENTITY_ID = "owner-uuid-001";
const BOB_ENTITY_ID = "ent-bob-002";
const CAROL_ENTITY_ID = "ent-carol-003";

const KNOWS_NEIGHBOURS: NeighboursResponse = {
  neighbours: {
    knows: [
      {
        entity_id: BOB_ENTITY_ID,
        canonical_name: "Bob Friend",
        direction: "forward",
        src: "relationship",
        conf: 1.0,
        last_seen: "2026-05-01T10:00:00Z",
        weight: null,
        verified: false,
        primary: null,
      },
    ],
    "family-of": [
      {
        entity_id: CAROL_ENTITY_ID,
        canonical_name: "Carol Danvers",
        direction: "reverse",
        src: "relationship",
        conf: 0.9,
        last_seen: null,
        weight: 2,
        verified: true,
        primary: null,
      },
    ],
  },
  remainders: {},
};

const EMPTY_NEIGHBOURS: NeighboursResponse = { neighbours: {}, remainders: {} };

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

function renderPage(initialEntry = "/entities/columns") {
  const qc = makeQueryClient();
  act(() => {
    root.render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={[initialEntry]}>
          <ColumnsPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );
  });
}

/** Helper to create a mocked useQuery return value for owner status. */
function mockOwnerQuery(overrides?: Partial<{ entity_id: string | null; isLoading: boolean }>) {
  const entityId = overrides?.entity_id !== undefined ? overrides.entity_id : OWNER_ENTITY_ID;
  const isLoading = overrides?.isLoading ?? false;
  vi.mocked(useQuery).mockImplementation(({ queryKey }: { queryKey: readonly unknown[] }) => {
    if (queryKey[0] === "owner-setup-status") {
      return {
        data: isLoading ? undefined : ({ entity_id: entityId } as OwnerSetupStatus),
        isLoading,
        error: null,
        isError: false,
        refetch: vi.fn(),
      } as unknown as ReturnType<typeof useQuery>;
    }
    // Passthrough for other useQuery calls (shouldn't happen in ColumnsPage)
    return {
      data: undefined,
      isLoading: false,
      error: null,
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useQuery>;
  });
}

beforeEach(() => {
  vi.resetAllMocks();

  // Default: owner already resolved with entity_id (synchronous, no loading state).
  mockOwnerQuery();

  vi.mocked(useEntityNeighbours).mockReturnValue({
    data: KNOWS_NEIGHBOURS,
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  } as unknown as ReturnType<typeof useEntityNeighbours>);

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

describe("ColumnsPage — route mount", () => {
  it("renders the SubpageTabs nav strip", () => {
    renderPage("/entities/columns?path=" + OWNER_ENTITY_ID);
    const nav = container.querySelector("nav[aria-label='Entity views']");
    expect(nav).toBeTruthy();
  });

  it("renders Index, Hop, Columns, Concentration, Social map tabs", () => {
    renderPage("/entities/columns?path=" + OWNER_ENTITY_ID);
    const nav = container.querySelector("nav[aria-label='Entity views']");
    const links = nav?.querySelectorAll("a") ?? [];
    const labels = Array.from(links).map((a) => a.textContent?.trim());
    expect(labels).toContain("Index");
    expect(labels).toContain("Hop");
    expect(labels).toContain("Columns");
    expect(labels).toContain("Concentration");
    expect(labels).toContain("Social map");
  });

  it("renders the cascading column container", () => {
    renderPage("/entities/columns?path=" + OWNER_ENTITY_ID);
    const cascade = container.querySelector("[data-testid='columns-cascade']");
    expect(cascade).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// Anchor column rendering
// ---------------------------------------------------------------------------

describe("ColumnsPage — anchor column", () => {
  it("renders column 0 when ?path= is provided with a single entity ID", () => {
    renderPage("/entities/columns?path=" + OWNER_ENTITY_ID);
    const col0 = container.querySelector("[data-testid='column-panel-0']");
    expect(col0).toBeTruthy();
  });

  it("renders predicate groups inside column 0", () => {
    renderPage("/entities/columns?path=" + OWNER_ENTITY_ID);
    const neighbours0 = container.querySelector("[data-testid='column-neighbours-0']");
    expect(neighbours0).toBeTruthy();
    const knowsGroup = container.querySelector(
      "[data-testid='column-predicate-group-0-knows']",
    );
    expect(knowsGroup).toBeTruthy();
  });

  it("renders neighbour rows inside column 0", () => {
    renderPage("/entities/columns?path=" + OWNER_ENTITY_ID);
    const rows = container.querySelectorAll("[data-testid='column-neighbour-row-0']");
    // KNOWS_NEIGHBOURS has 2 neighbours across two predicates
    expect(rows.length).toBe(2);
  });
});

// ---------------------------------------------------------------------------
// URL state round-trips
// ---------------------------------------------------------------------------

describe("ColumnsPage — URL state round-trips", () => {
  it("renders two columns when ?path=a,b is provided", () => {
    vi.mocked(useEntityNeighbours).mockReturnValue({
      data: KNOWS_NEIGHBOURS,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useEntityNeighbours>);

    renderPage(`/entities/columns?path=${OWNER_ENTITY_ID},${BOB_ENTITY_ID}`);

    const col0 = container.querySelector("[data-testid='column-panel-0']");
    const col1 = container.querySelector("[data-testid='column-panel-1']");
    expect(col0).toBeTruthy();
    expect(col1).toBeTruthy();
  });

  it("renders three columns when ?path=a,b,c is provided", () => {
    vi.mocked(useEntityNeighbours).mockReturnValue({
      data: KNOWS_NEIGHBOURS,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useEntityNeighbours>);

    renderPage(`/entities/columns?path=${OWNER_ENTITY_ID},${BOB_ENTITY_ID},${CAROL_ENTITY_ID}`);

    const col0 = container.querySelector("[data-testid='column-panel-0']");
    const col1 = container.querySelector("[data-testid='column-panel-1']");
    const col2 = container.querySelector("[data-testid='column-panel-2']");
    expect(col0).toBeTruthy();
    expect(col1).toBeTruthy();
    expect(col2).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// Click neighbour appends column
// ---------------------------------------------------------------------------

describe("ColumnsPage — click neighbour appends column", () => {
  it("calls useEntityNeighbours with a new entity ID after clicking a neighbour", async () => {
    vi.mocked(useEntityNeighbours).mockReturnValue({
      data: KNOWS_NEIGHBOURS,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useEntityNeighbours>);

    renderPage("/entities/columns?path=" + OWNER_ENTITY_ID);

    // Find a neighbour button for Bob
    const neighbourBtn = container.querySelector(
      `[data-entity-id='${BOB_ENTITY_ID}']`,
    ) as HTMLButtonElement | null;
    expect(neighbourBtn).toBeTruthy();

    await act(async () => {
      neighbourBtn?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    // After click, useEntityNeighbours should have been called with BOB_ENTITY_ID
    const calls = vi.mocked(useEntityNeighbours).mock.calls;
    const bobCall = calls.find((c) => c[0] === BOB_ENTITY_ID);
    expect(bobCall).toBeTruthy();
  });

  it("clicking a neighbour in column 0 when three columns are visible truncates to two columns", async () => {
    // Three-column path: owner → bob → carol
    vi.mocked(useEntityNeighbours).mockReturnValue({
      data: KNOWS_NEIGHBOURS,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useEntityNeighbours>);

    renderPage(
      `/entities/columns?path=${OWNER_ENTITY_ID},${BOB_ENTITY_ID},${CAROL_ENTITY_ID}`,
    );

    // Column 0 shows owner's neighbours (Bob and Carol in KNOWS_NEIGHBOURS).
    // Click Carol in column 0 — should truncate to [owner, carol] (not append carol to [owner,bob,carol]).
    const carolBtns = container.querySelectorAll(
      `[data-entity-id='${CAROL_ENTITY_ID}'][data-column-index='0']`,
    ) as NodeListOf<HTMLButtonElement>;
    expect(carolBtns.length).toBeGreaterThan(0);

    await act(async () => {
      carolBtns[0].dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    // After click, useEntityNeighbours should have been called for carol (new column 1).
    const calls = vi.mocked(useEntityNeighbours).mock.calls;
    const carolCall = calls.find((c) => c[0] === CAROL_ENTITY_ID);
    expect(carolCall).toBeTruthy();

    // Column 2 (previously carol) should have been removed — only two column panels.
    const col2 = container.querySelector("[data-testid='column-panel-2']");
    expect(col2).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Empty cascade state (no owner, no ?path=)
// ---------------------------------------------------------------------------

describe("ColumnsPage — empty cascade state", () => {
  it("shows loading state while resolving owner when no ?path= param", () => {
    // Simulate owner status query still in-flight (isLoading=true)
    mockOwnerQuery({ isLoading: true });

    vi.mocked(useEntityNeighbours).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useEntityNeighbours>);

    renderPage("/entities/columns");

    // Page loading skeleton should be shown
    const loadingRegion = container.querySelector("[role='status'][aria-label='Loading']");
    expect(loadingRegion).toBeTruthy();
  });

  it("shows empty anchor state when owner resolves to null entity_id", () => {
    // Owner resolved but entity_id is null
    mockOwnerQuery({ entity_id: null, isLoading: false });

    vi.mocked(useEntityNeighbours).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useEntityNeighbours>);

    renderPage("/entities/columns");

    const emptyAnchor = container.querySelector("[data-testid='columns-no-anchor']");
    expect(emptyAnchor).toBeTruthy();
    expect(container.textContent).toContain("No anchor entity found.");
  });
});

// ---------------------------------------------------------------------------
// Empty neighbours state
// ---------------------------------------------------------------------------

describe("ColumnsPage — empty neighbours within column", () => {
  it("shows empty state inside column when neighbours object is empty", () => {
    vi.mocked(useEntityNeighbours).mockReturnValue({
      data: EMPTY_NEIGHBOURS,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useEntityNeighbours>);

    renderPage("/entities/columns?path=" + OWNER_ENTITY_ID);

    const colEmpty = container.querySelector("[data-testid='column-empty-0']");
    expect(colEmpty).toBeTruthy();
    const panel = container.querySelector("[data-testid='column-neighbours-0']");
    expect(panel).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Loading state
// ---------------------------------------------------------------------------

describe("ColumnsPage — loading state", () => {
  it("shows skeleton placeholders while column neighbours are fetching", () => {
    vi.mocked(useEntityNeighbours).mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useEntityNeighbours>);

    renderPage("/entities/columns?path=" + OWNER_ENTITY_ID);

    const loadingDiv = container.querySelector("[data-testid='column-loading-0']");
    expect(loadingDiv).toBeTruthy();
    const neighbours = container.querySelector("[data-testid='column-neighbours-0']");
    expect(neighbours).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Owner fallback
// ---------------------------------------------------------------------------

describe("ColumnsPage — owner fallback", () => {
  it("calls useEntityNeighbours with owner entity_id when no ?path= provided", () => {
    // Owner is already resolved (isLoading=false, entity_id=OWNER_ENTITY_ID)
    mockOwnerQuery({ entity_id: OWNER_ENTITY_ID, isLoading: false });

    vi.mocked(useEntityNeighbours).mockReturnValue({
      data: KNOWS_NEIGHBOURS,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useEntityNeighbours>);

    renderPage("/entities/columns");

    // useEntityNeighbours should have been called with the owner entity ID
    const calls = vi.mocked(useEntityNeighbours).mock.calls;
    const ownerCall = calls.find((c) => c[0] === OWNER_ENTITY_ID);
    expect(ownerCall).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// Ranked truncation request
// ---------------------------------------------------------------------------

describe("ColumnsPage — ranked truncation", () => {
  it("requests ranked neighbours (rank=weight, per_predicate=6)", () => {
    renderPage("/entities/columns?path=" + OWNER_ENTITY_ID);
    const calls = vi.mocked(useEntityNeighbours).mock.calls;
    const ranked = calls.find((c) => c[1]?.rank === "weight" && c[1]?.per_predicate === 6);
    expect(ranked).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// Keyboard map (view-local)
// ---------------------------------------------------------------------------

describe("ColumnsPage — keyboard map", () => {
  function getCascade() {
    return container.querySelector("[data-testid='columns-cascade']") as HTMLElement | null;
  }

  it("the cascade is focusable (tabIndex) so the map is view-local", () => {
    renderPage("/entities/columns?path=" + OWNER_ENTITY_ID);
    expect(getCascade()?.getAttribute("tabindex")).toBe("0");
  });

  it("ArrowRight deepens — opens a new column for the cursored neighbour", async () => {
    renderPage("/entities/columns?path=" + OWNER_ENTITY_ID);
    const cascade = getCascade();
    // flatEntries are predicate-sorted: row 0 = Carol (family-of).
    // ArrowRight appends a column for Carol.
    await act(async () => {
      cascade?.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }));
    });
    const calls = vi.mocked(useEntityNeighbours).mock.calls;
    expect(calls.find((c) => c[0] === CAROL_ENTITY_ID)).toBeTruthy();
  });

  it("ArrowDown then ArrowRight deepens on the moved cursor (Bob)", async () => {
    renderPage("/entities/columns?path=" + OWNER_ENTITY_ID);
    const cascade = getCascade();
    await act(async () => {
      cascade?.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true }));
    });
    await act(async () => {
      cascade?.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }));
    });
    const calls = vi.mocked(useEntityNeighbours).mock.calls;
    expect(calls.find((c) => c[0] === BOB_ENTITY_ID)).toBeTruthy();
  });

  it("Enter opens the cursored neighbour's detail page", async () => {
    renderPage("/entities/columns?path=" + OWNER_ENTITY_ID);
    const cascade = getCascade();
    // Cursor row 0 = Carol (family-of sorts first).
    await act(async () => {
      cascade?.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    });
    expect(mockNavigate).toHaveBeenCalledWith(`/entities/${CAROL_ENTITY_ID}`);
  });

  it("ArrowLeft pops the rightmost column", async () => {
    renderPage(`/entities/columns?path=${OWNER_ENTITY_ID},${BOB_ENTITY_ID}`);
    expect(container.querySelector("[data-testid='column-panel-1']")).toBeTruthy();
    const cascade = getCascade();
    await act(async () => {
      cascade?.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowLeft", bubbles: true }));
    });
    // The second column is removed.
    expect(container.querySelector("[data-testid='column-panel-1']")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Reset button
// ---------------------------------------------------------------------------

describe("ColumnsPage — reset path button", () => {
  it("renders 'Reset to owner' button when ?path= is set", () => {
    renderPage(`/entities/columns?path=${OWNER_ENTITY_ID},${BOB_ENTITY_ID}`);
    const clearBtn = container.querySelector("[data-testid='clear-path-btn']");
    expect(clearBtn).toBeTruthy();
    expect(clearBtn?.textContent).toContain("Reset to owner");
  });

  it("does not render 'Reset to owner' button when no ?path= is set", () => {
    // No ?path= in URL — no reset button should appear
    mockOwnerQuery({ isLoading: true }); // owner still loading
    renderPage("/entities/columns");
    const clearBtn = container.querySelector("[data-testid='clear-path-btn']");
    expect(clearBtn).toBeNull();
  });
});
