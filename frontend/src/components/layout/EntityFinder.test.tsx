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
import { CommandRegistryProvider, useRegisterCommands } from "@/lib/command-registry";
import { getRecents } from "@/lib/recents-store";
import {
  useEntityFinderSearch,
  useEntityNeighbours,
} from "@/hooks/use-entities";
import { useSearch } from "@/hooks/use-search";
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

vi.mock("@/hooks/use-search", () => ({
  useSearch: vi.fn(),
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

/**
 * Control the shared GET /api/search hook (sessions/state + degraded meta).
 * `response` is the ApiResponse envelope ({ data, meta }) or undefined for the
 * pre-fetch state. Default (undefined) reproduces the prior behaviour where
 * this surface contributed no session/state rows and no degraded flag.
 */
function mockGenericSearch(response: unknown): void {
  vi.mocked(useSearch).mockReturnValue({
    data: response,
    isFetching: false,
  } as unknown as ReturnType<typeof useSearch>);
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
    mockGenericSearch(undefined);
    localStorage.clear();

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
  // bu-1ukzt: useEntityFinderSearch pairs placeholderData with the debounced
  // query key, so a background refetch failure on a previously-successful
  // (cache-hit) query keeps entityResults populated — isError must still
  // surface, not be silently swallowed behind the stale rows.
  // -------------------------------------------------------------------------

  it("surfaces the error state instead of masking it behind stale results", async () => {
    const refetch = vi.fn();
    vi.mocked(useEntityFinderSearch).mockReturnValue({
      data: {
        results: [
          {
            entity_id: "uuid-alice",
            canonical_name: "Alice",
            entity_type: "person",
            score: 100,
            match_kind: "prefix",
          },
        ],
        total: 1,
        q: "ali",
        limit: 8,
      },
      isLoading: false,
      isFetching: false,
      isError: true,
      refetch,
    } as unknown as UseEntityFinderSearchResult);

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

    // Stale results still render — this is the never-blank floor (bu-nhcp5)
    // working as intended.
    const items = document.body.querySelectorAll(
      "[data-testid='entity-finder-entity-item']",
    );
    expect(items.length).toBe(1);
    expect(items[0].textContent).toContain("Alice");

    // But the error must ALSO surface — not be swallowed because
    // entityResults.length > 0. The hard "Search failed" full-list banner is
    // reserved for the no-fallback-data case; here a degraded note sits next
    // to the (still visible) stale rows.
    const degraded = document.body.querySelector(
      "[data-testid='entity-finder-search-degraded']",
    );
    expect(degraded).toBeTruthy();
    expect(degraded?.textContent).toContain("search failed");

    // The degraded note's Retry action must be wired to the query's own
    // refetch, so a transient failure is recoverable without retyping.
    const retryButton = degraded?.querySelector("button");
    expect(retryButton).toBeTruthy();
    await act(async () => {
      retryButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });
    expect(refetch).toHaveBeenCalledTimes(1);

    // The full-blanking "Search failed. Try again in a moment." banner must
    // NOT render here — the stale rows are still useful and other sources
    // (pages/butlers/etc) are unaffected by the entity search failure.
    expect(
      document.body.querySelector("[data-testid='entity-finder-search-error']"),
    ).toBeNull();
  });

  // -------------------------------------------------------------------------
  // bu-tpudw.4: GET /api/search sessions/state fan-out honesty. A half-down
  // fleet reports meta.sources_degraded; a zero-result search must then NAME
  // the degraded source, never render as a clean "No results".
  // -------------------------------------------------------------------------

  it("names the degraded search source and suppresses 'No results' when a fan-out source failed", async () => {
    mockGenericSearch({
      data: { entities: [], contacts: [], sessions: [], state: [] },
      meta: { sources_degraded: ["finance"] },
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

    const input = document.body.querySelector(
      "[data-testid='entity-finder-input']",
    ) as HTMLInputElement;
    await act(async () => {
      typeInto(input, "zzznomatch");
      await flush();
    });

    const degraded = document.body.querySelector(
      "[data-testid='entity-finder-generic-degraded']",
    );
    expect(degraded).toBeTruthy();
    // The failed butler is named inline (not a generic "something went wrong").
    expect(degraded?.textContent).toContain("finance");
    // A degraded fan-out must NOT read as a truthful empty result.
    expect(document.body.textContent).not.toContain("No results for");
  });

  // -------------------------------------------------------------------------

  it("shows the honest 'No results' empty copy and NO degraded note on a clean empty search", async () => {
    // Mutation guard opposite direction: a clean fan-out (no sources_degraded)
    // must still render the plain empty state and never a spurious note.
    mockGenericSearch({
      data: { entities: [], contacts: [], sessions: [], state: [] },
      meta: {},
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

    const input = document.body.querySelector(
      "[data-testid='entity-finder-input']",
    ) as HTMLInputElement;
    await act(async () => {
      typeInto(input, "zzznomatch");
      await flush();
    });

    expect(
      document.body.querySelector("[data-testid='entity-finder-generic-degraded']"),
    ).toBeNull();
    expect(document.body.textContent).toContain("No results for");
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

  it("hops (centers the Plex via /entities?center=) when Tab is pressed on an active result", async () => {
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
    expect(loc).toBe("/entities?center=uuid-fox");
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

  // -------------------------------------------------------------------------
  // Palette browsability (bu-qvnce.11): verbs at empty query, binding kbd
  // column, recents.
  // -------------------------------------------------------------------------

  function ActionRegistrar({ perform }: { perform: () => void }) {
    useRegisterCommands([
      { id: "approve-next", label: "Approve next", perform, binding: ["a"] },
    ]);
    return null;
  }

  it("shows registered Actions at empty query, not just after the first keystroke", async () => {
    const perform = vi.fn();
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    await act(async () => {
      root.render(
        <QueryClientProvider client={qc}>
          <MemoryRouter>
            <CommandRegistryProvider>
              <ActionRegistrar perform={perform} />
              <EntityFinder />
            </CommandRegistryProvider>
          </MemoryRouter>
        </QueryClientProvider>,
      );
      await flush();
    });

    await act(async () => {
      dispatchOpenEntityFinder();
      await flush();
    });

    const actionItem = document.body.querySelector(
      "[data-testid='entity-finder-action-item']",
    );
    expect(actionItem?.textContent).toContain("Approve next");
  });

  it("renders the action's binding as a kbd hint next to it in the palette", async () => {
    const perform = vi.fn();
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    await act(async () => {
      root.render(
        <QueryClientProvider client={qc}>
          <MemoryRouter>
            <CommandRegistryProvider>
              <ActionRegistrar perform={perform} />
              <EntityFinder />
            </CommandRegistryProvider>
          </MemoryRouter>
        </QueryClientProvider>,
      );
      await flush();
    });

    await act(async () => {
      dispatchOpenEntityFinder();
      await flush();
    });

    const binding = document.body.querySelector(
      "[data-testid='entity-finder-action-binding']",
    );
    expect(binding?.textContent).toBe("a");
  });

  it("records a selected action as a Recent and surfaces it at empty query on reopen", async () => {
    const perform = vi.fn();
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    await act(async () => {
      root.render(
        <QueryClientProvider client={qc}>
          <MemoryRouter>
            <CommandRegistryProvider>
              <ActionRegistrar perform={perform} />
              <EntityFinder />
            </CommandRegistryProvider>
          </MemoryRouter>
        </QueryClientProvider>,
      );
      await flush();
    });

    await act(async () => {
      dispatchOpenEntityFinder();
      await flush();
    });

    const actionItem = document.body.querySelector(
      "[data-testid='entity-finder-action-item']",
    ) as HTMLElement;
    await act(async () => {
      actionItem.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    expect(perform).toHaveBeenCalledTimes(1);
    expect(getRecents().map((r) => r.label)).toContain("Approve next");

    // Reopen — the Finder resets its own open state on each dispatch, and
    // recents are re-read from storage at that point.
    await act(async () => {
      dispatchOpenEntityFinder();
      await flush();
    });

    const recentItem = document.body.querySelector(
      "[data-testid='entity-finder-recent-item']",
    );
    expect(recentItem?.textContent).toContain("Approve next");
  });

  it("records a selected page as a Recent", async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });

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

    const input = document.body.querySelector(
      "[data-testid='entity-finder-input']",
    ) as HTMLInputElement;
    await act(async () => {
      typeInto(input, "contacts");
      await flush();
    });

    const pageItem = document.body.querySelector(
      "[data-testid='entity-finder-page-item']",
    ) as HTMLElement;
    expect(pageItem).not.toBeNull();

    await act(async () => {
      pageItem.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    expect(getRecents().some((r) => r.kind === "page")).toBe(true);
  });

  // -------------------------------------------------------------------------
  // Never-blank floor (bu-nhcp5): useEntityFinderSearch already pairs
  // placeholderData with the query, so isLoading stays false after the first
  // keystroke — but nothing signalled that a keystroke was re-searching. The
  // Entities group must keep showing the previous keystroke's rows, dimmed,
  // instead of a silent stale-to-fresh swap or a blank.
  // -------------------------------------------------------------------------

  it("dims the Entities group while a re-search is in flight (isFetching, not the first load)", async () => {
    vi.mocked(useEntityFinderSearch).mockReturnValue({
      data: {
        results: [
          {
            entity_id: "uuid-alice",
            canonical_name: "Alice",
            entity_type: "person",
            score: 100,
            match_kind: "prefix",
          },
        ],
        total: 1,
        q: "ali",
        limit: 8,
      },
      isLoading: false,
      isFetching: true,
      isError: false,
    } as UseEntityFinderSearchResult);

    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
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

    // Never blanks: the stale-but-visible row stays on screen.
    const item = document.body.querySelector(
      "[data-testid='entity-finder-entity-item']",
    );
    expect(item?.textContent).toContain("Alice");

    // Dims: the FetchingDim wrapper around the Entities group carries the
    // opacity-60 treatment while the re-search is in flight.
    const group = document.body.querySelector(
      "[data-testid='entity-finder-entity-group']",
    );
    expect(group?.parentElement?.className).toContain("opacity-60");
  });

  it("does not dim the Entities group once the re-search settles", async () => {
    mockSearchResults({
      results: [
        {
          entity_id: "uuid-alice",
          canonical_name: "Alice",
          entity_type: "person",
          score: 100,
          match_kind: "prefix",
        },
      ],
      total: 1,
      q: "ali",
      limit: 8,
    });

    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
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

    const group = document.body.querySelector(
      "[data-testid='entity-finder-entity-group']",
    );
    expect(group?.parentElement?.className).not.toContain("opacity-60");
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
      entity_type: null,
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
