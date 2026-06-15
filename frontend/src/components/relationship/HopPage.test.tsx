// @vitest-environment jsdom
/**
 * Component tests for HopPage (§8.2).
 *
 * Covers:
 * - Route mounts at /entities/hop (SubpageTabs rendered, Hop tab active)
 * - Empty state when owner resolution returns null entity_id
 * - Anchor card renders with centerId from ?center= param
 * - Neighbour list renders predicate groups
 * - Re-centre interaction updates ?center= query param
 * - Empty neighbours state when API returns empty neighbours map
 * - Loading state shows skeleton placeholders
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
    getRelationshipEntity: vi.fn(),
  };
});

import { useEntityNeighbours } from "@/hooks/use-entities";
import { getOwnerSetupStatus, getRelationshipEntity } from "@/api/index";
import type { NeighboursResponse, RelationshipEntityDetail } from "@/api/types";
import HopPage from "./HopPage";

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const OWNER_ENTITY: RelationshipEntityDetail = {
  id: "owner-uuid-001",
  canonical_name: "Alice Owner",
  entity_type: "person",
  aliases: [],
  roles: ["owner"],
  metadata: {},
  created_at: "2025-01-01T00:00:00Z",
  updated_at: "2025-01-01T00:00:00Z",
  state: "healthy",
  state_evidence: null,
  entity_info: [],
};

const BOB_ENTITY: RelationshipEntityDetail = {
  id: "ent-bob-002",
  canonical_name: "Bob Friend",
  entity_type: "person",
  aliases: ["Bobby"],
  roles: [],
  metadata: {},
  created_at: "2025-01-01T00:00:00Z",
  updated_at: "2025-01-01T00:00:00Z",
  state: "healthy",
  state_evidence: null,
  entity_info: [],
};

const KNOWS_NEIGHBOURS: NeighboursResponse = {
  neighbours: {
    knows: [
      {
        entity_id: "ent-bob-002",
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
        entity_id: "ent-carol-003",
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

function renderPage(initialEntry = "/entities/hop") {
  const qc = makeQueryClient();
  act(() => {
    root.render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={[initialEntry]}>
          <HopPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );
  });
}

beforeEach(() => {
  vi.resetAllMocks();

  // Default: owner has entity_id, hook returns loading
  vi.mocked(getOwnerSetupStatus).mockResolvedValue({
    entity_id: "owner-uuid-001",
    has_name: true,
    has_telegram: false,
    has_telegram_chat_id: false,
    has_email: true,
  });

  vi.mocked(getRelationshipEntity).mockResolvedValue(OWNER_ENTITY);

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

describe("HopPage — route mount", () => {
  it("renders the SubpageTabs nav strip", () => {
    renderPage("/entities/hop?center=owner-uuid-001");
    const nav = container.querySelector("nav[aria-label='Entity views']");
    expect(nav).toBeTruthy();
  });

  it("renders Index, Hop, Columns, Concentration, Social map tabs", () => {
    renderPage("/entities/hop?center=owner-uuid-001");
    const nav = container.querySelector("nav[aria-label='Entity views']");
    const links = nav?.querySelectorAll("a") ?? [];
    const labels = Array.from(links).map((a) => a.textContent?.trim());
    expect(labels).toContain("Index");
    expect(labels).toContain("Hop");
    expect(labels).toContain("Columns");
    expect(labels).toContain("Concentration");
    expect(labels).toContain("Social map");
  });
});

// ---------------------------------------------------------------------------
// Anchor card tests
// ---------------------------------------------------------------------------

describe("HopPage — anchor card", () => {
  it("renders anchor card when ?center= is provided", () => {
    vi.mocked(getRelationshipEntity).mockResolvedValue(BOB_ENTITY);
    renderPage("/entities/hop?center=ent-bob-002");

    // The anchor card container is present (may be loading or loaded)
    // We check for loading state since getRelationshipEntity is async
    const loadingCard = container.querySelector("[data-testid='anchor-card-loading']");
    // Either loading or loaded card should be present
    const anchorCard = container.querySelector("[data-testid='anchor-card']");
    expect(loadingCard != null || anchorCard != null).toBe(true);
  });

  it("renders 'Reset to owner' back button when ?center= is set", () => {
    renderPage("/entities/hop?center=ent-bob-002");
    const clearBtn = container.querySelector("[data-testid='clear-center-btn']");
    expect(clearBtn).toBeTruthy();
    expect(clearBtn?.textContent).toContain("Reset to owner");
  });

  it("does not render 'Reset to owner' button when no ?center= is set", () => {
    // getOwnerSetupStatus is still resolving (async), so no center yet
    vi.mocked(getOwnerSetupStatus).mockReturnValue(new Promise(() => {})); // never resolves
    renderPage("/entities/hop");
    const clearBtn = container.querySelector("[data-testid='clear-center-btn']");
    expect(clearBtn).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Neighbour list rendering tests
// ---------------------------------------------------------------------------

describe("HopPage — neighbour list", () => {
  it("renders predicate groups from the neighbours API", () => {
    vi.mocked(useEntityNeighbours).mockReturnValue({
      data: KNOWS_NEIGHBOURS,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useEntityNeighbours>);

    renderPage("/entities/hop?center=owner-uuid-001");

    const panel = container.querySelector("[data-testid='neighbours-panel']");
    expect(panel).toBeTruthy();

    // Both predicate groups should render
    const knowsGroup = container.querySelector("[data-testid='predicate-group-knows']");
    expect(knowsGroup).toBeTruthy();

    const familyGroup = container.querySelector("[data-testid='predicate-group-family-of']");
    expect(familyGroup).toBeTruthy();
  });

  it("renders neighbour rows inside each predicate group", () => {
    vi.mocked(useEntityNeighbours).mockReturnValue({
      data: KNOWS_NEIGHBOURS,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useEntityNeighbours>);

    renderPage("/entities/hop?center=owner-uuid-001");

    const rows = container.querySelectorAll("[data-testid='neighbour-row']");
    expect(rows.length).toBe(2); // one in knows, one in family-of
  });

  it("shows loading skeletons while neighbours are fetching", () => {
    vi.mocked(useEntityNeighbours).mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useEntityNeighbours>);

    renderPage("/entities/hop?center=owner-uuid-001");

    const loadingDiv = container.querySelector("[data-testid='neighbours-loading']");
    expect(loadingDiv).toBeTruthy();
    const panel = container.querySelector("[data-testid='neighbours-panel']");
    expect(panel).toBeNull();
  });

  it("shows empty state when neighbours object is empty", () => {
    vi.mocked(useEntityNeighbours).mockReturnValue({
      data: EMPTY_NEIGHBOURS,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useEntityNeighbours>);

    renderPage("/entities/hop?center=owner-uuid-001");

    const panel = container.querySelector("[data-testid='neighbours-panel']");
    expect(panel).toBeNull();
    expect(container.textContent).toContain("No neighbours yet.");
  });

  it("requests ranked neighbours (rank=weight, per_predicate=6) for the +N more affordance", () => {
    renderPage("/entities/hop?center=owner-uuid-001");
    const calls = vi.mocked(useEntityNeighbours).mock.calls;
    const ranked = calls.find((c) => c[1]?.rank === "weight" && c[1]?.per_predicate === 6);
    expect(ranked).toBeTruthy();
  });

  it("renders '+N more' from the neighbours remainders map", () => {
    vi.mocked(useEntityNeighbours).mockReturnValue({
      data: { ...KNOWS_NEIGHBOURS, remainders: { knows: 34 } },
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useEntityNeighbours>);

    renderPage("/entities/hop?center=owner-uuid-001");

    const more = container.querySelector("[data-testid='predicate-more-knows']");
    expect(more).toBeTruthy();
    expect(more?.textContent).toContain("+34 more");
    // family-of has no remainder → no affordance.
    expect(container.querySelector("[data-testid='predicate-more-family-of']")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Re-centre interaction test
// ---------------------------------------------------------------------------

describe("HopPage — re-centre interaction", () => {
  it("updates ?center= when a neighbour button is clicked", async () => {
    vi.mocked(useEntityNeighbours).mockReturnValue({
      data: KNOWS_NEIGHBOURS,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useEntityNeighbours>);

    renderPage("/entities/hop?center=owner-uuid-001");

    // Find a neighbour button by its data-entity-id attribute
    const neighbourBtn = container.querySelector(
      "[data-entity-id='ent-bob-002']",
    ) as HTMLButtonElement | null;

    expect(neighbourBtn).toBeTruthy();

    await act(async () => {
      neighbourBtn?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    // After click, useEntityNeighbours should have been called with the new entity ID
    // The URL update is handled by setSearchParams inside MemoryRouter —
    // we verify by checking the hook was re-called (the component re-renders).
    // Since the call signature changes, the mock will record the new entityId.
    const calls = vi.mocked(useEntityNeighbours).mock.calls;
    // At least one call should have been made with the new entity ID
    const recentredCall = calls.find((c) => c[0] === "ent-bob-002");
    expect(recentredCall).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// Empty state — no owner registered
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Breadcrumb trail
// ---------------------------------------------------------------------------

describe("HopPage — breadcrumb trail", () => {
  it("does not render a trail at depth 0 (no ?trail=)", () => {
    renderPage("/entities/hop?center=owner-uuid-001");
    expect(container.querySelector("[data-testid='hop-trail']")).toBeNull();
  });

  it("renders the trail with clickable past segments + a current segment", () => {
    // owner › A › (current B)
    renderPage("/entities/hop?center=ent-bob-002&trail=owner-uuid-001,ent-a-009");
    const trail = container.querySelector("[data-testid='hop-trail']");
    expect(trail).toBeTruthy();
    // Two past segments are links.
    expect(trail?.querySelector("[data-testid='hop-trail-segment-0']")).toBeTruthy();
    expect(trail?.querySelector("[data-testid='hop-trail-segment-1']")).toBeTruthy();
    // The current segment is not a link button.
    const current = trail?.querySelector("[data-testid='hop-trail-current']");
    expect(current).toBeTruthy();
    expect(current?.tagName).not.toBe("BUTTON");
  });

  it("shows the reset pill only at depth > 1", () => {
    // depth 1 → no reset pill
    renderPage("/entities/hop?center=ent-a-009&trail=owner-uuid-001");
    expect(container.querySelector("[data-testid='hop-trail-reset']")).toBeNull();
  });

  it("shows the reset pill at depth 2", () => {
    renderPage("/entities/hop?center=ent-bob-002&trail=owner-uuid-001,ent-a-009");
    expect(container.querySelector("[data-testid='hop-trail-reset']")).toBeTruthy();
  });

  it("pushes the leaving centre onto the trail when re-centring via detail pane", async () => {
    renderPage("/entities/hop?center=owner-uuid-001");
    // Step 1: click a neighbour to open the detail pane (no re-centre yet).
    const neighbourBtn = container.querySelector(
      "[data-entity-id='ent-bob-002']",
    ) as HTMLButtonElement | null;
    expect(neighbourBtn).toBeTruthy();
    await act(async () => {
      neighbourBtn?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    // Pane is open, but no trail yet.
    expect(container.querySelector("[data-testid='neighbour-detail-pane']")).toBeTruthy();
    expect(container.querySelector("[data-testid='hop-trail']")).toBeNull();

    // Step 2: click "Go to this entity" in the detail pane to actually re-centre.
    const recentreBtn = container.querySelector(
      "[data-testid='detail-pane-recentre-btn']",
    ) as HTMLButtonElement | null;
    expect(recentreBtn).toBeTruthy();
    await act(async () => {
      recentreBtn?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    // After re-centring, the hook is queried for the new centre, and the trail
    // now holds the previous owner centre (rendered as a clickable segment).
    const calls = vi.mocked(useEntityNeighbours).mock.calls;
    expect(calls.find((c) => c[0] === "ent-bob-002")).toBeTruthy();
    const segment = container.querySelector("[data-testid='hop-trail-segment-0']");
    expect(segment?.getAttribute("data-entity-id")).toBe("owner-uuid-001");
  });
});

// ---------------------------------------------------------------------------
// Keyboard map (view-local)
// ---------------------------------------------------------------------------

describe("HopPage — keyboard map", () => {
  function getPane() {
    return container.querySelector("[data-testid='neighbours-panel']") as HTMLElement | null;
  }

  it("the relations pane is focusable (tabIndex) and a listbox", () => {
    renderPage("/entities/hop?center=owner-uuid-001");
    const pane = getPane();
    expect(pane?.getAttribute("tabindex")).toBe("0");
    expect(pane?.getAttribute("role")).toBe("listbox");
  });

  it("ArrowDown then Enter re-centres on the cursored neighbour", async () => {
    renderPage("/entities/hop?center=owner-uuid-001");
    const pane = getPane();
    // flatEntries are predicate-sorted: row 0 = Carol (family-of),
    // row 1 = Bob (knows). ArrowDown moves to Bob; Enter re-centres on Bob.
    await act(async () => {
      pane?.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true }));
    });
    await act(async () => {
      pane?.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    });
    const calls = vi.mocked(useEntityNeighbours).mock.calls;
    expect(calls.find((c) => c[0] === "ent-bob-002")).toBeTruthy();
  });

  it("'r' resets to the owner anchor (clears ?center=)", async () => {
    renderPage("/entities/hop?center=ent-bob-002&trail=owner-uuid-001,ent-a-009");
    const pane = getPane();
    await act(async () => {
      pane?.dispatchEvent(new KeyboardEvent("keydown", { key: "r", bubbles: true }));
    });
    // Reset clears the trail → no trail rendered any more.
    expect(container.querySelector("[data-testid='hop-trail']")).toBeNull();
  });

  it("Escape pops the trail (steps back one hop)", async () => {
    renderPage("/entities/hop?center=ent-bob-002&trail=owner-uuid-001,ent-a-009");
    const pane = getPane();
    await act(async () => {
      pane?.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    });
    // After popping, the centre becomes ent-a-009 → hook queried for it.
    const calls = vi.mocked(useEntityNeighbours).mock.calls;
    expect(calls.find((c) => c[0] === "ent-a-009")).toBeTruthy();
  });
});

describe("HopPage — no owner registered", () => {
  it("shows loading state while resolving owner when no ?center= param", () => {
    // Simulate owner status query still in-flight (never resolves)
    vi.mocked(getOwnerSetupStatus).mockReturnValue(new Promise(() => {}));

    vi.mocked(useEntityNeighbours).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useEntityNeighbours>);

    renderPage("/entities/hop");

    // When owner query is in-flight, the Page loading skeleton is shown
    const loadingRegion = container.querySelector("[role='status'][aria-label='Loading']");
    expect(loadingRegion).toBeTruthy();
  });

  it("does not show 'Reset to owner' button when navigating without ?center=", () => {
    vi.mocked(getOwnerSetupStatus).mockReturnValue(new Promise(() => {}));

    vi.mocked(useEntityNeighbours).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useEntityNeighbours>);

    renderPage("/entities/hop");

    const clearBtn = container.querySelector("[data-testid='clear-center-btn']");
    expect(clearBtn).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Predicate filter chips (conditional — only when 2+ predicates)
// ---------------------------------------------------------------------------

describe("HopPage — predicate filter chips", () => {
  it("renders predicate chips when there are 2+ distinct predicates", () => {
    vi.mocked(useEntityNeighbours).mockReturnValue({
      data: KNOWS_NEIGHBOURS, // has 2 predicates: knows + family-of
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useEntityNeighbours>);

    renderPage("/entities/hop?center=owner-uuid-001");

    const chips = container.querySelector("[data-testid='predicate-chips']");
    expect(chips).toBeTruthy();
    // One chip per predicate
    expect(container.querySelector("[data-testid='predicate-chip-knows']")).toBeTruthy();
    expect(container.querySelector("[data-testid='predicate-chip-family-of']")).toBeTruthy();
  });

  it("does NOT render chips when there is only 1 predicate", () => {
    vi.mocked(useEntityNeighbours).mockReturnValue({
      data: {
        neighbours: {
          knows: KNOWS_NEIGHBOURS.neighbours.knows,
        },
        remainders: {},
      },
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useEntityNeighbours>);

    renderPage("/entities/hop?center=owner-uuid-001");

    const chips = container.querySelector("[data-testid='predicate-chips']");
    expect(chips).toBeNull();
  });

  it("clicking a chip filters out other predicate groups", async () => {
    vi.mocked(useEntityNeighbours).mockReturnValue({
      data: KNOWS_NEIGHBOURS,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useEntityNeighbours>);

    renderPage("/entities/hop?center=owner-uuid-001");

    // Both groups visible initially
    expect(container.querySelector("[data-testid='predicate-group-knows']")).toBeTruthy();
    expect(container.querySelector("[data-testid='predicate-group-family-of']")).toBeTruthy();

    // Click "knows" chip to filter to that predicate only
    const knowsChip = container.querySelector(
      "[data-testid='predicate-chip-knows']",
    ) as HTMLButtonElement | null;
    expect(knowsChip).toBeTruthy();

    await act(async () => {
      knowsChip?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    // "knows" group still visible, "family-of" filtered out
    expect(container.querySelector("[data-testid='predicate-group-knows']")).toBeTruthy();
    expect(container.querySelector("[data-testid='predicate-group-family-of']")).toBeNull();
  });

  it("clicking an active chip again removes it from the filter (toggle off)", async () => {
    vi.mocked(useEntityNeighbours).mockReturnValue({
      data: KNOWS_NEIGHBOURS,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useEntityNeighbours>);

    renderPage("/entities/hop?center=owner-uuid-001");

    const knowsChip = container.querySelector(
      "[data-testid='predicate-chip-knows']",
    ) as HTMLButtonElement | null;

    // Click once → filter active (only knows visible)
    await act(async () => {
      knowsChip?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector("[data-testid='predicate-group-family-of']")).toBeNull();

    // Click again → filter cleared (both groups restored)
    await act(async () => {
      knowsChip?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector("[data-testid='predicate-group-knows']")).toBeTruthy();
    expect(container.querySelector("[data-testid='predicate-group-family-of']")).toBeTruthy();
  });

  it("clear button removes all active filters and restores full set", async () => {
    vi.mocked(useEntityNeighbours).mockReturnValue({
      data: KNOWS_NEIGHBOURS,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useEntityNeighbours>);

    renderPage("/entities/hop?center=owner-uuid-001");

    const knowsChip = container.querySelector(
      "[data-testid='predicate-chip-knows']",
    ) as HTMLButtonElement | null;

    // Activate a filter
    await act(async () => {
      knowsChip?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    // Clear button should appear
    const clearBtn = container.querySelector(
      "[data-testid='predicate-chips-clear']",
    ) as HTMLButtonElement | null;
    expect(clearBtn).toBeTruthy();

    // Click clear → full set restored
    await act(async () => {
      clearBtn?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector("[data-testid='predicate-group-knows']")).toBeTruthy();
    expect(container.querySelector("[data-testid='predicate-group-family-of']")).toBeTruthy();
    // Clear button gone (no active filters)
    expect(container.querySelector("[data-testid='predicate-chips-clear']")).toBeNull();
  });

  it("clear button does not appear when no chips are active", () => {
    vi.mocked(useEntityNeighbours).mockReturnValue({
      data: KNOWS_NEIGHBOURS,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useEntityNeighbours>);

    renderPage("/entities/hop?center=owner-uuid-001");

    const clearBtn = container.querySelector("[data-testid='predicate-chips-clear']");
    expect(clearBtn).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Right detail pane — inspect a selected neighbour without re-centring
// ---------------------------------------------------------------------------

describe("HopPage — right detail pane", () => {
  it("detail pane is not shown initially (no neighbour selected)", () => {
    renderPage("/entities/hop?center=owner-uuid-001");
    expect(container.querySelector("[data-testid='neighbour-detail-pane']")).toBeNull();
  });

  it("clicking a neighbour opens the detail pane for that entity", async () => {
    vi.mocked(useEntityNeighbours).mockReturnValue({
      data: KNOWS_NEIGHBOURS,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useEntityNeighbours>);

    renderPage("/entities/hop?center=owner-uuid-001");

    const neighbourBtn = container.querySelector(
      "[data-entity-id='ent-bob-002']",
    ) as HTMLButtonElement | null;
    expect(neighbourBtn).toBeTruthy();

    await act(async () => {
      neighbourBtn?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    // Detail pane should appear
    const pane = container.querySelector("[data-testid='neighbour-detail-pane']");
    expect(pane).toBeTruthy();
  });

  it("clicking the same neighbour again deselects (toggles) the detail pane", async () => {
    vi.mocked(useEntityNeighbours).mockReturnValue({
      data: KNOWS_NEIGHBOURS,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useEntityNeighbours>);

    renderPage("/entities/hop?center=owner-uuid-001");

    const neighbourBtn = container.querySelector(
      "[data-entity-id='ent-bob-002']",
    ) as HTMLButtonElement | null;

    // Open pane
    await act(async () => {
      neighbourBtn?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector("[data-testid='neighbour-detail-pane']")).toBeTruthy();

    // Click same entity again → pane closes
    await act(async () => {
      neighbourBtn?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector("[data-testid='neighbour-detail-pane']")).toBeNull();
  });

  it("clicking the detail pane close button dismisses the pane", async () => {
    vi.mocked(useEntityNeighbours).mockReturnValue({
      data: KNOWS_NEIGHBOURS,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useEntityNeighbours>);

    renderPage("/entities/hop?center=owner-uuid-001");

    // Open pane
    const neighbourBtn = container.querySelector(
      "[data-entity-id='ent-bob-002']",
    ) as HTMLButtonElement | null;
    await act(async () => {
      neighbourBtn?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector("[data-testid='neighbour-detail-pane']")).toBeTruthy();

    // Click close
    const closeBtn = container.querySelector(
      "[data-testid='detail-pane-close']",
    ) as HTMLButtonElement | null;
    expect(closeBtn).toBeTruthy();
    await act(async () => {
      closeBtn?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector("[data-testid='neighbour-detail-pane']")).toBeNull();
  });

  it("the detail pane 'Go to this entity' button re-centres on the selected neighbour", async () => {
    vi.mocked(useEntityNeighbours).mockReturnValue({
      data: KNOWS_NEIGHBOURS,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useEntityNeighbours>);

    renderPage("/entities/hop?center=owner-uuid-001");

    // Open pane for Bob
    const neighbourBtn = container.querySelector(
      "[data-entity-id='ent-bob-002']",
    ) as HTMLButtonElement | null;
    await act(async () => {
      neighbourBtn?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    // Click "Go to this entity"
    const recentreBtn = container.querySelector(
      "[data-testid='detail-pane-recentre-btn']",
    ) as HTMLButtonElement | null;
    expect(recentreBtn).toBeTruthy();

    await act(async () => {
      recentreBtn?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    // After re-centring, the hook should have been called with the new entity ID,
    // and the pane should be dismissed.
    const calls = vi.mocked(useEntityNeighbours).mock.calls;
    expect(calls.find((c) => c[0] === "ent-bob-002")).toBeTruthy();
    // The pane is dismissed after re-centring
    expect(container.querySelector("[data-testid='neighbour-detail-pane']")).toBeNull();
  });

  it("clicking a neighbour does NOT immediately re-centre (URL stays the same)", async () => {
    vi.mocked(useEntityNeighbours).mockReturnValue({
      data: KNOWS_NEIGHBOURS,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useEntityNeighbours>);

    renderPage("/entities/hop?center=owner-uuid-001");

    const neighbourBtn = container.querySelector(
      "[data-entity-id='ent-bob-002']",
    ) as HTMLButtonElement | null;
    await act(async () => {
      neighbourBtn?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    // The trail should NOT have appeared (no re-centre happened)
    // and the original anchor card loading/state should still be present.
    // We check that the clear-center-btn is still there (it was present because center=owner-uuid-001).
    // No new trail segment should have been created.
    const trail = container.querySelector("[data-testid='hop-trail']");
    expect(trail).toBeNull(); // no trail — click didn't re-centre
  });

  it("Enter on the keyboard cursor still re-centres (not just opens the pane)", async () => {
    vi.mocked(useEntityNeighbours).mockReturnValue({
      data: KNOWS_NEIGHBOURS,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useEntityNeighbours>);

    renderPage("/entities/hop?center=owner-uuid-001");

    const pane = container.querySelector("[data-testid='neighbours-panel']") as HTMLElement | null;
    // Enter on cursor (first row) should re-centre
    await act(async () => {
      pane?.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    });

    // The hook should have been called with the first flat-entry entity.
    // Predicates are sorted: "family-of" < "knows", so first row is Carol (ent-carol-003).
    const calls = vi.mocked(useEntityNeighbours).mock.calls;
    expect(calls.find((c) => c[0] === "ent-carol-003")).toBeTruthy();
    // Detail pane closed after re-centring
    expect(container.querySelector("[data-testid='neighbour-detail-pane']")).toBeNull();
  });
});
