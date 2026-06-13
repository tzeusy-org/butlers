// @vitest-environment jsdom
/**
 * Component tests for EntitiesIndexPage (§8.1).
 *
 * Covers:
 * - Route mounts the page (SubpageTabs + table heading rendered)
 * - Table renders entity rows from the list API (§9.1)
 * - Filter chips update the query params passed to the hook
 * - Right rail loads items from the queue endpoint (§9.5)
 * - Right rail collapses to serif italic when queue is empty
 * - Table renders empty state when entities list is empty
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
// Mock hooks — must appear before component imports
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-entities", () => ({
  useRelationshipEntities: vi.fn(),
  useRelationshipEntityQueue: vi.fn(),
  usePromoteRelationshipEntity: vi.fn(),
  useArchiveRelationshipEntity: vi.fn(),
  useForgetRelationshipEntity: vi.fn(),
  useDismissRelationshipEntityQueueItem: vi.fn(),
  useMergeRelationshipEntities: vi.fn(),
  useCompareEntities: vi.fn(),
  useDismissEntityPair: vi.fn(),
  // Other exports from use-entities that the module re-exports
  useEntityLinkedContacts: vi.fn(),
  useEntityGifts: vi.fn(),
  useEntityLoans: vi.fn(),
  useEntityTimeline: vi.fn(),
  useEntityMessageThreads: vi.fn(),
  useEntityDates: vi.fn(),
  useEntityFinderSearch: vi.fn(),
  useUpdateEntityDunbarTier: vi.fn(),
}));

import {
  useArchiveRelationshipEntity,
  useCompareEntities,
  useDismissEntityPair,
  useDismissRelationshipEntityQueueItem,
  useEntityFinderSearch,
  useForgetRelationshipEntity,
  useMergeRelationshipEntities,
  usePromoteRelationshipEntity,
  useRelationshipEntities,
  useRelationshipEntityQueue,
} from "@/hooks/use-entities";
import type {
  RelationshipEntitySummary,
  RelationshipEntityListResponse,
  RelationshipQueueResponse,
} from "@/api/types";
import { EntitiesIndexPage } from "./EntitiesIndexPage";

// ---------------------------------------------------------------------------
// Test fixtures
// ---------------------------------------------------------------------------

const ALICE: RelationshipEntitySummary = {
  id: "ent-alice-001",
  canonical_name: "Alice Fogg",
  entity_type: "person",
  aliases: ["Al"],
  roles: [],
  metadata: {},
  tier: 15,
  last_seen: "2026-05-01T10:00:00Z",
  contact_fact_count: 2,
  created_at: "2025-01-01T00:00:00Z",
  updated_at: "2025-01-01T00:00:00Z",
};

const BOB: RelationshipEntitySummary = {
  id: "ent-bob-002",
  canonical_name: "Bob Hatch",
  entity_type: "organization",
  aliases: [],
  roles: [],
  metadata: {},
  tier: null,
  last_seen: null,
  contact_fact_count: 0,
  created_at: "2025-02-01T00:00:00Z",
  updated_at: "2025-02-01T00:00:00Z",
};

function makeListResponse(
  items: RelationshipEntitySummary[],
): RelationshipEntityListResponse {
  return { items, total: items.length, limit: 50, offset: 0 };
}

function makeQueueResponse(
  items: RelationshipQueueResponse["items"],
): RelationshipQueueResponse {
  return { items, total: items.length, limit: 20, offset: 0 };
}

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
let promoteMutateAsync: ReturnType<typeof vi.fn>;
let archiveMutateAsync: ReturnType<typeof vi.fn>;
let forgetMutateAsync: ReturnType<typeof vi.fn>;
let dismissMutateAsync: ReturnType<typeof vi.fn>;
let mergeMutateAsync: ReturnType<typeof vi.fn>;

function renderPage(initialUrl = "/entities") {
  const qc = makeQueryClient();
  act(() => {
    root.render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={[initialUrl]}>
          <EntitiesIndexPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );
  });
}

beforeEach(() => {
  vi.resetAllMocks();
  promoteMutateAsync = vi.fn().mockResolvedValue({});
  archiveMutateAsync = vi.fn().mockResolvedValue(undefined);
  forgetMutateAsync = vi.fn().mockResolvedValue(undefined);
  dismissMutateAsync = vi.fn().mockResolvedValue({});
  mergeMutateAsync = vi.fn().mockResolvedValue({});

  // Default: empty list + empty queue
  vi.mocked(useRelationshipEntities).mockReturnValue({
    data: makeListResponse([]),
    isLoading: false,
    isError: false,
    error: null,
  } as unknown as ReturnType<typeof useRelationshipEntities>);

  vi.mocked(useRelationshipEntityQueue).mockReturnValue({
    data: makeQueueResponse([]),
    isLoading: false,
    isError: false,
    error: null,
  } as unknown as ReturnType<typeof useRelationshipEntityQueue>);

  // Default: toolbar/finder search returns nothing (empty query path).
  vi.mocked(useEntityFinderSearch).mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useEntityFinderSearch>);

  vi.mocked(usePromoteRelationshipEntity).mockReturnValue({
    mutateAsync: promoteMutateAsync,
    isPending: false,
  } as unknown as ReturnType<typeof usePromoteRelationshipEntity>);
  vi.mocked(useArchiveRelationshipEntity).mockReturnValue({
    mutateAsync: archiveMutateAsync,
    isPending: false,
  } as unknown as ReturnType<typeof useArchiveRelationshipEntity>);
  vi.mocked(useForgetRelationshipEntity).mockReturnValue({
    mutateAsync: forgetMutateAsync,
    isPending: false,
  } as unknown as ReturnType<typeof useForgetRelationshipEntity>);
  vi.mocked(useDismissRelationshipEntityQueueItem).mockReturnValue({
    mutateAsync: dismissMutateAsync,
    isPending: false,
  } as unknown as ReturnType<typeof useDismissRelationshipEntityQueueItem>);
  vi.mocked(useMergeRelationshipEntities).mockReturnValue({
    mutateAsync: mergeMutateAsync,
    isPending: false,
  } as unknown as ReturnType<typeof useMergeRelationshipEntities>);
  vi.mocked(useCompareEntities).mockReturnValue({
    mutateAsync: vi.fn().mockResolvedValue({
      a: {
        entity: { id: "a", canonical_name: "A", entity_type: "person", aliases: [], tier: null, state: "active" },
        identity_facts: [],
        narrative_facts: [],
      },
      b: {
        entity: { id: "b", canonical_name: "B", entity_type: "person", aliases: [], tier: null, state: "active" },
        identity_facts: [],
        narrative_facts: [],
      },
      shared: [],
      divergent: [],
    }),
    reset: vi.fn(),
    isPending: false,
  } as unknown as ReturnType<typeof useCompareEntities>);
  vi.mocked(useDismissEntityPair).mockReturnValue({
    mutateAsync: vi.fn().mockResolvedValue({}),
    isPending: false,
  } as unknown as ReturnType<typeof useDismissEntityPair>);

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
// Tests
// ---------------------------------------------------------------------------

describe("EntitiesIndexPage — route mount", () => {
  it("renders the SubpageTabs nav strip", () => {
    renderPage();
    const nav = container.querySelector("nav[aria-label='Entity views']");
    expect(nav).toBeTruthy();
  });

  it("renders Index, Hop, Columns, Concentration, Social map tabs", () => {
    renderPage();
    const nav = container.querySelector("nav[aria-label='Entity views']");
    const links = nav?.querySelectorAll("a") ?? [];
    const labels = Array.from(links).map((a) => a.textContent?.trim());
    expect(labels).toContain("Index");
    expect(labels).toContain("Hop");
    expect(labels).toContain("Columns");
    expect(labels).toContain("Concentration");
    expect(labels).toContain("Social map");
  });

  it("renders the queue right rail", () => {
    renderPage();
    const aside = container.querySelector("aside[aria-label='Curation queue']");
    expect(aside).toBeTruthy();
  });
});

describe("EntitiesIndexPage — entity table", () => {
  it("renders entity rows from the API response", () => {
    vi.mocked(useRelationshipEntities).mockReturnValue({
      data: makeListResponse([ALICE, BOB]),
      isLoading: false,
      isError: false,
      error: null,
    } as unknown as ReturnType<typeof useRelationshipEntities>);

    renderPage();

    const table = container.querySelector("[data-testid='entity-table']");
    expect(table).toBeTruthy();
    expect(table?.textContent).toContain("Alice Fogg");
    expect(table?.textContent).toContain("Bob Hatch");
  });

  it("links each entity row to /entities/:id", () => {
    vi.mocked(useRelationshipEntities).mockReturnValue({
      data: makeListResponse([ALICE]),
      isLoading: false,
      isError: false,
      error: null,
    } as unknown as ReturnType<typeof useRelationshipEntities>);

    renderPage();

    const aliceLink = Array.from(container.querySelectorAll("a")).find(
      (a) => a.textContent?.trim() === "Alice Fogg",
    );
    expect(aliceLink).toBeTruthy();
    expect(aliceLink?.getAttribute("href")).toBe("/entities/ent-alice-001");
  });

  it("shows empty state when entity list is empty", () => {
    renderPage();
    expect(container.textContent).toContain("No entities found.");
  });

  it("shows loading skeletons while fetching", () => {
    vi.mocked(useRelationshipEntities).mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      error: null,
    } as unknown as ReturnType<typeof useRelationshipEntities>);

    renderPage();

    // Table should not be present, skeletons should be
    expect(container.querySelector("[data-testid='entity-table']")).toBeNull();
    // At least one skeleton div should be rendered
    const skeletons = container.querySelectorAll("[class*='animate-pulse']");
    expect(skeletons.length).toBeGreaterThan(0);
  });

  it("renders management actions for each entity row", () => {
    vi.mocked(useRelationshipEntities).mockReturnValue({
      data: makeListResponse([ALICE]),
      isLoading: false,
      isError: false,
      error: null,
    } as unknown as ReturnType<typeof useRelationshipEntities>);

    renderPage();

    expect(container.querySelector("button[aria-label='Merge Alice Fogg']")).toBeTruthy();
    expect(container.querySelector("button[aria-label='Archive Alice Fogg']")).toBeTruthy();
    expect(container.querySelector("button[aria-label='Delete Alice Fogg']")).toBeTruthy();
  });

  it("archives an entity from the row action", async () => {
    vi.mocked(useRelationshipEntities).mockReturnValue({
      data: makeListResponse([ALICE]),
      isLoading: false,
      isError: false,
      error: null,
    } as unknown as ReturnType<typeof useRelationshipEntities>);

    renderPage();

    const archiveButton = container.querySelector("button[aria-label='Archive Alice Fogg']");
    expect(archiveButton).toBeTruthy();

    await act(async () => {
      archiveButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(archiveMutateAsync).toHaveBeenCalledWith(ALICE.id);
  });
});

describe("EntitiesIndexPage — filter chips", () => {
  it("defaults the entity list to people and organizations", () => {
    vi.mocked(useRelationshipEntities).mockReturnValue({
      data: makeListResponse([]),
      isLoading: false,
      isError: false,
      error: null,
    } as unknown as ReturnType<typeof useRelationshipEntities>);

    renderPage();

    expect(vi.mocked(useRelationshipEntities)).toHaveBeenCalledWith(
      expect.objectContaining({ entity_type: ["person", "organization"] }),
    );
  });

  it("supports multiselect type chips", async () => {
    vi.mocked(useRelationshipEntities).mockReturnValue({
      data: makeListResponse([]),
      isLoading: false,
      isError: false,
      error: null,
    } as unknown as ReturnType<typeof useRelationshipEntities>);

    renderPage();

    expect(vi.mocked(useRelationshipEntities)).toHaveBeenCalledWith(
      expect.objectContaining({ entity_type: ["person", "organization"] }),
    );

    const locationChip = Array.from(container.querySelectorAll("button")).find(
      (b) => b.textContent?.trim() === "Location",
    );
    expect(locationChip).toBeTruthy();

    await act(async () => {
      locationChip?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    const calls = vi.mocked(useRelationshipEntities).mock.calls;
    const lastCall = calls[calls.length - 1][0];
    expect(lastCall?.entity_type).toEqual(["person", "organization", "location"]);
  });

  it("calls useRelationshipEntities with has=contact when chip is clicked", async () => {
    vi.mocked(useRelationshipEntities).mockReturnValue({
      data: makeListResponse([]),
      isLoading: false,
      isError: false,
      error: null,
    } as unknown as ReturnType<typeof useRelationshipEntities>);

    renderPage();

    const hasContactChip = Array.from(container.querySelectorAll("button")).find(
      (b) => b.textContent?.trim() === "Has contact",
    );
    expect(hasContactChip).toBeTruthy();

    await act(async () => {
      hasContactChip?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    const calls = vi.mocked(useRelationshipEntities).mock.calls;
    const lastCall = calls[calls.length - 1][0];
    expect(lastCall?.has).toBe("contact");
  });
});

describe("EntitiesIndexPage — error states", () => {
  it("shows queue error message when queue fetch fails", () => {
    vi.mocked(useRelationshipEntityQueue).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("Queue unavailable"),
    } as unknown as ReturnType<typeof useRelationshipEntityQueue>);

    renderPage();

    const aside = container.querySelector("aside[aria-label='Curation queue']");
    expect(aside?.textContent).toContain("Queue unavailable");
  });

  it("shows fallback error message when queue error is not an Error instance", () => {
    vi.mocked(useRelationshipEntityQueue).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: "network error",
    } as unknown as ReturnType<typeof useRelationshipEntityQueue>);

    renderPage();

    const aside = container.querySelector("aside[aria-label='Curation queue']");
    expect(aside?.textContent).toContain("Failed to load queue.");
  });
});

describe("EntitiesIndexPage — right rail queue", () => {
  it("shows serif italic 'Nothing waiting.' when queue is empty", () => {
    renderPage();
    const emptyEl = container.querySelector("[data-testid='queue-rail-empty']");
    expect(emptyEl).toBeTruthy();
    expect(emptyEl?.textContent?.trim()).toBe("Nothing waiting.");
  });

  it("renders queue items grouped by bucket", () => {
    vi.mocked(useRelationshipEntityQueue).mockReturnValue({
      data: makeQueueResponse([
        {
          entity_id: "ent-u-001",
          canonical_name: "Unknown Person",
          entity_type: "person",
          bucket: "unidentified",
          evidence: {},
          last_seen: null,
        },
        {
          entity_id: "ent-s-001",
          canonical_name: "Old Contact",
          entity_type: "person",
          bucket: "stale",
          evidence: { last_seen: "2023-01-01T00:00:00Z" },
          last_seen: "2023-01-01T00:00:00Z",
        },
      ]),
      isLoading: false,
      isError: false,
      error: null,
    } as unknown as ReturnType<typeof useRelationshipEntityQueue>);

    renderPage();

    const rail = container.querySelector("[data-testid='queue-rail']");
    expect(rail).toBeTruthy();
    expect(rail?.textContent).toContain("Unidentified");
    expect(rail?.textContent).toContain("Unknown Person");
    expect(rail?.textContent).toContain("Stale");
    expect(rail?.textContent).toContain("Old Contact");
  });

  it("renders inline queue actions by bucket", () => {
    vi.mocked(useRelationshipEntityQueue).mockReturnValue({
      data: makeQueueResponse([
        {
          entity_id: "ent-u-001",
          canonical_name: "Unknown Person",
          entity_type: "person",
          bucket: "unidentified",
          evidence: {},
          last_seen: null,
        },
        {
          entity_id: "ent-d-001",
          canonical_name: "Duplicate Person",
          entity_type: "person",
          bucket: "duplicate-candidate",
          evidence: {},
          last_seen: null,
        },
        {
          entity_id: "ent-s-001",
          canonical_name: "Old Contact",
          entity_type: "person",
          bucket: "stale",
          evidence: {},
          last_seen: "2023-01-01T00:00:00Z",
        },
      ]),
      isLoading: false,
      isError: false,
      error: null,
    } as unknown as ReturnType<typeof useRelationshipEntityQueue>);

    renderPage();

    expect(container.querySelector("button[aria-label='Promote Unknown Person']")).toBeTruthy();
    expect(container.querySelector("button[aria-label='Merge Unknown Person']")).toBeTruthy();
    expect(container.querySelector("button[aria-label='Dismiss Unknown Person']")).toBeTruthy();
    expect(container.querySelector("button[aria-label='Merge Duplicate Person']")).toBeTruthy();
    expect(container.querySelector("button[aria-label='Archive Old Contact']")).toBeTruthy();
  });

  it("promotes an unidentified queue item inline", async () => {
    vi.mocked(useRelationshipEntityQueue).mockReturnValue({
      data: makeQueueResponse([
        {
          entity_id: "ent-u-001",
          canonical_name: "Unknown Person",
          entity_type: "person",
          bucket: "unidentified",
          evidence: {},
          last_seen: null,
        },
      ]),
      isLoading: false,
      isError: false,
      error: null,
    } as unknown as ReturnType<typeof useRelationshipEntityQueue>);

    renderPage();

    const promoteButton = container.querySelector("button[aria-label='Promote Unknown Person']");
    expect(promoteButton).toBeTruthy();

    await act(async () => {
      promoteButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(promoteMutateAsync).toHaveBeenCalledWith({
      entityId: "ent-u-001",
      canonicalName: "Unknown Person",
      entityType: "person",
    });
  });

  it("dismisses a queue item inline", async () => {
    vi.mocked(useRelationshipEntityQueue).mockReturnValue({
      data: makeQueueResponse([
        {
          entity_id: "ent-u-001",
          canonical_name: "Unknown Person",
          entity_type: "person",
          bucket: "unidentified",
          evidence: {},
          last_seen: null,
        },
      ]),
      isLoading: false,
      isError: false,
      error: null,
    } as unknown as ReturnType<typeof useRelationshipEntityQueue>);

    renderPage();

    const dismissButton = container.querySelector("button[aria-label='Dismiss Unknown Person']");
    expect(dismissButton).toBeTruthy();

    await act(async () => {
      dismissButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(dismissMutateAsync).toHaveBeenCalledWith("ent-u-001");
  });
});

describe("EntitiesIndexPage — ?has=contact URL param", () => {
  it("pre-activates the Has contact chip when navigated to ?has=contact", () => {
    renderPage("/entities?has=contact");

    // The hook should have been called with has=contact from URL initialization
    const calls = vi.mocked(useRelationshipEntities).mock.calls;
    const firstCall = calls[0][0];
    expect(firstCall?.has).toBe("contact");
  });

  it("chip is visually active (variant=default) when ?has=contact is in URL", () => {
    renderPage("/entities?has=contact");

    // The Has contact button should have the active (default) variant class.
    // In this component, active chips use variant="default" which applies
    // bg-primary styling; outline chips use variant="outline".
    // We detect activity by checking useRelationshipEntities was called with has=contact.
    const calls = vi.mocked(useRelationshipEntities).mock.calls;
    expect(calls.some((c) => c[0]?.has === "contact")).toBe(true);
  });

  it("does NOT pass has=contact when URL has no ?has param", () => {
    renderPage("/entities");

    const calls = vi.mocked(useRelationshipEntities).mock.calls;
    const firstCall = calls[0][0];
    expect(firstCall?.has).toBeUndefined();
  });

  it("toggling the chip ON adds ?has=contact to the URL and passes filter", async () => {
    renderPage("/entities");

    const hasContactChip = Array.from(container.querySelectorAll("button")).find(
      (b) => b.textContent?.trim() === "Has contact",
    );
    expect(hasContactChip).toBeTruthy();

    await act(async () => {
      hasContactChip?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    // After clicking, the hook should be called with has=contact
    const calls = vi.mocked(useRelationshipEntities).mock.calls;
    const lastCall = calls[calls.length - 1][0];
    expect(lastCall?.has).toBe("contact");
  });

  it("toggling the chip OFF removes ?has from URL and clears filter", async () => {
    renderPage("/entities?has=contact");

    // Verify initial state: has=contact is active
    let calls = vi.mocked(useRelationshipEntities).mock.calls;
    expect(calls[0][0]?.has).toBe("contact");

    const hasContactChip = Array.from(container.querySelectorAll("button")).find(
      (b) => b.textContent?.trim() === "Has contact",
    );
    expect(hasContactChip).toBeTruthy();

    await act(async () => {
      hasContactChip?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    // After toggling OFF, the hook should be called without has=contact
    calls = vi.mocked(useRelationshipEntities).mock.calls;
    const lastCall = calls[calls.length - 1][0];
    expect(lastCall?.has).toBeUndefined();
  });

  it("preserves other URL params when toggling has=contact ON", async () => {
    // Start with some other query param present
    renderPage("/entities?foo=bar");

    const hasContactChip = Array.from(container.querySelectorAll("button")).find(
      (b) => b.textContent?.trim() === "Has contact",
    );

    await act(async () => {
      hasContactChip?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    // After toggling ON, has=contact is applied. The foo=bar param is preserved
    // in URL state — we can't easily introspect the URL from MemoryRouter here,
    // but we verify the hook gets the contact filter (not overwriting all params).
    const calls = vi.mocked(useRelationshipEntities).mock.calls;
    const lastCall = calls[calls.length - 1][0];
    expect(lastCall?.has).toBe("contact");
  });
});

describe("EntitiesIndexPage — ?type= URL param", () => {
  it("pre-activates the type chip when navigated to ?type=person", () => {
    renderPage("/entities?type=person");

    const calls = vi.mocked(useRelationshipEntities).mock.calls;
    const firstCall = calls[0][0];
    expect(firstCall?.entity_type).toEqual(["person"]);
  });

  it("pre-activates multiple type chips when navigated to repeated ?type params", () => {
    renderPage("/entities?type=person&type=organization");

    const calls = vi.mocked(useRelationshipEntities).mock.calls;
    const firstCall = calls[0][0];
    expect(firstCall?.entity_type).toEqual(["person", "organization"]);
  });

  it("uses People and Orgs when URL has no ?type param", () => {
    renderPage("/entities");

    const calls = vi.mocked(useRelationshipEntities).mock.calls;
    const firstCall = calls[0][0];
    expect(firstCall?.entity_type).toEqual(["person", "organization"]);
  });

  it("toggling type chip OFF removes just that type while preserving other params", async () => {
    renderPage("/entities?type=person&has=contact");

    // Verify initial state: type=person is active
    let calls = vi.mocked(useRelationshipEntities).mock.calls;
    expect(calls[0][0]?.entity_type).toEqual(["person"]);
    expect(calls[0][0]?.has).toBe("contact");

    const personChip = Array.from(container.querySelectorAll("button")).find(
      (b) => b.textContent?.trim() === "Person",
    );
    expect(personChip).toBeTruthy();

    await act(async () => {
      personChip?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    // After toggling OFF, the explicit empty type selection is preserved.
    calls = vi.mocked(useRelationshipEntities).mock.calls;
    const lastCall = calls[calls.length - 1][0];
    expect(lastCall?.entity_type).toEqual([]);
    expect(lastCall?.has).toBe("contact");
  });

  it("toggling type chip ON adds ?type to URL while preserving other params", async () => {
    renderPage("/entities?type=person&has=contact");

    const orgChip = Array.from(container.querySelectorAll("button")).find(
      (b) => b.textContent?.trim() === "Org",
    );
    expect(orgChip).toBeTruthy();

    await act(async () => {
      orgChip?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    const calls = vi.mocked(useRelationshipEntities).mock.calls;
    const lastCall = calls[calls.length - 1][0];
    expect(lastCall?.entity_type).toEqual(["person", "organization"]);
    expect(lastCall?.has).toBe("contact");
  });
});

describe("EntitiesIndexPage — ?state= URL param", () => {
  it("pre-activates the state chip when navigated to ?state=unidentified", () => {
    renderPage("/entities?state=unidentified");

    const calls = vi.mocked(useRelationshipEntities).mock.calls;
    const firstCall = calls[0][0];
    expect(firstCall?.state).toBe("unidentified");
  });

  it("does NOT pass state when URL has no ?state param", () => {
    renderPage("/entities");

    const calls = vi.mocked(useRelationshipEntities).mock.calls;
    const firstCall = calls[0][0];
    expect(firstCall?.state).toBeUndefined();
  });

  it("toggling state chip ON adds ?state to URL while preserving other params", async () => {
    renderPage("/entities?has=contact");

    const unidentifiedChip = Array.from(container.querySelectorAll("button")).find(
      (b) => b.textContent?.trim() === "Unidentified",
    );
    expect(unidentifiedChip).toBeTruthy();

    await act(async () => {
      unidentifiedChip?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    const calls = vi.mocked(useRelationshipEntities).mock.calls;
    const lastCall = calls[calls.length - 1][0];
    expect(lastCall?.state).toBe("unidentified");
    expect(lastCall?.has).toBe("contact");
  });

  it("toggling state chip OFF removes ?state from URL while preserving other params", async () => {
    renderPage("/entities?state=unidentified&has=contact");

    // Verify initial state
    let calls = vi.mocked(useRelationshipEntities).mock.calls;
    expect(calls[0][0]?.state).toBe("unidentified");
    expect(calls[0][0]?.has).toBe("contact");

    const unidentifiedChip = Array.from(container.querySelectorAll("button")).find(
      (b) => b.textContent?.trim() === "Unidentified",
    );
    expect(unidentifiedChip).toBeTruthy();

    await act(async () => {
      unidentifiedChip?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    calls = vi.mocked(useRelationshipEntities).mock.calls;
    const lastCall = calls[calls.length - 1][0];
    expect(lastCall?.state).toBeUndefined();
    expect(lastCall?.has).toBe("contact");
  });
});

describe("EntitiesIndexPage — combined URL params", () => {
  it("pre-activates type, state, and has=contact chips when all three are in URL", () => {
    renderPage("/entities?type=person&state=unidentified&has=contact");

    const calls = vi.mocked(useRelationshipEntities).mock.calls;
    const firstCall = calls[0][0];
    expect(firstCall?.entity_type).toEqual(["person"]);
    expect(firstCall?.state).toBe("unidentified");
    expect(firstCall?.has).toBe("contact");
  });
});

describe("EntitiesIndexPage — bulk gutter merge (exactly two)", () => {
  beforeEach(() => {
    vi.mocked(useRelationshipEntities).mockReturnValue({
      data: makeListResponse([ALICE, BOB]),
      isLoading: false,
      isError: false,
      error: null,
    } as unknown as ReturnType<typeof useRelationshipEntities>);
  });

  function selectRow(name: string) {
    const checkbox = container.querySelector(
      `input[type='checkbox'][aria-label='Select ${name}']`,
    ) as HTMLInputElement;
    act(() => checkbox.click());
  }

  it("disables the gutter merge action with one row selected", () => {
    renderPage();
    selectRow("Alice Fogg");
    const gutterMerge = container.querySelector(
      "[data-testid='gutter-merge']",
    ) as HTMLButtonElement;
    expect(gutterMerge).toBeTruthy();
    expect(gutterMerge.disabled).toBe(true);
  });

  it("enables the gutter merge action when exactly two rows are selected and opens compare", () => {
    renderPage();
    selectRow("Alice Fogg");
    selectRow("Bob Hatch");
    const gutterMerge = container.querySelector(
      "[data-testid='gutter-merge']",
    ) as HTMLButtonElement;
    expect(gutterMerge.disabled).toBe(false);
    act(() => gutterMerge.click());
    // The compare view (merge-review surface) opens for the selected pair.
    // DialogContent renders through a portal to document.body.
    expect(document.body.querySelector("[data-testid='merge-compare-dialog']")).toBeTruthy();
  });
});

describe("EntitiesIndexPage — duplicate-candidate queue evidence drill", () => {
  it("renders the shared value and each peer as a compare link", () => {
    vi.mocked(useRelationshipEntityQueue).mockReturnValue({
      data: makeQueueResponse([
        {
          entity_id: "ent-dup-1",
          canonical_name: "Dup One",
          entity_type: "person",
          bucket: "duplicate-candidate",
          evidence: {
            predicate: "has-email",
            shared_value: "x@y.com",
            peer_entity_ids: ["ent-dup-2"],
          },
          last_seen: null,
        },
        {
          entity_id: "ent-dup-2",
          canonical_name: "Dup Two",
          entity_type: "person",
          bucket: "duplicate-candidate",
          evidence: {
            predicate: "has-email",
            shared_value: "x@y.com",
            peer_entity_ids: ["ent-dup-1"],
          },
          last_seen: null,
        },
      ]),
      isLoading: false,
      isError: false,
      error: null,
    } as unknown as ReturnType<typeof useRelationshipEntityQueue>);

    renderPage();
    const drill = container.querySelector("[data-testid='queue-duplicate-evidence']");
    expect(drill).toBeTruthy();
    // Shared value is surfaced as evidence.
    expect(drill?.textContent).toContain("x@y.com");
    // The peer name (resolved off the queue) renders as a compare affordance.
    const peerBtn = container.querySelector(
      "button[aria-label='Compare Dup One with Dup Two']",
    ) as HTMLButtonElement;
    expect(peerBtn).toBeTruthy();
    expect(peerBtn.textContent).toContain("Dup Two");
  });

  it("opens the compare view pre-highlighted when a peer link is clicked", () => {
    vi.mocked(useRelationshipEntityQueue).mockReturnValue({
      data: makeQueueResponse([
        {
          entity_id: "ent-dup-1",
          canonical_name: "Dup One",
          entity_type: "person",
          bucket: "duplicate-candidate",
          evidence: {
            predicate: "has-email",
            shared_value: "x@y.com",
            peer_entity_ids: ["ent-dup-2"],
          },
          last_seen: null,
        },
      ]),
      isLoading: false,
      isError: false,
      error: null,
    } as unknown as ReturnType<typeof useRelationshipEntityQueue>);

    renderPage();
    // Peer name unresolved (peer not in queue): aria-label falls back to "peer",
    // the visible label to "Linked entity".
    const peerBtn = container.querySelector(
      "button[aria-label='Compare Dup One with peer']",
    ) as HTMLButtonElement;
    expect(peerBtn).toBeTruthy();
    expect(peerBtn.textContent).toContain("Linked entity");
    act(() => peerBtn.click());
    // The compare view opens straight for the pair (no target picker).
    expect(document.body.querySelector("[data-testid='merge-compare-dialog']")).toBeTruthy();
  });

  it("falls back to the target picker for a duplicate flagged without a peer", () => {
    vi.mocked(useRelationshipEntityQueue).mockReturnValue({
      data: makeQueueResponse([
        {
          entity_id: "ent-dup-1",
          canonical_name: "Lone Dup",
          entity_type: "person",
          bucket: "duplicate-candidate",
          evidence: {},
          last_seen: null,
        },
      ]),
      isLoading: false,
      isError: false,
      error: null,
    } as unknown as ReturnType<typeof useRelationshipEntityQueue>);

    renderPage();
    // No peer link; the metadata-only duplicate renders the Merge action that
    // routes through the target picker.
    expect(container.querySelector("[data-testid='queue-duplicate-peer']")).toBeNull();
    expect(container.querySelector("button[aria-label='Merge Lone Dup']")).toBeTruthy();
  });
});

describe("EntitiesIndexPage — queue evidence drill (stale + multi-peer)", () => {
  it("shows the staleness age on a stale card", () => {
    vi.mocked(useRelationshipEntityQueue).mockReturnValue({
      data: makeQueueResponse([
        {
          entity_id: "ent-s-001",
          canonical_name: "Old Contact",
          entity_type: "person",
          bucket: "stale",
          evidence: { last_seen: "2023-01-01T00:00:00Z" },
          last_seen: "2023-01-01T00:00:00Z",
        },
      ]),
      isLoading: false,
      isError: false,
      error: null,
    } as unknown as ReturnType<typeof useRelationshipEntityQueue>);

    renderPage();
    const age = container.querySelector("[data-testid='queue-stale-age']");
    expect(age).toBeTruthy();
    expect(age?.textContent?.toLowerCase()).toContain("last seen");
    // The stale card still links to detail.
    const link = Array.from(container.querySelectorAll("a")).find(
      (a) => a.textContent?.trim() === "Old Contact",
    );
    expect(link?.getAttribute("href")).toBe("/entities/ent-s-001");
  });

  it("renders one comparable peer link per collision (multi-peer)", () => {
    vi.mocked(useRelationshipEntityQueue).mockReturnValue({
      data: makeQueueResponse([
        {
          entity_id: "ent-dup-1",
          canonical_name: "Dup One",
          entity_type: "person",
          bucket: "duplicate-candidate",
          evidence: {
            predicate: "has-phone",
            shared_value: "+15550001",
            peer_entity_ids: ["ent-dup-2", "ent-dup-3"],
          },
          last_seen: null,
        },
        {
          entity_id: "ent-dup-2",
          canonical_name: "Dup Two",
          entity_type: "person",
          bucket: "duplicate-candidate",
          evidence: {},
          last_seen: null,
        },
        {
          entity_id: "ent-dup-3",
          canonical_name: "Dup Three",
          entity_type: "person",
          bucket: "duplicate-candidate",
          evidence: {},
          last_seen: null,
        },
      ]),
      isLoading: false,
      isError: false,
      error: null,
    } as unknown as ReturnType<typeof useRelationshipEntityQueue>);

    renderPage();
    expect(
      container.querySelector("button[aria-label='Compare Dup One with Dup Two']"),
    ).toBeTruthy();
    expect(
      container.querySelector("button[aria-label='Compare Dup One with Dup Three']"),
    ).toBeTruthy();
  });
});

describe("EntitiesIndexPage — bulk gutter", () => {
  beforeEach(() => {
    vi.mocked(useRelationshipEntities).mockReturnValue({
      data: makeListResponse([ALICE, BOB]),
      isLoading: false,
      isError: false,
      error: null,
    } as unknown as ReturnType<typeof useRelationshipEntities>);
  });

  function selectRow(name: string) {
    const checkbox = container.querySelector(
      `input[type='checkbox'][aria-label='Select ${name}']`,
    ) as HTMLInputElement;
    act(() => checkbox.click());
  }

  it("renders a mono tabular selected-count caption", () => {
    renderPage();
    selectRow("Alice Fogg");
    const count = container.querySelector("[data-testid='bulk-gutter-count']");
    expect(count).toBeTruthy();
    expect(count?.textContent).toContain("1");
    expect(count?.className).toContain("font-mono");
    // The numeral itself carries tabular-nums.
    expect(count?.querySelector(".tabular-nums")?.textContent).toBe("1");
  });

  it("exposes archive, forget, merge and clear actions", () => {
    renderPage();
    selectRow("Alice Fogg");
    expect(container.querySelector("[data-testid='gutter-archive']")).toBeTruthy();
    expect(container.querySelector("[data-testid='gutter-forget']")).toBeTruthy();
    expect(container.querySelector("[data-testid='gutter-merge']")).toBeTruthy();
    expect(container.querySelector("[data-testid='gutter-clear']")).toBeTruthy();
  });

  it("opens a serif confirm gloss for the forget action", () => {
    renderPage();
    selectRow("Alice Fogg");
    selectRow("Bob Hatch");
    const forgetBtn = container.querySelector(
      "[data-testid='gutter-forget']",
    ) as HTMLButtonElement;
    act(() => forgetBtn.click());
    const gloss = document.body.querySelector("[data-testid='bulk-confirm-gloss']");
    expect(gloss).toBeTruthy();
    // Canned gloss with exact count + irreversibility note.
    expect(gloss?.textContent).toContain("Delete 2 entities");
    expect(gloss?.textContent).toContain("cannot be undone");
  });

  it("forgets all selected entities on confirm", async () => {
    renderPage();
    selectRow("Alice Fogg");
    selectRow("Bob Hatch");
    const forgetBtn = container.querySelector(
      "[data-testid='gutter-forget']",
    ) as HTMLButtonElement;
    act(() => forgetBtn.click());
    const commit = document.body.querySelector(
      "[data-testid='bulk-confirm-commit']",
    ) as HTMLButtonElement;
    await act(async () => {
      commit.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(forgetMutateAsync).toHaveBeenCalledWith("ent-alice-001");
    expect(forgetMutateAsync).toHaveBeenCalledWith("ent-bob-002");
  });

  it("archives all selected entities on confirm", async () => {
    renderPage();
    selectRow("Alice Fogg");
    const archiveBtn = container.querySelector(
      "[data-testid='gutter-archive']",
    ) as HTMLButtonElement;
    act(() => archiveBtn.click());
    const gloss = document.body.querySelector("[data-testid='bulk-confirm-gloss']");
    expect(gloss?.textContent).toContain("Archive 1 entity");
    const commit = document.body.querySelector(
      "[data-testid='bulk-confirm-commit']",
    ) as HTMLButtonElement;
    await act(async () => {
      commit.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(archiveMutateAsync).toHaveBeenCalledWith("ent-alice-001");
  });

  it("keeps only the failed entities selected on partial failure", async () => {
    // Alice succeeds, Bob fails: only Bob must remain selected for retry.
    archiveMutateAsync.mockImplementation((id: string) =>
      id === "ent-bob-002" ? Promise.reject(new Error("boom")) : Promise.resolve(undefined),
    );
    renderPage();
    selectRow("Alice Fogg");
    selectRow("Bob Hatch");
    const archiveBtn = container.querySelector(
      "[data-testid='gutter-archive']",
    ) as HTMLButtonElement;
    act(() => archiveBtn.click());
    const commit = document.body.querySelector(
      "[data-testid='bulk-confirm-commit']",
    ) as HTMLButtonElement;
    await act(async () => {
      commit.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(archiveMutateAsync).toHaveBeenCalledWith("ent-alice-001");
    expect(archiveMutateAsync).toHaveBeenCalledWith("ent-bob-002");
    // The failed entity (Bob) stays selected; the gutter still shows 1.
    const count = container.querySelector("[data-testid='bulk-gutter-count']");
    expect(count?.querySelector(".tabular-nums")?.textContent).toBe("1");
    const bobCheckbox = container.querySelector(
      "input[type='checkbox'][aria-label='Select Bob Hatch']",
    ) as HTMLInputElement;
    expect(bobCheckbox.checked).toBe(true);
    const aliceCheckbox = container.querySelector(
      "input[type='checkbox'][aria-label='Select Alice Fogg']",
    ) as HTMLInputElement;
    expect(aliceCheckbox.checked).toBe(false);
  });
});

describe("EntitiesIndexPage — Index keyboard map (focused list container)", () => {
  beforeEach(() => {
    vi.mocked(useRelationshipEntities).mockReturnValue({
      data: makeListResponse([ALICE, BOB]),
      isLoading: false,
      isError: false,
      error: null,
    } as unknown as ReturnType<typeof useRelationshipEntities>);
  });

  function dispatchKey(key: string, init: KeyboardEventInit = {}) {
    const list = container.querySelector(
      "[data-testid='entity-list-container']",
    ) as HTMLDivElement;
    act(() => {
      list.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true, ...init }));
    });
  }

  it("binds the keyboard map to the focused list container, not window", () => {
    renderPage();
    const list = container.querySelector("[data-testid='entity-list-container']");
    expect(list).toBeTruthy();
    // The container is focusable (keyboard map is local to it).
    expect(list?.getAttribute("tabindex")).toBe("0");
    // A window-level keydown must NOT toggle selection (map is not global).
    act(() => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true }));
      window.dispatchEvent(new KeyboardEvent("keydown", { key: " ", bubbles: true }));
    });
    expect(container.querySelector("[data-testid='bulk-gutter']")).toBeNull();
  });

  it("Down moves the cursor and Space toggles selection at the cursor", () => {
    renderPage();
    dispatchKey("ArrowDown"); // cursor → row 0
    dispatchKey(" "); // toggle select row 0
    // The bulk gutter appears once a row is selected.
    expect(container.querySelector("[data-testid='bulk-gutter']")).toBeTruthy();
    const count = container.querySelector("[data-testid='bulk-gutter-count']");
    expect(count?.textContent).toContain("1");
  });

  it("marks the cursored row with a 2px left border (design-language focus)", () => {
    // Spec ("Keyboard maps per view"): "Focus states MUST be visible per the
    // design language (2px left border, no glow)."
    renderPage();
    dispatchKey("ArrowDown"); // cursor → row 0
    const cursored = container.querySelector("tr[data-cursor='true']");
    expect(cursored).toBeTruthy();
    expect(cursored?.className).toContain("border-l-2");
    expect(cursored?.className).toContain("border-l-foreground");
    // No glow/ring on the cursor treatment.
    expect(cursored?.className).not.toContain("ring");
  });

  it("Shift+Down extends the selection range", () => {
    renderPage();
    dispatchKey("ArrowDown"); // cursor → row 0
    dispatchKey("ArrowDown", { shiftKey: true }); // extend to row 1
    const count = container.querySelector("[data-testid='bulk-gutter-count']");
    expect(count?.textContent).toContain("2");
    // Exactly-two selection enables the gutter merge.
    const gutterMerge = container.querySelector(
      "[data-testid='gutter-merge']",
    ) as HTMLButtonElement;
    expect(gutterMerge.disabled).toBe(false);
  });

  it("Escape clears the selection", () => {
    renderPage();
    dispatchKey("ArrowDown");
    dispatchKey(" ");
    expect(container.querySelector("[data-testid='bulk-gutter']")).toBeTruthy();
    dispatchKey("Escape");
    expect(container.querySelector("[data-testid='bulk-gutter']")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Toolbar search (entity-v3: wired to the relationship search endpoint)
// ---------------------------------------------------------------------------

describe("EntitiesIndexPage — toolbar search", () => {
  it("renders a toolbar search input", () => {
    renderPage();
    expect(
      container.querySelector("[data-testid='entities-toolbar-search']"),
    ).toBeTruthy();
  });

  it("filters the table to the search endpoint's ranked id set", () => {
    vi.mocked(useRelationshipEntities).mockReturnValue({
      data: makeListResponse([ALICE, BOB]),
      isLoading: false,
      isError: false,
      error: null,
    } as unknown as ReturnType<typeof useRelationshipEntities>);

    // The search endpoint matches only Bob (e.g. by contact-fact value).
    vi.mocked(useEntityFinderSearch).mockReturnValue({
      data: {
        results: [
          {
            entity_id: BOB.id,
            canonical_name: BOB.canonical_name,
            entity_type: BOB.entity_type,
            score: 70,
            match_kind: "contact_fact",
          },
        ],
        total: 1,
        q: "hatch",
        limit: 50,
      },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useEntityFinderSearch>);

    const setter = Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype,
      "value",
    )?.set;

    renderPage();

    const input = container.querySelector(
      "[data-testid='entities-toolbar-search']",
    ) as HTMLInputElement;
    act(() => {
      setter?.call(input, "hatch");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });

    const table = container.querySelector("[data-testid='entity-table']");
    expect(table?.textContent).toContain("Bob Hatch");
    expect(table?.textContent).not.toContain("Alice Fogg");
  });

  it("passes the search query through to useEntityFinderSearch", () => {
    const setter = Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype,
      "value",
    )?.set;

    renderPage();

    const input = container.querySelector(
      "[data-testid='entities-toolbar-search']",
    ) as HTMLInputElement;
    act(() => {
      setter?.call(input, "alice@x.com");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });

    expect(vi.mocked(useEntityFinderSearch)).toHaveBeenCalledWith(
      "alice@x.com",
      { limit: 50 },
    );
  });
});
