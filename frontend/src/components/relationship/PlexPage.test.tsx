// @vitest-environment jsdom
/**
 * Component tests for PlexPage (/entities landing — the owner ego-graph).
 *
 * Covers behaviors, not render details:
 * - Owner mode: one plex-node per non-1500 non-owner ranking entry, the owner
 *   mark, ring capacity labels, and the attention rail
 * - Attention derivation: cadence gates, tier-weighted ordering, the 5-item
 *   cap, owner/within-cadence/never-seen exclusion
 * - Neighbour mode (?center=<id>): predicate sector labels, the dossier
 *   aside, no attention rail
 * - Hop navigation: clicking a node re-centers and pushes the previous center
 *   onto ?trail; clicking the owner resets center+trail
 * - Escape on the canvas pops one hop off the trail
 */

import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, useLocation } from "react-router";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

// ---------------------------------------------------------------------------
// Mock hooks + heavy blocks — must appear before component imports
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-memory", () => ({
  useDunbarRanking: vi.fn(),
}));

vi.mock("@/hooks/use-entities", () => ({
  useEntityNeighbours: vi.fn(),
  usePlexHalo: vi.fn(),
  useRelationshipEntitiesByIds: vi.fn(),
  useEntityFacts: vi.fn(),
  useEntityCoreDates: vi.fn(),
  useUpdateEntityDunbarTier: vi.fn(),
}));

// ActivitySparkline / LatestInteractionsBlock pull their own query hooks;
// stub the components so the dossier renders without wiring those hooks.
vi.mock("@/components/relationship/ActivitySparkline", () => ({
  ActivitySparkline: ({ entityId }: { entityId: string }) => (
    <div data-testid="sparkline-stub" data-entity-id={entityId} />
  ),
}));
vi.mock("@/components/relationship/LatestInteractionsBlock", () => ({
  LatestInteractionsBlock: ({ entityId }: { entityId: string }) => (
    <div data-testid="latest-interactions-stub" data-entity-id={entityId} />
  ),
}));

import type {
  DunbarEntry,
  DunbarRankingResponse,
  HaloResponse,
  NeighbourEntry,
  NeighboursResponse,
} from "@/api/types";
import {
  useEntityCoreDates,
  useEntityFacts,
  useEntityNeighbours,
  usePlexHalo,
  useRelationshipEntitiesByIds,
  useUpdateEntityDunbarTier,
} from "@/hooks/use-entities";
import { useDunbarRanking } from "@/hooks/use-memory";
import PlexPage from "./PlexPage";

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const DAY_MS = 86_400_000;
/** ISO timestamp n days before now — keeps daysSince() deterministic-enough. */
const daysAgo = (n: number) => new Date(Date.now() - n * DAY_MS).toISOString();

const OWNER_ID = "ent-owner";

function entry(overrides: Partial<DunbarEntry> & { entity_id: string; canonical_name: string }): DunbarEntry {
  return {
    contact_id: `contact-${overrides.entity_id}`,
    dunbar_tier: 50,
    dunbar_score: 1,
    dunbar_tier_override: false,
    last_interaction_at: null,
    ...overrides,
  };
}

function neighbour(
  overrides: Partial<NeighbourEntry> & { entity_id: string; canonical_name: string },
): NeighbourEntry {
  return {
    entity_type: null,
    direction: "forward",
    src: "relationship",
    conf: 1,
    last_seen: null,
    weight: 1,
    verified: false,
    primary: null,
    ...overrides,
  };
}

/** Owner + one contact per interesting tier; everyone recently seen. */
const RANKING: DunbarRankingResponse = {
  owner_entity_id: OWNER_ID,
  entries: [
    entry({ entity_id: OWNER_ID, canonical_name: "Owen Owner", dunbar_tier: 5, last_interaction_at: daysAgo(999) }),
    entry({ entity_id: "ent-ana", canonical_name: "Ana", dunbar_tier: 5, dunbar_score: 9, last_interaction_at: daysAgo(2) }),
    entry({ entity_id: "ent-bea", canonical_name: "Bea", dunbar_tier: 15, last_interaction_at: daysAgo(3) }),
    entry({ entity_id: "ent-cal", canonical_name: "Cal", dunbar_tier: 500, last_interaction_at: daysAgo(1) }),
    entry({ entity_id: "ent-dot", canonical_name: "Dot", dunbar_tier: 1500, last_interaction_at: daysAgo(400) }),
  ],
};

const NEIGHBOURS: NeighboursResponse = {
  neighbours: {
    "family-of": [
      neighbour({ entity_id: "ent-ana", canonical_name: "Ana", weight: 5 }),
      neighbour({ entity_id: OWNER_ID, canonical_name: "Owen Owner", weight: 3 }),
    ],
    works_with: [neighbour({ entity_id: "ent-cal", canonical_name: "Cal", weight: 2 })],
  },
  remainders: { "family-of": 2 },
};

/** Two org satellites (one linked to Ana), one place; org arc truncated. */
const HALO: HaloResponse = {
  arcs: {
    organization: [
      {
        entity_id: "sat-acme",
        canonical_name: "Acme Corp",
        last_seen: daysAgo(4),
        edges: [{ person_id: "ent-ana", predicate: "works-at" }],
      },
      {
        entity_id: "sat-guild",
        canonical_name: "The Guild",
        last_seen: null,
        edges: [],
      },
    ],
    place: [
      {
        entity_id: "sat-cafe",
        canonical_name: "Corner Cafe",
        last_seen: daysAgo(10),
        edges: [],
      },
    ],
  },
  totals: { organization: 171, place: 1 },
};

function loaded<T>(data: T) {
  return { data, isLoading: false, isError: false, error: null } as unknown;
}

// ---------------------------------------------------------------------------
// jsdom sizing: the canvas only renders when clientWidth/clientHeight > 0.
// ---------------------------------------------------------------------------

beforeAll(() => {
  Object.defineProperty(HTMLElement.prototype, "clientWidth", {
    configurable: true,
    get: () => 1200,
  });
  Object.defineProperty(HTMLElement.prototype, "clientHeight", {
    configurable: true,
    get: () => 800,
  });
});

afterAll(() => {
  Reflect.deleteProperty(HTMLElement.prototype, "clientWidth");
  Reflect.deleteProperty(HTMLElement.prototype, "clientHeight");
});

// ---------------------------------------------------------------------------
// Render helper
// ---------------------------------------------------------------------------

let container: HTMLDivElement;
let root: Root;

/** Exposes the live router URL so navigation behavior is observable. */
function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location-probe" data-search={location.search} />;
}

function renderPage(initialEntry = "/entities") {
  act(() => {
    root.render(
      <MemoryRouter initialEntries={[initialEntry]}>
        <PlexPage />
        <LocationProbe />
      </MemoryRouter>,
    );
  });
}

function currentSearch(): URLSearchParams {
  const probe = container.querySelector("[data-testid='location-probe']");
  return new URLSearchParams(probe?.getAttribute("data-search") ?? "");
}

function nodeByName(name: string): HTMLButtonElement | null {
  return container.querySelector(`[data-testid='plex-node'][title='${name}']`);
}

async function clickNode(name: string) {
  const btn = nodeByName(name);
  expect(btn, `expected a plex node for ${name}`).toBeTruthy();
  await act(async () => {
    btn?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
}

beforeEach(() => {
  vi.resetAllMocks();

  vi.mocked(useDunbarRanking).mockReturnValue(
    loaded(RANKING) as ReturnType<typeof useDunbarRanking>,
  );
  // Neighbour fetches: the centered entity gets data; the disabled hover
  // spotlight call (undefined id) resolves to nothing.
  vi.mocked(useEntityNeighbours).mockImplementation(
    (entityId) =>
      (entityId ? loaded(NEIGHBOURS) : loaded(undefined)) as ReturnType<
        typeof useEntityNeighbours
      >,
  );
  vi.mocked(useRelationshipEntitiesByIds).mockImplementation(
    (params) =>
      loaded(
        (params.ids ?? []).length > 0
          ? {
              items: (params.ids ?? []).map((id) => ({
                id,
                canonical_name:
                  RANKING.entries.find((e) => e.entity_id === id)?.canonical_name ??
                  "Unknown",
                entity_type: "person",
                tier: 15,
                last_seen: daysAgo(3),
              })),
            }
          : undefined,
      ) as ReturnType<typeof useRelationshipEntitiesByIds>,
  );
  // Halo defaults to "not yet loaded" — tests that want the band opt in.
  vi.mocked(usePlexHalo).mockReturnValue(
    loaded(undefined) as ReturnType<typeof usePlexHalo>,
  );
  vi.mocked(useEntityFacts).mockReturnValue(
    loaded({ items: [] }) as ReturnType<typeof useEntityFacts>,
  );
  vi.mocked(useEntityCoreDates).mockReturnValue(
    loaded({ items: [] }) as ReturnType<typeof useEntityCoreDates>,
  );
  vi.mocked(useUpdateEntityDunbarTier).mockReturnValue({
    mutateAsync: vi.fn(),
  } as unknown as ReturnType<typeof useUpdateEntityDunbarTier>);

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
// Owner mode
// ---------------------------------------------------------------------------

describe("PlexPage — owner mode", () => {
  it("renders one plex node per non-1500, non-owner ranking entry", () => {
    renderPage("/entities");
    const nodes = container.querySelectorAll("[data-testid='plex-node']");
    expect(nodes.length).toBe(3); // Ana, Bea, Cal
    expect(nodeByName("Ana")).toBeTruthy();
    expect(nodeByName("Bea")).toBeTruthy();
    expect(nodeByName("Cal")).toBeTruthy();
    // Tier 1500 is summarized, never drawn; the owner is the center mark.
    expect(nodeByName("Dot")).toBeNull();
    expect(nodeByName("Owen Owner")).toBeNull();
  });

  it("renders the owner mark at the center", () => {
    renderPage("/entities");
    const owner = container.querySelector("[data-testid='plex-owner']");
    expect(owner?.textContent).toContain("Owen Owner");
  });

  it("renders ring capacity labels with per-tier counts", () => {
    renderPage("/entities");
    expect(
      container.querySelector("[data-testid='plex-capacity-5']")?.textContent,
    ).toBe("1/5");
    expect(
      container.querySelector("[data-testid='plex-capacity-15']")?.textContent,
    ).toBe("1/15");
    expect(
      container.querySelector("[data-testid='plex-capacity-50']")?.textContent,
    ).toBe("0/50");
    expect(
      container.querySelector("[data-testid='plex-capacity-500']")?.textContent,
    ).toBe("1/500");
  });

  it("renders the attention rail", () => {
    renderPage("/entities");
    expect(container.querySelector("[data-testid='plex-rail']")).toBeTruthy();
  });

  it("opens the micro-dossier on keyboard focus, not hover alone (bu-f310e task 4)", () => {
    renderPage("/entities");
    const node = nodeByName("Ana");
    expect(node).toBeTruthy();
    // No card yet.
    expect(container.querySelector("[data-testid='plex-hover-card']")).toBeNull();

    // Tab-focus the node (a real <button>). The card is opened via a debounced
    // scheduleHover (HOVER_SHOW_DELAY_MS); fake timers flush that delay
    // deterministically.
    vi.useFakeTimers();
    try {
      act(() => {
        node!.dispatchEvent(new FocusEvent("focusin", { bubbles: true }));
      });
      act(() => {
        vi.advanceTimersByTime(300);
      });
      const card = container.querySelector("[data-testid='plex-hover-card']");
      expect(card).toBeTruthy();
      expect(card?.textContent).toContain("Ana");

      // Blurring dismisses it (debounced hide).
      act(() => {
        node!.dispatchEvent(new FocusEvent("focusout", { bubbles: true }));
      });
      act(() => {
        vi.advanceTimersByTime(400);
      });
      expect(container.querySelector("[data-testid='plex-hover-card']")).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });
});

// ---------------------------------------------------------------------------
// Attention derivation
// ---------------------------------------------------------------------------

function railItemNames(): string[] {
  const rail = container.querySelector("[data-testid='plex-rail']");
  return Array.from(rail?.querySelectorAll("li button") ?? []).map(
    (b) => b.textContent ?? "",
  );
}

describe("PlexPage — attention derivation", () => {
  it("lists overdue entries tier-weighted: an overdue tier-5 outranks a longer-overdue tier-500", () => {
    vi.mocked(useDunbarRanking).mockReturnValue(
      loaded({
        owner_entity_id: OWNER_ID,
        // Owner is long overdue too — must still be excluded.
        entries: [
          entry({ entity_id: OWNER_ID, canonical_name: "Owen Owner", dunbar_tier: 5, last_interaction_at: daysAgo(100) }),
          // tier 5, cadence 7d, 21d since → overdue, urgency 32 * 2
          entry({ entity_id: "ent-t5", canonical_name: "Tia Five", dunbar_tier: 5, last_interaction_at: daysAgo(21) }),
          // tier 500, cadence 365d, 800d since → longer overdue in days, but
          // tier-weighted urgency is far lower (2 * ~1.2)
          entry({ entity_id: "ent-t500", canonical_name: "Paz Distant", dunbar_tier: 500, last_interaction_at: daysAgo(800) }),
          // within cadence (tier 15, 5d < 30d) → excluded
          entry({ entity_id: "ent-fresh", canonical_name: "Fay Fresh", dunbar_tier: 15, last_interaction_at: daysAgo(5) }),
          // never seen → excluded (no cadence baseline)
          entry({ entity_id: "ent-null", canonical_name: "Nul Never", dunbar_tier: 50, last_interaction_at: null }),
        ],
      }) as ReturnType<typeof useDunbarRanking>,
    );
    renderPage("/entities");
    expect(railItemNames()).toEqual(["Tia Five", "Paz Distant"]);
  });

  it("caps the rail at 5 items, keeping the most urgent", () => {
    const overdue = Array.from({ length: 7 }, (_, i) =>
      // All tier 50 (cadence 90d), 100..160 days since — urgency grows with days.
      entry({
        entity_id: `ent-o${i + 1}`,
        canonical_name: `Over ${i + 1}`,
        dunbar_tier: 50,
        last_interaction_at: daysAgo(100 + 10 * i),
      }),
    );
    vi.mocked(useDunbarRanking).mockReturnValue(
      loaded({
        owner_entity_id: OWNER_ID,
        entries: [
          entry({ entity_id: OWNER_ID, canonical_name: "Owen Owner", dunbar_tier: 5 }),
          ...overdue,
        ],
      }) as ReturnType<typeof useDunbarRanking>,
    );
    renderPage("/entities");
    // The five most-overdue (Over 7 … Over 3); Over 1 and Over 2 fall off.
    expect(railItemNames()).toEqual(["Over 7", "Over 6", "Over 5", "Over 4", "Over 3"]);
  });

  it("shows the empty message when no one is overdue", () => {
    renderPage("/entities"); // default RANKING: everyone recent or excluded
    const rail = container.querySelector("[data-testid='plex-rail']");
    expect(rail?.querySelectorAll("li").length).toBe(0);
    expect(rail?.textContent).toContain("No one is owed a call.");
  });

  it("shows a degraded note instead of a false all-clear when the ranking source errors (bu-qvnce.1)", () => {
    vi.mocked(useDunbarRanking).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("ranking fetch failed"),
    } as ReturnType<typeof useDunbarRanking>);

    renderPage("/entities");

    const rail = container.querySelector("[data-testid='plex-rail']");
    expect(rail?.textContent).not.toContain("No one is owed a call.");
    expect(rail?.textContent).toContain("ranking source unavailable");

    const aside = container.querySelector("[aria-label='Capacity']");
    expect(aside?.textContent).toContain("ranking source unavailable");
    // No fabricated 0/N capacity bars while the ranking source is down.
    expect(aside?.textContent).not.toContain("Layer sizes are cognitive limits");
  });
});

// ---------------------------------------------------------------------------
// Neighbour mode
// ---------------------------------------------------------------------------

describe("PlexPage — neighbour mode", () => {
  it("renders a sector label per predicate, humanized", () => {
    renderPage("/entities?center=ent-bea");
    const labels = Array.from(
      container.querySelectorAll("[data-testid='plex-sector-label']"),
    ).map((el) => el.textContent ?? "");
    expect(labels.some((t) => t.includes("family of"))).toBe(true);
    expect(labels.some((t) => t.includes("works with"))).toBe(true);
    expect(labels.length).toBe(2);
  });

  it("links the sector remainder to the centered entity's record", () => {
    renderPage("/entities?center=ent-bea");
    const family = Array.from(
      container.querySelectorAll("[data-testid='plex-sector-label']"),
    ).find((el) => el.textContent?.includes("family of"));
    const more = family?.querySelector("a");
    expect(more?.textContent).toBe("+2");
    expect(more?.getAttribute("href")).toBe("/entities/ent-bea");
  });

  it("renders the centered entity mark and its dossier aside", () => {
    renderPage("/entities?center=ent-bea");
    expect(
      container.querySelector("[data-testid='plex-center']")?.textContent,
    ).toContain("Bea");
    const dossier = container.querySelector("[data-testid='plex-dossier']");
    expect(dossier).toBeTruthy();
    expect(dossier?.getAttribute("aria-label")).toBe("Bea dossier");
  });

  it("does not render the attention rail", () => {
    renderPage("/entities?center=ent-bea");
    expect(container.querySelector("[data-testid='plex-rail']")).toBeNull();
  });

  it("shows the empty message when the centered entity has no relational facts", () => {
    vi.mocked(useEntityNeighbours).mockImplementation(
      (entityId) =>
        (entityId
          ? loaded({ neighbours: {}, remainders: {} })
          : loaded(undefined)) as ReturnType<typeof useEntityNeighbours>,
    );
    renderPage("/entities?center=ent-bea");
    expect(container.textContent).toContain("No relational facts yet.");
    expect(container.querySelectorAll("[data-testid='plex-node']").length).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// Hop navigation (URL contract)
// ---------------------------------------------------------------------------

describe("PlexPage — hop navigation", () => {
  it("clicking a node in owner mode sets ?center without a trail", async () => {
    renderPage("/entities");
    await clickNode("Ana");
    const search = currentSearch();
    expect(search.get("center")).toBe("ent-ana");
    expect(search.get("trail")).toBeNull();
  });

  it("clicking a node in neighbour mode pushes the previous center onto ?trail", async () => {
    renderPage("/entities?center=ent-bea");
    await clickNode("Ana");
    const search = currentSearch();
    expect(search.get("center")).toBe("ent-ana");
    expect(search.get("trail")).toBe("ent-bea");
  });

  it("clicking the owner's node resets center and trail back to the home plex", async () => {
    renderPage("/entities?center=ent-bea&trail=ent-ana");
    await clickNode("Owen Owner"); // owner appears as a family-of neighbour
    const search = currentSearch();
    expect(search.get("center")).toBeNull();
    expect(search.get("trail")).toBeNull();
    // Back in owner mode: the attention rail returns.
    expect(container.querySelector("[data-testid='plex-rail']")).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// Keyboard: Escape pops the trail
// ---------------------------------------------------------------------------

describe("PlexPage — Escape on the canvas", () => {
  function pressEscape() {
    const canvas = container.querySelector("[data-testid='plex-canvas']");
    expect(canvas).toBeTruthy();
    act(() => {
      canvas?.dispatchEvent(
        new KeyboardEvent("keydown", { key: "Escape", bubbles: true }),
      );
    });
  }

  it("pops one hop: the last trail entry becomes the center", () => {
    renderPage("/entities?center=ent-cal&trail=ent-ana,ent-bea");
    pressEscape();
    const search = currentSearch();
    expect(search.get("center")).toBe("ent-bea");
    expect(search.get("trail")).toBe("ent-ana");
  });

  it("with an empty trail in neighbour mode, resets to the owner plex", () => {
    renderPage("/entities?center=ent-bea");
    pressEscape();
    const search = currentSearch();
    expect(search.get("center")).toBeNull();
    expect(search.get("trail")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Dimension halo (owner mode)
// ---------------------------------------------------------------------------

describe("PlexPage — dimension halo", () => {
  it("renders no halo band while the halo has not loaded", () => {
    renderPage("/entities");
    expect(container.querySelectorAll("[data-testid='plex-halo-mark']").length).toBe(0);
    expect(
      container.querySelectorAll("[data-testid='plex-halo-arc-label']").length,
    ).toBe(0);
    // Genuinely no-data-yet must never render the degraded note either.
    expect(container.querySelector("[data-testid='plex-halo-degraded']")).toBeNull();
  });

  it("shows a degraded note instead of a silent absence when the halo source errors (bu-ep4ks.5)", () => {
    vi.mocked(usePlexHalo).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("halo fetch failed"),
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof usePlexHalo>);
    renderPage("/entities");
    // The rings still render — this is not a full-page error.
    expect(container.querySelectorAll("[data-testid='plex-node']").length).toBe(3);
    // But the halo failure is named, not silently absent.
    const note = container.querySelector("[data-testid='plex-halo-degraded']");
    expect(note).toBeTruthy();
    expect(note?.textContent).toContain("halo source unavailable");
    expect(container.querySelectorAll("[data-testid='plex-halo-mark']").length).toBe(0);
  });

  it("renders one mark per satellite and one label per arc", () => {
    vi.mocked(usePlexHalo).mockReturnValue(
      loaded(HALO) as ReturnType<typeof usePlexHalo>,
    );
    renderPage("/entities");
    const marks = container.querySelectorAll("[data-testid='plex-halo-mark']");
    expect(marks.length).toBe(3);
    expect(
      container.querySelector("[data-testid='plex-halo-mark'][title='Acme Corp']"),
    ).toBeTruthy();
    const labels = [
      ...container.querySelectorAll("[data-testid='plex-halo-arc-label']"),
    ];
    expect(labels.length).toBe(2);
    // A truncated arc owns up to its cap; a complete arc shows the plain total.
    const texts = labels.map((l) => l.textContent);
    expect(texts).toContain("organizations · 2/171");
    expect(texts).toContain("places · 1");
  });

  it("arc labels link to the index filtered to that entity type", () => {
    vi.mocked(usePlexHalo).mockReturnValue(
      loaded(HALO) as ReturnType<typeof usePlexHalo>,
    );
    renderPage("/entities");
    const hrefs = [
      ...container.querySelectorAll("[data-testid='plex-halo-arc-label']"),
    ].map((l) => l.getAttribute("href"));
    expect(hrefs).toContain("/entities/index?type=organization");
    expect(hrefs).toContain("/entities/index?type=place");
  });

  it("swaps the truncated arc's title= for a focusable, SR-announced tooltip (bu-f310e task 3)", async () => {
    vi.mocked(usePlexHalo).mockReturnValue(
      loaded(HALO) as ReturnType<typeof usePlexHalo>,
    );
    renderPage("/entities");
    const labels = [
      ...container.querySelectorAll<HTMLAnchorElement>(
        "[data-testid='plex-halo-arc-label']",
      ),
    ];
    const orgLabel = labels.find((l) => l.textContent?.includes("2/171"));
    const placeLabel = labels.find((l) => l.textContent?.includes("places"));
    expect(orgLabel, "truncated org arc-label should render").toBeTruthy();
    expect(placeLabel, "complete place arc-label should render").toBeTruthy();

    // The load-bearing "shown/total" explanation no longer lives in a title=
    // attribute (which never surfaced on keyboard focus).
    expect(orgLabel!.getAttribute("title")).toBeNull();
    // It is now a radix tooltip trigger: a natively-focusable <a> the tooltip
    // is wired to (radix stamps data-state on the trigger).
    expect(orgLabel!.tagName).toBe("A");
    expect(orgLabel!.getAttribute("href")).toBe(
      "/entities/index?type=organization",
    );
    expect(orgLabel!.getAttribute("data-state")).toBe("closed");

    // A complete arc (nothing truncated) carries no tooltip and no title.
    expect(placeLabel!.getAttribute("data-state")).toBeNull();
    expect(placeLabel!.getAttribute("title")).toBeNull();

    // Focusing the trigger opens the tooltip and announces the count's meaning
    // (delayDuration=0 → instant open); the content is SR-announced (role=tooltip).
    await act(async () => {
      orgLabel!.dispatchEvent(new FocusEvent("focusin", { bubbles: true }));
    });
    const tip = document.body.querySelector("[data-testid='plex-halo-arc-tooltip']");
    expect(tip?.textContent).toContain(
      "Showing the 2 most recently active of 171",
    );
  });

  it("clicking a satellite re-centers the plex on it", async () => {
    vi.mocked(usePlexHalo).mockReturnValue(
      loaded(HALO) as ReturnType<typeof usePlexHalo>,
    );
    renderPage("/entities");
    const mark = container.querySelector<HTMLButtonElement>(
      "[data-testid='plex-halo-mark'][title='Acme Corp']",
    );
    expect(mark).toBeTruthy();
    await act(async () => {
      mark?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(currentSearch().get("center")).toBe("sat-acme");
  });

  it("does not request the halo in neighbour mode", () => {
    vi.mocked(usePlexHalo).mockReturnValue(
      loaded(undefined) as ReturnType<typeof usePlexHalo>,
    );
    renderPage("/entities?center=ent-bea");
    expect(vi.mocked(usePlexHalo)).toHaveBeenCalledWith(false);
    expect(container.querySelectorAll("[data-testid='plex-halo-mark']").length).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// Neighbour mode: entity type marks
// ---------------------------------------------------------------------------

describe("PlexPage — neighbour entity types", () => {
  it("renders a non-person neighbour with its type mark, not initials", () => {
    vi.mocked(useEntityNeighbours).mockImplementation(
      (entityId) =>
        (entityId
          ? loaded({
              neighbours: {
                "works-at": [
                  neighbour({
                    entity_id: "sat-acme",
                    canonical_name: "Acme Corp",
                    entity_type: "organization",
                  }),
                ],
              },
              remainders: {},
            } satisfies NeighboursResponse)
          : loaded(undefined)) as ReturnType<typeof useEntityNeighbours>,
    );
    renderPage("/entities?center=ent-bea");
    const node = container.querySelector("[data-testid='plex-node'][title='Acme Corp']");
    expect(node).toBeTruthy();
    // EntityMark renders non-person entities as `${type} entity` images.
    expect(node?.querySelector("[aria-label='organization entity']")).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// Find-as-you-type
// ---------------------------------------------------------------------------

describe("PlexPage — find-as-you-type", () => {
  function pressKey(key: string) {
    const canvas = container.querySelector("[data-testid='plex-canvas']");
    expect(canvas).toBeTruthy();
    act(() => {
      canvas?.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true }));
    });
  }

  function typeQuery(text: string) {
    for (const ch of text) pressKey(ch);
  }

  function findBar(): HTMLElement | null {
    return container.querySelector("[data-testid='plex-find']");
  }

  it("typing builds a query, shows the find bar, and dims non-matches", () => {
    renderPage("/entities");
    expect(findBar()).toBeNull();
    typeQuery("an");
    expect(findBar()?.textContent).toContain("an");
    expect(findBar()?.textContent).toContain("1 match");
    // Ana matches and keeps full opacity; Bea recedes.
    expect(nodeByName("Ana")?.style.opacity).not.toBe("0.2");
    expect(nodeByName("Bea")?.style.opacity).toBe("0.2");
  });

  it("matches halo satellites too", () => {
    vi.mocked(usePlexHalo).mockReturnValue(
      loaded(HALO) as ReturnType<typeof usePlexHalo>,
    );
    renderPage("/entities");
    typeQuery("acme");
    expect(findBar()?.textContent).toContain("1 match");
    const acme = container.querySelector<HTMLButtonElement>(
      "[data-testid='plex-halo-mark'][title='Acme Corp']",
    );
    expect(acme?.style.opacity).not.toBe("0.2");
    expect(nodeByName("Ana")?.style.opacity).toBe("0.2");
  });

  it("Backspace edits and Escape clears the query before popping the trail", () => {
    renderPage("/entities");
    typeQuery("ana");
    pressKey("Backspace");
    expect(findBar()?.textContent).toContain("an");
    pressKey("Escape");
    expect(findBar()).toBeNull();
    // Escape consumed by the query — still in owner mode, nothing popped.
    expect(currentSearch().get("center")).toBeNull();
  });

  it("Enter jumps to the best match and the query resets on the hop", () => {
    renderPage("/entities");
    typeQuery("bea");
    pressKey("Enter");
    expect(currentSearch().get("center")).toBe("ent-bea");
    expect(findBar()).toBeNull();
  });

  it("'0' with an active query types into the query instead of resetting the view", () => {
    renderPage("/entities");
    typeQuery("a0");
    expect(findBar()?.textContent).toContain("a0");
  });

  it("Enter with a zero-match query in owner mode neither navigates nor clears the query", () => {
    renderPage("/entities");
    typeQuery("zzz"); // matches nothing in the default ranking
    expect(findBar()?.textContent).toContain("0 matches");
    pressKey("Enter");
    // No best match → Enter is a no-op: still owner mode, query survives.
    expect(currentSearch().get("center")).toBeNull();
    expect(findBar()?.textContent).toContain("zzz");
  });

  it("in neighbour mode, Escape clears the query first and only pops the trail on a second press", () => {
    renderPage("/entities?center=ent-cal&trail=ent-ana,ent-bea");
    typeQuery("an");
    expect(findBar()?.textContent).toContain("an");

    // First Escape: the active find owns it — query clears, trail untouched.
    pressKey("Escape");
    expect(findBar()).toBeNull();
    expect(currentSearch().get("center")).toBe("ent-cal");
    expect(currentSearch().get("trail")).toBe("ent-ana,ent-bea");

    // Second Escape: no query now, so it pops one hop off the trail.
    pressKey("Escape");
    expect(currentSearch().get("center")).toBe("ent-bea");
    expect(currentSearch().get("trail")).toBe("ent-ana");
  });
});
