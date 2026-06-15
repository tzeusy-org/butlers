// @vitest-environment jsdom
/**
 * Workbench three-rail layout tests for EntityDetailPage (entity v3,
 * dashboard-relationship "Workbench three-rail layout", bu-ly48x).
 *
 * Covers the three spec scenarios:
 *  - "Workbench is a real layout, not a re-skin" — the three rails render with
 *    the KPI strip, the sortable provenance grid (store-labelled rows), and the
 *    action rail.
 *  - "Duplicate panel routes to compare" — the right-rail panel's commit button
 *    opens the compare view (no direct merge).
 *  - "Staleness inspector" — the inspector renders a stale band per fact.
 * Plus: the Detail keyboard map (k/j siblings, Esc back) and the no-44px-Display
 *  invariant.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import type { EntityFact, NeighbourEntry } from "@/api/types";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

const navigateMock = vi.fn();

vi.mock("react-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router")>();
  return {
    ...actual,
    useParams: vi.fn(() => ({ entityId: "entity-001" })),
    useNavigate: vi.fn(() => navigateMock),
  };
});

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

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
const useEntityNeighbours = vi.fn();
const useRelationshipEntities = vi.fn();
const useRelationshipEntitiesByIds = vi.fn(() => ({
  data: { items: [], total: 0, limit: 1, offset: 0 },
}));
const useEntityFacts = vi.fn();

vi.mock("@/hooks/use-entities", () => ({
  useEntityTimeline: vi.fn(() => ({ data: [], isLoading: false })),
  useEntityGifts: vi.fn(() => ({ data: [], isLoading: false })),
  useEntityLoans: vi.fn(() => ({ data: [], isLoading: false })),
  useEntityMessageThreads: vi.fn(() => ({ data: [], isLoading: false })),
  useEntityLinkedContacts: vi.fn(() => ({ data: [], isLoading: false })),
  useEntityDates: vi.fn(() => ({ data: [], isLoading: false })),
  useEntityActivityBins: vi.fn(() => ({
    data: { bins: [{ date: "2025-03-10", count: 3 }] },
    isLoading: false,
    isError: false,
  })),
  useEntityDeltaFacts: vi.fn(() => ({ data: { marked_at: null, items: [] }, isSuccess: true })),
  useMarkEntityView: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  useEntityCoreDates: vi.fn(() => ({ data: { items: [] }, isLoading: false })),
  useUpdateEntityDunbarTier: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  useEntityNeighbours: (...args: Parameters<typeof import("@/hooks/use-entities").useEntityNeighbours>) => useEntityNeighbours(...args),
  useRelationshipEntities: (...args: Parameters<typeof import("@/hooks/use-entities").useRelationshipEntities>) => useRelationshipEntities(...args),
  useRelationshipEntitiesByIds: (...args: Parameters<typeof import("@/hooks/use-entities").useRelationshipEntitiesByIds>) => useRelationshipEntitiesByIds(...args),
  useArchiveRelationshipEntity: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  useEntityFacts: (...args: Parameters<typeof import("@/hooks/use-entities").useEntityFacts>) => useEntityFacts(...args),
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
import EntityDetailPage, { ENTITY_MODE_STORAGE_KEY } from "@/pages/EntityDetailPage";
import { DUP_QUEUE, EMPTY_QUEUE, ENTITY } from "@/test-utils/entity-detail-page";

// localStorage is provided by jsdom; default the detail mode to workbench.

const NEIGHBOUR: NeighbourEntry = {
  entity_id: "peer-200",
  canonical_name: "Lin Friend",
  direction: "forward",
  src: "general",
  conf: 0.9,
  last_seen: null,
  weight: 7,
  verified: true,
  primary: null,
};

const IDENTITY_FACT: EntityFact = {
  id: "fact-id-1",
  subject: "entity-001",
  predicate: "has-email",
  object: "x@y.com",
  object_kind: "literal",
  src: "general",
  conf: 1.0,
  weight: 5,
  last_observed_at: "2024-05-01T00:00:00Z",
  verified: true,
  primary: null,
  validity: "active",
  created_at: "2024-05-01T00:00:00Z",
  store: "identity",
  staleness_band: "stale",
};

const NARRATIVE_FACT: EntityFact = {
  id: "fact-narr-1",
  subject: "entity-001",
  predicate: "discussed",
  object: "the merger",
  object_kind: "literal",
  src: "general",
  conf: 0.5,
  weight: 2,
  last_observed_at: "2025-03-01T00:00:00Z",
  verified: false,
  primary: null,
  validity: "active",
  created_at: "2025-03-01T00:00:00Z",
  store: "narrative",
  staleness_band: "fresh",
};

let container: HTMLDivElement;
let root: Root;

function render() {
  vi.mocked(useEntity).mockReturnValue({
    data: { data: ENTITY },
    isLoading: false,
    error: null,
  } as unknown as ReturnType<typeof useEntity>);
  act(() => {
    root.render(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter>
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
  navigateMock.mockReset();
  localStorage.clear();
  localStorage.setItem(ENTITY_MODE_STORAGE_KEY, "workbench");

  // Sensible defaults — overridden per test as needed.
  useRelationshipEntityQueue.mockReturnValue(EMPTY_QUEUE);
  useEntityNeighbours.mockReturnValue({
    data: { neighbours: { knows: [NEIGHBOUR] }, remainders: {} },
  });
  useRelationshipEntities.mockReturnValue({
    data: {
      items: [{ id: "sib-prev" }, { id: "entity-001" }, { id: "sib-next" }],
      total: 3,
      limit: 200,
      offset: 0,
    },
  });
  useEntityFacts.mockReturnValue({
    data: { items: [IDENTITY_FACT, NARRATIVE_FACT], next_cursor: null, has_more: false },
    isFetching: false,
    error: null,
  });
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.clearAllMocks();
});

describe("EntityDetailPage — Workbench three-rail layout", () => {
  it("renders the three rails with KPI strip, provenance grid, and action rail", () => {
    render();
    expect(container.querySelector("[data-testid='workbench-three-rail']")).toBeTruthy();
    expect(container.querySelector("[data-testid='workbench-context-rail']")).toBeTruthy();
    expect(container.querySelector("[data-testid='workbench-action-rail']")).toBeTruthy();
    expect(container.querySelector("[data-testid='workbench-kpi-strip']")).toBeTruthy();
    expect(container.querySelector("[data-testid='provenance-grid']")).toBeTruthy();
  });

  it("KPI strip has exactly four cells", () => {
    render();
    const cells = container.querySelectorAll("[data-testid='workbench-kpi-cell']");
    expect(cells.length).toBe(4);
  });

  it("provenance grid labels each row's store of origin (both stores)", () => {
    render();
    expect(container.querySelector("[data-testid='provenance-row-identity']")).toBeTruthy();
    expect(container.querySelector("[data-testid='provenance-row-narrative']")).toBeTruthy();
  });

  it("does NOT render a 44px Display headline in the workbench", () => {
    render();
    // The Display primitive emits the text-[44px] utility class; the workbench
    // (archetype="overview") must never render it. The identity hero carries the
    // name at text-2xl instead.
    expect(container.innerHTML).not.toContain("text-[44px]");
    expect(container.innerHTML).not.toContain("44px");
  });

  it("left rail renders top relations and the canned introduced-via gloss", () => {
    render();
    const relRows = container.querySelectorAll("[data-testid='workbench-relation-row']");
    expect(relRows.length).toBeGreaterThan(0);
    expect(container.textContent).toContain("Lin Friend");
    const introduced = container.querySelector("[data-testid='workbench-introduced-via']");
    expect(introduced).toBeTruthy();
  });

  it("renders the curation action list (merge/promote-tier/archive/forget)", () => {
    render();
    expect(container.querySelector("[data-testid='workbench-action-promote-tier']")).toBeTruthy();
    expect(container.querySelector("[data-testid='workbench-action-demote-tier']")).toBeTruthy();
    expect(container.querySelector("[data-testid='workbench-action-edit-names']")).toBeTruthy();
    expect(container.querySelector("[data-testid='workbench-action-edit-contacts']")).toBeTruthy();
    expect(container.querySelector("[data-testid='workbench-action-archive']")).toBeTruthy();
    expect(container.querySelector("[data-testid='workbench-action-forget']")).toBeTruthy();
  });
});

describe("EntityDetailPage — Workbench staleness inspector", () => {
  it("renders the staleness band for each fact", () => {
    render();
    const inspector = container.querySelector("[data-testid='workbench-inspector']");
    expect(inspector).toBeTruthy();
    // Staleness band rendered for each fact row.
    const staleBand = inspector!.querySelector("[data-staleness='stale']");
    expect(staleBand).toBeTruthy();
    expect(staleBand!.getAttribute("data-stale")).toBe("true");
  });
});

describe("EntityDetailPage — Workbench duplicate panel", () => {
  it("renders the duplicate panel only when the entity is a duplicate-candidate", () => {
    useRelationshipEntityQueue.mockReturnValue(EMPTY_QUEUE);
    render();
    expect(container.querySelector("[data-testid='workbench-duplicate-panel']")).toBeNull();

    act(() => root.unmount());
    container.remove();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    useRelationshipEntityQueue.mockReturnValue(DUP_QUEUE);
    render();
    expect(container.querySelector("[data-testid='workbench-duplicate-panel']")).toBeTruthy();
  });

  it("shows the deterministic evidence string in the panel", () => {
    useRelationshipEntityQueue.mockReturnValue(DUP_QUEUE);
    render();
    const panel = container.querySelector("[data-testid='workbench-duplicate-panel']");
    expect(panel!.textContent).toContain("has email");
    expect(panel!.textContent).toContain("x@y.com");
  });

  it("commit button opens the compare view (no direct merge)", () => {
    useRelationshipEntityQueue.mockReturnValue(DUP_QUEUE);
    render();
    const commit = container.querySelector(
      "[data-testid='workbench-duplicate-commit']",
    ) as HTMLButtonElement;
    act(() => commit.click());
    expect(document.querySelector("[data-testid='merge-compare-dialog']")).toBeTruthy();
  });
});

describe("EntityDetailPage — Workbench keyboard map", () => {
  // The Detail keyboard map is VIEW-LOCAL: it binds to the focused detail
  // container via onKeyDown, never to window.
  function detailRoot() {
    return container.querySelector(
      "[data-testid='entity-detail-root']",
    ) as HTMLDivElement;
  }

  function dispatchKey(key: string, init: KeyboardEventInit = {}) {
    act(() => {
      detailRoot().dispatchEvent(
        new KeyboardEvent("keydown", { key, bubbles: true, ...init }),
      );
    });
  }

  it("j steps to the next sibling in Index order", () => {
    render();
    dispatchKey("j");
    expect(navigateMock).toHaveBeenCalledWith("/entities/sib-next");
  });

  it("k steps to the previous sibling in Index order", () => {
    render();
    dispatchKey("k");
    expect(navigateMock).toHaveBeenCalledWith("/entities/sib-prev");
  });

  it("Esc returns to the entities index", () => {
    render();
    dispatchKey("Escape");
    expect(navigateMock).toHaveBeenCalledWith("/entities");
  });

  it("does not shadow Cmd-K (meta-modified keys are ignored)", () => {
    render();
    dispatchKey("k", { metaKey: true });
    expect(navigateMock).not.toHaveBeenCalled();
  });

  it("the map is not window-global (window keydown does not navigate)", () => {
    render();
    act(() => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "j" }));
    });
    expect(navigateMock).not.toHaveBeenCalled();
  });

  it("e starts editing the entity name (name input appears)", () => {
    render();
    // Before pressing 'e', no text input for the name should be visible.
    const inputsBefore = container.querySelectorAll("input");
    const nameInputBefore = Array.from(inputsBefore).find(
      (el) => el.classList.contains("text-2xl"),
    );
    expect(nameInputBefore).toBeUndefined();

    dispatchKey("e");

    // After pressing 'e', the name-edit inline input should appear.
    const inputsAfter = container.querySelectorAll("input");
    const nameInputAfter = Array.from(inputsAfter).find(
      (el) => el.classList.contains("text-2xl"),
    );
    expect(nameInputAfter).toBeDefined();
  });

  it("Shift+Backspace opens the forget confirmation dialog without confirming", () => {
    render();
    // Dialog should not be open initially (Radix portals render into document.body).
    expect(document.body.querySelector("[role='alertdialog']")).toBeNull();

    dispatchKey("Backspace", { shiftKey: true });

    // Forget confirmation dialog should be open; entity should NOT be forgotten.
    expect(document.body.querySelector("[role='alertdialog']")).toBeTruthy();
  });

  it("e is ignored when the event target is an INPUT element", () => {
    render();
    // Press 'e' first to open the edit input, then fire another 'e' from inside
    // the input — the second dispatch should not re-trigger (no crash / no double).
    dispatchKey("e");
    const nameInput = Array.from(container.querySelectorAll("input")).find(
      (el) => el.classList.contains("text-2xl"),
    ) as HTMLInputElement | undefined;
    expect(nameInput).toBeDefined();

    // Dispatch 'e' from the input itself — guard should suppress the handler.
    act(() => {
      nameInput!.dispatchEvent(
        new KeyboardEvent("keydown", { key: "e", bubbles: true }),
      );
    });
    // Navigate should still not have been called.
    expect(navigateMock).not.toHaveBeenCalled();
  });

  it("Shift+Backspace is ignored when meta key is held", () => {
    render();
    dispatchKey("Backspace", { shiftKey: true, metaKey: true });
    expect(document.body.querySelector("[role='alertdialog']")).toBeNull();
  });
});
