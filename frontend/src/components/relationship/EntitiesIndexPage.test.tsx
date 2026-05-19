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
  // Other exports from use-entities that the module re-exports
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
}));

import {
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

function renderPage() {
  const qc = makeQueryClient();
  act(() => {
    root.render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={["/entities"]}>
          <EntitiesIndexPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );
  });
}

beforeEach(() => {
  vi.resetAllMocks();

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
});

describe("EntitiesIndexPage — filter chips", () => {
  it("calls useRelationshipEntities with entity_type when type chip is clicked", async () => {
    vi.mocked(useRelationshipEntities).mockReturnValue({
      data: makeListResponse([]),
      isLoading: false,
      isError: false,
      error: null,
    } as unknown as ReturnType<typeof useRelationshipEntities>);

    renderPage();

    // Verify initial call with no filters
    expect(vi.mocked(useRelationshipEntities)).toHaveBeenCalledWith(
      expect.objectContaining({ entity_type: undefined }),
    );

    // Click "Person" chip
    const personChip = Array.from(container.querySelectorAll("button")).find(
      (b) => b.textContent?.trim() === "Person",
    );
    expect(personChip).toBeTruthy();

    await act(async () => {
      personChip?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    // Should now be called with entity_type = "person"
    const calls = vi.mocked(useRelationshipEntities).mock.calls;
    const lastCall = calls[calls.length - 1][0];
    expect(lastCall?.entity_type).toBe("person");
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
});
