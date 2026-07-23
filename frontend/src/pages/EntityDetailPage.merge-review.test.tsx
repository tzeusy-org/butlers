// @vitest-environment jsdom
/**
 * Merge-review entry-point tests for EntityDetailPage (relationship-merge-review,
 * bu-b2qg8).
 *
 * Covers:
 * - the duplicate-warning panel renders only when duplicate evidence exists for
 *   the entity (a duplicate-candidate queue entry with a peer);
 * - the panel's "Review merge" action and the `m` key both open the compare view;
 * - `m` is inert when no duplicate evidence exists.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const routerSpies = vi.hoisted(() => ({
  announce: vi.fn(),
  navigate: vi.fn(),
}));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

vi.mock("react-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router")>();
  return {
    ...actual,
    useParams: vi.fn(() => ({ entityId: "entity-001" })),
    useNavigate: vi.fn(() => routerSpies.navigate),
  };
});

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

vi.mock("@/lib/shell-announcer", () => ({ announce: routerSpies.announce }));

vi.mock("@/hooks/use-memory", () => ({
  useEntity: vi.fn(),
  useUpdateEntity: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  usePromoteEntity: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  useForgetRelationshipEntity: vi.fn(() => ({ mutateAsync: vi.fn(), isPending: false })),
  useRevealEntitySecret: vi.fn(() => ({ mutate: vi.fn() })),
  useSetLinkedContact: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  useUnlinkContact: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
}));

const useRelationshipEntityQueue = vi.fn();

vi.mock("@/hooks/use-entities", () => ({
  useEntityTimeline: vi.fn(() => ({ data: [], isLoading: false })),
  useEntityGifts: vi.fn(() => ({ data: [], isLoading: false })),
  useEntityLoans: vi.fn(() => ({ data: [], isLoading: false })),
  useEntityMessageThreads: vi.fn(() => ({ data: [], isLoading: false })),
  useEntityLinkedContacts: vi.fn(() => ({ data: [], isLoading: false })),
  useEntityDates: vi.fn(() => ({ data: [], isLoading: false })),
  useEntityActivityBins: vi.fn(() => ({ data: { bins: [] }, isLoading: false, isError: false })),
  useEntityDeltaFacts: vi.fn(() => ({ data: { marked_at: null, items: [] }, isSuccess: true })),
  useMarkEntityView: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  useEntityCoreDates: vi.fn(() => ({ data: { items: [] }, isLoading: false })),
  useUpdateEntityDunbarTier: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  useEntityNeighbours: vi.fn(() => ({ data: { neighbours: {}, remainders: {} } })),
  useRelationshipEntities: vi.fn(() => ({
    data: { items: [], total: 0, limit: 200, offset: 0 },
  })),
  useRelationshipEntitiesByIds: vi.fn(() => ({
    data: { items: [], total: 0, limit: 1, offset: 0 },
  })),
  useArchiveRelationshipEntity: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  useEntityFacts: vi.fn(() => ({
    data: { items: [], next_cursor: null, has_more: false },
    isFetching: false,
    error: null,
  })),
  useRelationshipEntityQueue: (...args: Parameters<typeof import("@/hooks/use-entities").useRelationshipEntityQueue>) => useRelationshipEntityQueue(...args),
  useCompareEntities: vi.fn(() => ({
    mutateAsync: vi.fn().mockResolvedValue({
      a: { entity: { id: "entity-001", canonical_name: "A", entity_type: "person", aliases: [], tier: null, state: "active" }, identity_facts: [], narrative_facts: [] },
      b: { entity: { id: "peer-002", canonical_name: "B", entity_type: "person", aliases: [], tier: null, state: "active" }, identity_facts: [], narrative_facts: [] },
      shared: [],
      divergent: [],
    }),
    reset: vi.fn(),
    isPending: false,
  })),
  useDismissEntityPair: vi.fn(() => ({ mutateAsync: vi.fn(), isPending: false })),
  useMergeRelationshipEntities: vi.fn(() => ({ mutateAsync: vi.fn(), isPending: false })),
}));

vi.mock("@/hooks/use-contacts", () => ({
  useContacts: vi.fn(() => ({ data: { contacts: [] } })),
  useCreateContactInfo: vi.fn(() => ({ mutateAsync: vi.fn(), isPending: false })),
  useDeleteContactInfo: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  usePatchContact: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  usePatchContactInfo: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
}));

vi.mock("@/components/relationship/OwnerSetupBanner", () => ({
  OwnerSetupBanner: () => null,
}));

import { useEntity } from "@/hooks/use-memory";
import EntityDetailPage from "@/pages/EntityDetailPage";
import { DUP_QUEUE, EMPTY_QUEUE, ENTITY } from "@/test-utils/entity-detail-page";

// This entity collides with two distinct peers on the same identifier.
const MULTI_PEER_QUEUE = {
  data: {
    items: [
      {
        entity_id: "entity-001",
        canonical_name: "Test Person",
        entity_type: "person",
        bucket: "duplicate-candidate",
        evidence: {
          predicate: "has-email",
          shared_value: "x@y.com",
          peer_entity_ids: ["peer-002", "peer-003"],
        },
        last_seen: null,
      },
      {
        entity_id: "peer-002",
        canonical_name: "Peer Two",
        entity_type: "person",
        bucket: "duplicate-candidate",
        evidence: {},
        last_seen: null,
      },
      {
        entity_id: "peer-003",
        canonical_name: "Peer Three",
        entity_type: "person",
        bucket: "duplicate-candidate",
        evidence: {},
        last_seen: null,
      },
    ],
    total: 3,
    limit: 100,
    offset: 0,
  },
};

let container: HTMLDivElement;
let root: Root;

function render(initialUrl = "/entities/entity-001", entity = ENTITY) {
  vi.mocked(useEntity).mockReturnValue({
    data: { data: entity },
    isLoading: false,
    error: null,
  } as unknown as ReturnType<typeof useEntity>);
  act(() => {
    root.render(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter initialEntries={[initialUrl]}>
          <EntityDetailPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );
  });
}

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.clearAllMocks();
});

describe("EntityDetailPage — merge-review entry points", () => {
  it("renders the duplicate-warning panel when duplicate evidence exists", () => {
    useRelationshipEntityQueue.mockReturnValue(DUP_QUEUE);
    render();
    expect(container.querySelector("[data-testid='duplicate-warning-panel']")).toBeTruthy();
  });

  it("hides the duplicate-warning panel when no duplicate evidence exists", () => {
    useRelationshipEntityQueue.mockReturnValue(EMPTY_QUEUE);
    render();
    expect(container.querySelector("[data-testid='duplicate-warning-panel']")).toBeNull();
  });

  it("opens the compare view from the panel's Review merge action", () => {
    useRelationshipEntityQueue.mockReturnValue(DUP_QUEUE);
    render();
    const reviewBtn = container.querySelector(
      "[data-testid='duplicate-warning-review']",
    ) as HTMLButtonElement;
    act(() => reviewBtn.click());
    expect(document.querySelector("[data-testid='merge-compare-dialog']")).toBeTruthy();
  });

  it("opens the compare view when `m` is pressed and duplicate evidence exists", () => {
    useRelationshipEntityQueue.mockReturnValue(DUP_QUEUE);
    render();
    // The `m` binding is VIEW-LOCAL: it fires on the focused detail container,
    // not window.
    const detailRoot = container.querySelector(
      "[data-testid='entity-detail-root']",
    ) as HTMLDivElement;
    act(() => {
      detailRoot.dispatchEvent(new KeyboardEvent("keydown", { key: "m", bubbles: true }));
    });
    expect(document.querySelector("[data-testid='merge-compare-dialog']")).toBeTruthy();
  });

  it("the `m` binding is NOT window-global", () => {
    useRelationshipEntityQueue.mockReturnValue(DUP_QUEUE);
    render();
    // A window-level keydown must NOT open the compare view — the map is local
    // to the focused detail container.
    act(() => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "m" }));
    });
    expect(document.querySelector("[data-testid='merge-compare-dialog']")).toBeNull();
  });

  it("`m` is inert when no duplicate evidence exists", () => {
    useRelationshipEntityQueue.mockReturnValue(EMPTY_QUEUE);
    render();
    const detailRoot = container.querySelector(
      "[data-testid='entity-detail-root']",
    ) as HTMLDivElement;
    act(() => {
      detailRoot.dispatchEvent(new KeyboardEvent("keydown", { key: "m", bubbles: true }));
    });
    expect(document.querySelector("[data-testid='merge-compare-dialog']")).toBeNull();
  });

  it("the duplicate-warning panel opens the compare view with the triggering evidence", () => {
    useRelationshipEntityQueue.mockReturnValue(DUP_QUEUE);
    render();
    const reviewBtn = container.querySelector(
      "[data-testid='duplicate-warning-review']",
    ) as HTMLButtonElement;
    act(() => reviewBtn.click());
    // The compare view renders (the highlight is plumbed through; this asserts
    // the entry point opens the surface for the duplicate pair).
    expect(document.querySelector("[data-testid='merge-compare-dialog']")).toBeTruthy();
  });

  it("the Workbench left rail lists one 'shares identifiers with' entry per peer", () => {
    useRelationshipEntityQueue.mockReturnValue(MULTI_PEER_QUEUE);
    render("/entities/entity-001?mode=workbench");
    const shares = container.querySelectorAll("[data-testid='workbench-shares-identifiers']");
    // One clickable peer hint per collision (no longer peer_entity_ids[0] only).
    expect(shares.length).toBe(2);
    const labels = Array.from(shares).map((el) => el.textContent ?? "");
    expect(labels.some((t) => t.includes("Peer Two"))).toBe(true);
    expect(labels.some((t) => t.includes("Peer Three"))).toBe(true);
  });

  it("each Workbench peer hint opens the compare view for that pair", () => {
    useRelationshipEntityQueue.mockReturnValue(MULTI_PEER_QUEUE);
    render("/entities/entity-001?mode=workbench");
    const shares = container.querySelectorAll("[data-testid='workbench-shares-identifiers']");
    act(() => (shares[1] as HTMLButtonElement).click());
    expect(document.querySelector("[data-testid='merge-compare-dialog']")).toBeTruthy();
  });
});

describe("EntityDetailPage — tombstoned entity navigation", () => {
  it("replace-navigates to a valid survivor with an accessible announcement and no source controls", () => {
    useRelationshipEntityQueue.mockReturnValue(EMPTY_QUEUE);
    render("/entities/entity-001", {
      ...ENTITY,
      metadata: { merged_into: "survivor-002" },
    });

    expect(routerSpies.navigate).toHaveBeenCalledWith("/entities/survivor-002", {
      replace: true,
    });
    expect(routerSpies.announce).toHaveBeenCalledWith(
      "This entity was merged. Opening its surviving record.",
    );
    const redirectStatus = container.querySelector("[data-testid='entity-merge-redirect']");
    expect(redirectStatus?.getAttribute("role")).toBe("status");
    expect(redirectStatus?.getAttribute("aria-live")).toBe("polite");
    expect(container.querySelector("[data-testid='entity-detail-root']")).toBeNull();
    expect(container.querySelector("[data-testid='forget-entity-button']")).toBeNull();
    expect(container.textContent).not.toContain("Test Person");
  });

  it("does not redirect a normal entity", () => {
    useRelationshipEntityQueue.mockReturnValue(EMPTY_QUEUE);
    render();

    expect(routerSpies.navigate).not.toHaveBeenCalled();
    expect(container.querySelector("[data-testid='entity-detail-root']")).toBeTruthy();
  });

  it("does not redirect an archived entity without a merge target", () => {
    useRelationshipEntityQueue.mockReturnValue(EMPTY_QUEUE);
    render("/entities/entity-001", { ...ENTITY, archived: true, metadata: {} });

    expect(routerSpies.navigate).not.toHaveBeenCalled();
    expect(container.querySelector("[data-testid='entity-detail-root']")).toBeTruthy();
  });

  it("keeps malformed merge metadata in place and names the inconsistency", () => {
    useRelationshipEntityQueue.mockReturnValue(EMPTY_QUEUE);
    render("/entities/entity-001", { ...ENTITY, metadata: { merged_into: 42 } });

    expect(routerSpies.navigate).not.toHaveBeenCalled();
    const inconsistency = container.querySelector(
      "[data-testid='entity-merge-metadata-inconsistency']",
    );
    expect(inconsistency?.getAttribute("role")).toBe("alert");
    expect(inconsistency?.textContent).toContain("Merge metadata inconsistency");
  });

  it("keeps a blank merge target in place and names the inconsistency", () => {
    useRelationshipEntityQueue.mockReturnValue(EMPTY_QUEUE);
    render("/entities/entity-001", { ...ENTITY, metadata: { merged_into: "   " } });

    expect(routerSpies.navigate).not.toHaveBeenCalled();
    expect(
      container.querySelector("[data-testid='entity-merge-metadata-inconsistency']"),
    ).toBeTruthy();
  });

  it("keeps self-referential merge metadata in place and names the inconsistency", () => {
    useRelationshipEntityQueue.mockReturnValue(EMPTY_QUEUE);
    render("/entities/entity-001", { ...ENTITY, metadata: { merged_into: "entity-001" } });

    expect(routerSpies.navigate).not.toHaveBeenCalled();
    expect(
      container.querySelector("[data-testid='entity-merge-metadata-inconsistency']"),
    ).toBeTruthy();
  });
});
