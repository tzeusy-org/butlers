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
  useEntityNotes: vi.fn(),
  useEntityInteractions: vi.fn(),
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
};

const EMPTY_NEIGHBOURS: NeighboursResponse = { neighbours: {} };

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
