// @vitest-environment jsdom
//
// MemoryBrowser (bu-2ix8d.6) is the Band-3 left column: the one search
// affordance + register pills + focused browse register, OR grouped search
// results when a query is active. This suite asserts browse/results switching,
// the register pills, and that results reuse the register row shapes.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";

import MemoryBrowser from "@/components/memory/MemoryBrowser";
import {
  useEpisodes,
  useFacts,
  useMemoryInspect,
  useRules,
} from "@/hooks/use-memory";
import type { Episode, MemoryInspectResult } from "@/api/types";

vi.mock("@/hooks/use-memory", () => ({
  useEpisodes: vi.fn(),
  useFacts: vi.fn(),
  useRules: vi.fn(),
  useMemoryInspect: vi.fn(),
}));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

type UseEpisodesResult = ReturnType<typeof useEpisodes>;
type UseFactsResult = ReturnType<typeof useFacts>;
type UseRulesResult = ReturnType<typeof useRules>;
type UseInspectResult = ReturnType<typeof useMemoryInspect>;

const EPISODE_CONTENT = "Owner mentioned fatigue again during the afternoon check-in.";

function makeEpisode(overrides: Partial<Episode> = {}): Episode {
  return {
    id: "episode-1",
    butler: "general",
    session_id: null,
    content: EPISODE_CONTENT,
    importance: 5,
    reference_count: 1,
    consolidated: false,
    consolidation_status: "pending",
    created_at: "2026-02-19T01:00:00Z",
    last_referenced_at: null,
    expires_at: null,
    metadata: {},
    ...overrides,
  };
}

const emptyPage = {
  data: { data: [], meta: { total: 0, offset: 0, limit: 50, has_more: false } },
};

function primeBrowse(episodes: Episode[]) {
  vi.mocked(useFacts).mockReturnValue(emptyPage as unknown as UseFactsResult);
  vi.mocked(useRules).mockReturnValue(emptyPage as unknown as UseRulesResult);
  vi.mocked(useEpisodes).mockReturnValue({
    data: {
      data: episodes,
      meta: { total: episodes.length, offset: 0, limit: 50, has_more: false },
    },
  } as unknown as UseEpisodesResult);
  vi.mocked(useMemoryInspect).mockReturnValue(emptyPage as unknown as UseInspectResult);
}

describe("MemoryBrowser", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.resetAllMocks();
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

  it("renders the search affordance and register pills in browse mode", () => {
    primeBrowse([]);

    act(() => {
      root.render(
        <MemoryRouter>
          <MemoryBrowser />
        </MemoryRouter>,
      );
    });

    // The single search input exists (aria-label), and the register pills.
    expect(container.querySelector('[aria-label="Search memory"]')).not.toBeNull();
    expect(container.textContent).toContain("Facts");
    expect(container.textContent).toContain("Rules");
    expect(container.textContent).toContain("Episodes");
  });

  it("renders the daybook when register=episodes", () => {
    primeBrowse([makeEpisode()]);

    act(() => {
      root.render(
        <MemoryRouter initialEntries={["/memory?register=episodes"]}>
          <MemoryBrowser />
        </MemoryRouter>,
      );
    });

    expect(container.textContent).toContain(EPISODE_CONTENT);
    expect(container.querySelector('[role="button"][aria-expanded]')).not.toBeNull();
  });

  it("wraps the browse register in the §8 opacity cross-fade with the spec duration/easing", () => {
    primeBrowse([makeEpisode()]);

    act(() => {
      root.render(
        <MemoryRouter initialEntries={["/memory?register=episodes"]}>
          <MemoryBrowser />
        </MemoryRouter>,
      );
    });

    // The active register sits inside a cross-fade layer carrying an opacity-only
    // transition at MEMORY_LANGUAGE §8's 200ms cubic-bezier(0.22, 1, 0.36, 1).
    const layer = container.querySelector<HTMLElement>('[data-crossfade-layer="current"]');
    expect(layer).not.toBeNull();
    expect(layer!.style.transitionProperty).toBe("opacity");
    expect(layer!.style.transitionDuration).toBe("200ms");
    expect(layer!.style.transitionTimingFunction).toBe("cubic-bezier(0.22, 1, 0.36, 1)");
    expect(layer!.style.transform).toBe(""); // no scale, no slide
    // The register content lives inside the cross-fade layer.
    expect(layer!.textContent).toContain(EPISODE_CONTENT);
  });

  it("renders grouped results reusing register rows with full embedded data when q is set", () => {
    primeBrowse([]);
    // Each inspect result now carries the full register-shaped row (#2199) so
    // results render real belief/maturity data identical to browse mode.
    const results: MemoryInspectResult[] = [
      {
        id: "fact-1",
        kind: "fact",
        content: "ibuprofen, after meals",
        butler: "lifestyle",
        created_at: "2026-06-13T00:00:00Z",
        metadata: {},
        fact: {
          id: "fact-1",
          subject: "Owner",
          predicate: "preferred_pain_relief",
          content: "ibuprofen, after meals",
          importance: 8,
          confidence: 0.94,
          decay_rate: 0, // 0 → effective == stored, renders the literal "0.94"
          permanence: "stable",
          source_butler: "lifestyle",
          source_episode_id: null,
          session_id: null,
          supersedes_id: null,
          entity_id: null,
          entity_name: null,
          object_entity_id: null,
          object_entity_name: null,
          validity: "active",
          scope: "lifestyle",
          reference_count: 0,
          created_at: "2026-06-13T00:00:00Z",
          last_referenced_at: null,
          last_confirmed_at: "2026-06-13T00:00:00Z",
          tags: [],
          metadata: {},
        },
      },
      {
        id: "rule-1",
        kind: "rule",
        content: "Suggest a sleep study when fatigue is reported.",
        butler: "health",
        created_at: "2026-06-13T00:00:00Z",
        metadata: {},
        rule: {
          id: "rule-1",
          content: "Suggest a sleep study when fatigue is reported.",
          scope: "health",
          maturity: "proven",
          confidence: 0.8,
          decay_rate: 0,
          permanence: "stable",
          effectiveness_score: 0.9,
          applied_count: 12,
          success_count: 11,
          harmful_count: 0,
          source_episode_id: null,
          source_butler: "health",
          created_at: "2026-06-13T00:00:00Z",
          last_applied_at: null,
          last_evaluated_at: null,
          tags: [],
          metadata: {},
        },
      },
    ];
    vi.mocked(useMemoryInspect).mockReturnValue({
      data: { data: results, meta: { total: 2, offset: 0, limit: 50, has_more: false } },
    } as unknown as UseInspectResult);

    act(() => {
      root.render(
        <MemoryRouter initialEntries={["/memory?q=fatigue"]}>
          <MemoryBrowser />
        </MemoryRouter>,
      );
    });

    // Mono kind-group headers with counts, and the row content under them.
    expect(container.textContent).toContain("FACTS · 1");
    expect(container.textContent).toContain("RULES · 1");
    expect(container.textContent).toContain("ibuprofen, after meals");
    expect(container.textContent).toContain("Suggest a sleep study");
    // Real embedded belief data renders identical to browse mode: the fact's
    // confidence numeral (0.94), its predicate, and the rule's maturity.
    expect(container.textContent).toContain("0.94");
    expect(container.textContent).toContain("preferred_pain_relief");
    expect(container.textContent).toContain("proven");
    // Register pills are NOT shown in results mode (one affordance).
    // The fact row is a link to the fact detail (same shape as browse mode).
    expect(container.querySelector('[role="link"]')).not.toBeNull();
  });

  it("shows the empty-results line when a query returns nothing", () => {
    primeBrowse([]);
    vi.mocked(useMemoryInspect).mockReturnValue(emptyPage as unknown as UseInspectResult);

    act(() => {
      root.render(
        <MemoryRouter initialEntries={["/memory?q=nothingmatches"]}>
          <MemoryBrowser />
        </MemoryRouter>,
      );
    });

    expect(container.textContent).toContain("Nothing in the books.");
  });
});
