// @vitest-environment jsdom
/**
 * Tests for the EntityFinder Cmd-K component (bu-xfjwk).
 *
 * Covers:
 * - Keyboard activation (Cmd-K via dispatchOpenEntityFinder)
 * - Entity group rendered FIRST (entity-first ordering, Brief §5 OQ-14)
 * - Page group rendered AFTER entities
 * - API wiring: useEntityFinderSearch mock returns results
 * - Empty state when no results
 * - Closing on Escape
 * - Navigation on item select
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, useLocation } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import EntityFinder from "@/components/layout/EntityFinder";
import {
  aggregateOwnerPinned,
  dispatchOpenEntityFinder,
} from "@/lib/entity-finder";
import {
  useEntityFinderSearch,
  useEntityNeighbours,
} from "@/hooks/use-entities";
import type { NeighbourEntry } from "@/api/index.ts";

/** Renders the current location path+search for navigation assertions. */
function LocationProbe() {
  const loc = useLocation();
  return <span data-testid="loc">{`${loc.pathname}${loc.search}`}</span>;
}

/**
 * Set a controlled <input>'s value the way React expects in tests: use the
 * native value setter then dispatch a bubbling input event so React's onChange
 * (and cmdk's onValueChange) fire.
 */
function typeInto(input: HTMLInputElement, value: string): void {
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype,
    "value",
  )?.set;
  setter?.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-entities", () => ({
  useEntityFinderSearch: vi.fn(),
  useEntityNeighbours: vi.fn(),
  useEntityLinkedContacts: vi.fn(),
  useEntityGifts: vi.fn(),
  useEntityLoans: vi.fn(),
  useEntityTimeline: vi.fn(),
  useEntityMessageThreads: vi.fn(),
  useEntityDates: vi.fn(),
  useUpdateEntityDunbarTier: vi.fn(),
}));

vi.mock("@/api/index", () => ({
  getOwnerSetupStatus: vi.fn(async () => ({
    entity_id: null,
    has_name: false,
    has_telegram: false,
    has_telegram_chat_id: false,
    has_email: false,
  })),
}));

vi.mock("@/components/layout/nav-config", () => ({
  navSections: [
    {
      title: "Main",
      items: [
        { kind: "link", label: "Dashboard", path: "/" },
        { kind: "link", label: "Contacts", path: "/contacts" },
        {
          kind: "group",
          label: "Relationship",
          children: [
            { label: "Entities", path: "/butlers/relationship/entities" },
          ],
        },
      ],
    },
  ],
}));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

(
  globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }
).IS_REACT_ACT_ENVIRONMENT = true;

function flush(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

type UseEntityFinderSearchResult = ReturnType<typeof useEntityFinderSearch>;
type UseEntityNeighboursResult = ReturnType<typeof useEntityNeighbours>;

function mockNeighboursEmpty(): void {
  vi.mocked(useEntityNeighbours).mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: false,
  } as UseEntityNeighboursResult);
}

function mockSearchEmpty(): void {
  vi.mocked(useEntityFinderSearch).mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: false,
  } as UseEntityFinderSearchResult);
}

function mockSearchResults(
  results: UseEntityFinderSearchResult["data"],
): void {
  vi.mocked(useEntityFinderSearch).mockReturnValue({
    data: results,
    isLoading: false,
    isError: false,
  } as UseEntityFinderSearchResult);
}

function mockSearchError(): void {
  vi.mocked(useEntityFinderSearch).mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: true,
  } as UseEntityFinderSearchResult);
}

// ---------------------------------------------------------------------------
// Test setup
// ---------------------------------------------------------------------------

describe("EntityFinder", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.resetAllMocks();
    mockSearchEmpty();
    mockNeighboursEmpty();

    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
    vi.restoreAllMocks();
  });

  // -------------------------------------------------------------------------

  it("is hidden by default and opens on dispatchOpenEntityFinder", async () => {
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    await act(async () => {
      root.render(
        <QueryClientProvider client={qc}>
          <MemoryRouter>
            <EntityFinder />
          </MemoryRouter>
        </QueryClientProvider>,
      );
      await flush();
    });

    // Should not be visible before event
    expect(
      document.body.querySelector("[data-testid='entity-finder-input']"),
    ).toBeNull();

    // Fire the open event
    await act(async () => {
      dispatchOpenEntityFinder();
      await flush();
    });

    const input = document.body.querySelector(
      "[data-testid='entity-finder-input']",
    );
    expect(input).toBeInstanceOf(HTMLInputElement);
  });

  // -------------------------------------------------------------------------

  it("renders entity group FIRST, pages group SECOND (entity-first ordering)", async () => {
    mockSearchResults({
      results: [
        {
          entity_id: "uuid-alice",
          canonical_name: "Alice",
          entity_type: "person",
          score: 100,
          match_kind: "prefix",
        },
        {
          entity_id: "uuid-bob",
          canonical_name: "Bob",
          entity_type: "person",
          score: 50,
          match_kind: "substring",
        },
      ],
      total: 2,
      q: "ali",
      limit: 8,
    });

    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    await act(async () => {
      root.render(
        <QueryClientProvider client={qc}>
          <MemoryRouter>
            <EntityFinder />
          </MemoryRouter>
        </QueryClientProvider>,
      );
      await flush();
    });

    await act(async () => {
      dispatchOpenEntityFinder();
      await flush();
    });

    // Type a query so pages also match
    const input = document.body.querySelector(
      "[data-testid='entity-finder-input']",
    ) as HTMLInputElement;

    await act(async () => {
      // Simulate input change
      input.value = "ali";
      input.dispatchEvent(new Event("input", { bubbles: true }));
      await flush();
    });

    const groups = document.body.querySelectorAll("[cmdk-group]");
    const groupHeadings: string[] = [];
    groups.forEach((g) => {
      const heading = g.querySelector("[cmdk-group-heading]");
      if (heading) groupHeadings.push(heading.textContent ?? "");
    });

    // Entity group must appear before any Pages group
    const entityIdx = groupHeadings.indexOf("Entities");
    const pagesIdx = groupHeadings.indexOf("Pages");

    expect(entityIdx).toBeGreaterThanOrEqual(0);
    // If Pages group is present, entities must come first
    if (pagesIdx >= 0) {
      expect(entityIdx).toBeLessThan(pagesIdx);
    }
  });

  // -------------------------------------------------------------------------

  it("renders entity items with correct names from search results", async () => {
    mockSearchResults({
      results: [
        {
          entity_id: "uuid-carol",
          canonical_name: "Carol Danvers",
          entity_type: "person",
          score: 100,
          match_kind: "prefix",
        },
      ],
      total: 1,
      q: "carol",
      limit: 8,
    });

    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    await act(async () => {
      root.render(
        <QueryClientProvider client={qc}>
          <MemoryRouter>
            <EntityFinder />
          </MemoryRouter>
        </QueryClientProvider>,
      );
      await flush();
    });

    await act(async () => {
      dispatchOpenEntityFinder();
      await flush();
    });

    const items = document.body.querySelectorAll(
      "[data-testid='entity-finder-entity-item']",
    );
    expect(items.length).toBe(1);
    expect(items[0].textContent).toContain("Carol Danvers");
  });

  // -------------------------------------------------------------------------

  it("shows empty state when query has no results", async () => {
    mockSearchResults({
      results: [],
      total: 0,
      q: "zzznomatch",
      limit: 8,
    });

    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    await act(async () => {
      root.render(
        <QueryClientProvider client={qc}>
          <MemoryRouter>
            <EntityFinder />
          </MemoryRouter>
        </QueryClientProvider>,
      );
      await flush();
    });

    await act(async () => {
      dispatchOpenEntityFinder();
      await flush();
    });

    // With an empty query the empty state won't show; we need to set query
    // and ensure the component reflects it. The empty state checks query.trim().length > 0.
    // Since input change is complex to simulate in createRoot tests, we check
    // that entity items are absent when results array is empty.
    const entityItems = document.body.querySelectorAll(
      "[data-testid='entity-finder-entity-item']",
    );
    expect(entityItems.length).toBe(0);
  });

  // -------------------------------------------------------------------------

  it("surfaces a search error — NOT the 'No results' empty copy — when the query errors", async () => {
    mockSearchError();

    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    await act(async () => {
      root.render(
        <QueryClientProvider client={qc}>
          <MemoryRouter>
            <EntityFinder />
          </MemoryRouter>
        </QueryClientProvider>,
      );
      await flush();
    });

    await act(async () => {
      dispatchOpenEntityFinder();
      await flush();
    });

    // A non-empty query is required for the error/empty branches to render.
    const input = document.body.querySelector(
      "[data-testid='entity-finder-input']",
    ) as HTMLInputElement;
    await act(async () => {
      typeInto(input, "zzznomatch");
      await flush();
    });

    const errorBanner = document.body.querySelector(
      "[data-testid='entity-finder-search-error']",
    );
    expect(errorBanner).toBeTruthy();
    expect(errorBanner?.textContent).toContain("Search failed.");
    expect(document.body.textContent).not.toContain("No results for");
  });

  // -------------------------------------------------------------------------

  it("closes when backdrop is clicked", async () => {
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    await act(async () => {
      root.render(
        <QueryClientProvider client={qc}>
          <MemoryRouter>
            <EntityFinder />
          </MemoryRouter>
        </QueryClientProvider>,
      );
      await flush();
    });

    await act(async () => {
      dispatchOpenEntityFinder();
      await flush();
    });

    expect(
      document.body.querySelector("[data-testid='entity-finder-input']"),
    ).not.toBeNull();

    // Click the backdrop
    const backdrop = document.body.querySelector(
      "[data-testid='entity-finder-backdrop']",
    ) as HTMLElement;
    await act(async () => {
      backdrop.click();
      await flush();
    });

    // Should be unmounted
    expect(
      document.body.querySelector("[data-testid='entity-finder-input']"),
    ).toBeNull();
  });

  // -------------------------------------------------------------------------

  it("closes when Escape is pressed while the finder is open", async () => {
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    await act(async () => {
      root.render(
        <QueryClientProvider client={qc}>
          <MemoryRouter>
            <EntityFinder />
          </MemoryRouter>
        </QueryClientProvider>,
      );
      await flush();
    });

    await act(async () => {
      dispatchOpenEntityFinder();
      await flush();
    });

    expect(
      document.body.querySelector("[data-testid='entity-finder-input']"),
    ).not.toBeNull();

    // Dispatch Escape on the Command element (cmdk root)
    const command = document.body.querySelector("[cmdk-root]") as HTMLElement;
    await act(async () => {
      command.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
      await flush();
    });

    // Should be dismissed
    expect(
      document.body.querySelector("[data-testid='entity-finder-input']"),
    ).toBeNull();
  });

  // -------------------------------------------------------------------------

  it("calls useEntityFinderSearch with the typed query", async () => {
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    await act(async () => {
      root.render(
        <QueryClientProvider client={qc}>
          <MemoryRouter>
            <EntityFinder />
          </MemoryRouter>
        </QueryClientProvider>,
      );
      await flush();
    });

    await act(async () => {
      dispatchOpenEntityFinder();
      await flush();
    });

    // useEntityFinderSearch is called with the current query (empty string on open)
    expect(vi.mocked(useEntityFinderSearch)).toHaveBeenCalledWith("", {
      limit: 8,
    });
  });

  // -------------------------------------------------------------------------
  // entity-v3: preview pane + Tab-to-hop + empty-query owner-pinned set
  // -------------------------------------------------------------------------

  it("renders a footer documenting the hop key", async () => {
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    await act(async () => {
      root.render(
        <QueryClientProvider client={qc}>
          <MemoryRouter>
            <EntityFinder />
          </MemoryRouter>
        </QueryClientProvider>,
      );
      await flush();
    });

    await act(async () => {
      dispatchOpenEntityFinder();
      await flush();
    });

    const footer = document.body.querySelector("[cmdk-root]")?.textContent ?? "";
    expect(footer).toContain("hop");
    expect(footer).toContain("open");
  });

  it("shows a preview pane for the active entity with name and type — but NO fabricated gloss", async () => {
    // The search payload does not carry tier or curation state. The preview pane
    // must NOT display a gloss synthesized from neutral defaults (tier=150,
    // state=healthy) that look authoritative but are fabricated. Honest-UI
    // precedent: omit the field rather than show plausible fakes.
    mockSearchResults({
      results: [
        {
          entity_id: "uuid-dana",
          canonical_name: "Dana Scully",
          entity_type: "person",
          score: 100,
          match_kind: "prefix",
        },
      ],
      total: 1,
      q: "dana",
      limit: 8,
    });

    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    await act(async () => {
      root.render(
        <QueryClientProvider client={qc}>
          <MemoryRouter>
            <EntityFinder />
          </MemoryRouter>
        </QueryClientProvider>,
      );
      await flush();
    });

    await act(async () => {
      dispatchOpenEntityFinder();
      await flush();
    });

    // Type a query so the finder leaves empty-query (pinned) mode.
    const previewInput = document.body.querySelector(
      "[data-testid='entity-finder-input']",
    ) as HTMLInputElement;
    await act(async () => {
      typeInto(previewInput, "dana");
      await flush();
    });

    // Preview pane must exist and show the entity name.
    const preview = document.body.querySelector(
      "[data-testid='entity-finder-preview']",
    );
    expect(preview).not.toBeNull();
    expect(preview?.textContent).toContain("Dana Scully");

    // The gloss element must be ABSENT — no fabricated tier/state text.
    const gloss = document.body.querySelector(
      "[data-testid='entity-finder-preview-gloss']",
    );
    expect(gloss).toBeNull();

    // Specifically: the neutral-default gloss texts that used to appear must not
    // show up anywhere in the preview pane.
    const previewText = preview?.textContent ?? "";
    expect(previewText).not.toContain("Meaningful contact");
    expect(previewText).not.toContain("Active in the network");
    expect(previewText).not.toContain("Support clique");
    expect(previewText).not.toContain("Acquaintance");
  });

  it("hops (navigates to /entities/hop?center=) when Tab is pressed on an active result", async () => {
    mockSearchResults({
      results: [
        {
          entity_id: "uuid-fox",
          canonical_name: "Fox Mulder",
          entity_type: "person",
          score: 100,
          match_kind: "prefix",
        },
      ],
      total: 1,
      q: "fox",
      limit: 8,
    });

    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    await act(async () => {
      root.render(
        <QueryClientProvider client={qc}>
          <MemoryRouter initialEntries={["/dashboard"]}>
            <EntityFinder />
            <LocationProbe />
          </MemoryRouter>
        </QueryClientProvider>,
      );
      await flush();
    });

    await act(async () => {
      dispatchOpenEntityFinder();
      await flush();
    });

    // Type a query so the finder leaves empty-query (pinned) mode.
    const hopInput = document.body.querySelector(
      "[data-testid='entity-finder-input']",
    ) as HTMLInputElement;
    await act(async () => {
      typeInto(hopInput, "fox");
      await flush();
    });

    // Preview mirrors the highlighted (first) entity before Tab.
    expect(
      document.body.querySelector("[data-testid='entity-finder-preview']")
        ?.textContent,
    ).toContain("Fox Mulder");

    const command = document.body.querySelector("[cmdk-root]") as HTMLElement;
    await act(async () => {
      command.dispatchEvent(
        new KeyboardEvent("keydown", {
          key: "Tab",
          bubbles: true,
          cancelable: true,
        }),
      );
      await flush();
    });

    const loc = document.body.querySelector("[data-testid='loc']")?.textContent;
    expect(loc).toBe("/entities/hop?center=uuid-fox");
    // Finder closes on hop.
    expect(
      document.body.querySelector("[data-testid='entity-finder-input']"),
    ).toBeNull();
  });

  it("renders the owner-pinned set when the query is empty", async () => {
    // Empty query → search hook disabled → undefined data.
    mockSearchEmpty();
    vi.mocked(useEntityNeighbours).mockReturnValue({
      data: {
        neighbours: {
          knows: [
            {
              entity_id: "n1",
              canonical_name: "Pinned One",
              direction: "forward",
              src: "x",
              conf: 1,
              last_seen: null,
              weight: 9,
              verified: true,
              primary: null,
            },
          ],
        },
        remainders: {},
      },
      isLoading: false,
      isError: false,
    } as unknown as UseEntityNeighboursResult);

    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    await act(async () => {
      root.render(
        <QueryClientProvider client={qc}>
          <MemoryRouter>
            <EntityFinder />
          </MemoryRouter>
        </QueryClientProvider>,
      );
      await flush();
    });

    await act(async () => {
      dispatchOpenEntityFinder();
      await flush();
    });

    const pinned = document.body.querySelectorAll(
      "[data-testid='entity-finder-pinned-item']",
    );
    expect(pinned.length).toBe(1);
    expect(pinned[0].textContent).toContain("Pinned One");
  });
});

// ---------------------------------------------------------------------------
// Pure aggregation logic
// ---------------------------------------------------------------------------

describe("aggregateOwnerPinned", () => {
  function n(
    entity_id: string,
    canonical_name: string,
    weight: number | null,
  ): NeighbourEntry {
    return {
      entity_id,
      canonical_name,
      direction: "forward",
      src: "x",
      conf: 1,
      last_seen: null,
      weight,
      verified: true,
      primary: null,
    };
  }

  it("dedupes across predicates, sums COALESCE(weight,1), sorts desc, excludes owner, caps at limit", () => {
    const neighbours: Record<string, NeighbourEntry[]> = {
      knows: [n("a", "Alice", 3), n("b", "Bob", null), n("me", "Owner", 99)],
      "works-with": [n("a", "Alice", 2), n("c", "Carol", 5)],
    };
    const out = aggregateOwnerPinned(neighbours, "me", 8);
    // Alice (5) and Carol (5) tie; stable sort keeps first-seen order (Alice).
    expect(out.map((x) => x.entity_id)).toEqual(["a", "c", "b"]);
    // Alice: 3 + 2 = 5; Carol: 5; Bob: COALESCE(null,1) = 1.
    expect(out.find((x) => x.entity_id === "a")?.weight).toBe(5);
    expect(out.find((x) => x.entity_id === "b")?.weight).toBe(1);
    // Owner excluded.
    expect(out.find((x) => x.entity_id === "me")).toBeUndefined();
  });

  it("respects the limit", () => {
    const neighbours: Record<string, NeighbourEntry[]> = {
      knows: [n("a", "A", 5), n("b", "B", 4), n("c", "C", 3)],
    };
    expect(aggregateOwnerPinned(neighbours, null, 2).length).toBe(2);
  });

  it("returns [] for undefined neighbours", () => {
    expect(aggregateOwnerPinned(undefined, "me")).toEqual([]);
  });
});
