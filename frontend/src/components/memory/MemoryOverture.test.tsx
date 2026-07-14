// @vitest-environment jsdom
/**
 * Component tests for MemoryOverture (bu-2ix8d.2).
 *
 * Acceptance ((memory house-ledger redesign, graduated) prompts/01-overture.md):
 *   - dead_letter == 0 renders zero red pixels (the dead-letter fragment is
 *     muted, not --red).
 *   - dead_letter > 0 turns ONLY the `dead letters N` fragment --red.
 *   - KPI strip shows pending / active facts / proven rules / last write-up.
 *   - Voice sentence matches the templated output (delegated detail in
 *     memory-overture.test.ts; here we confirm it renders into the band).
 *   - While stats load (data undefined), the headline still renders and the
 *     reserved-height containers exist (no layout shift).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";

import MemoryOverture from "@/components/memory/MemoryOverture";
import { useMemoryStats } from "@/hooks/use-memory";
import type { MemoryStats, MemoryStatsMeta } from "@/api/types";

vi.mock("@/hooks/use-memory", () => ({
  useMemoryStats: vi.fn(),
}));

vi.mock("@/components/ui/timezone-context", () => ({
  useTimezone: () => "Asia/Singapore",
}));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

type UseMemoryStatsResult = ReturnType<typeof useMemoryStats>;

function makeStats(overrides: Partial<MemoryStats> = {}): MemoryStats {
  return {
    total_episodes: 1204,
    unconsolidated_episodes: 41,
    total_facts: 3182,
    active_facts: 3182,
    fading_facts: 207,
    total_rules: 58,
    candidate_rules: 10,
    established_rules: 39,
    proven_rules: 9,
    anti_pattern_rules: 0,
    last_consolidation_at: "2026-06-12T06:00:00+08:00",
    last_consolidation_facts_produced: 12,
    dead_letter_episodes: 0,
    ...overrides,
  };
}

function setStats(stats: MemoryStats | undefined, meta: MemoryStatsMeta = {}) {
  // useMemoryStats() resolves to MemoryStatsResponse = { data, meta }, so the
  // component reads response.data and response.meta.pools_failed. Mirror that
  // envelope here.
  vi.mocked(useMemoryStats).mockReturnValue({
    data: stats == null ? undefined : { data: stats, meta },
    isLoading: stats == null,
    refetch: vi.fn(),
  } as unknown as UseMemoryStatsResult);
}

/** First-load outage: query errored with nothing cached (bu-mkd5r). */
function setStatsError() {
  vi.mocked(useMemoryStats).mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: true,
    refetch: vi.fn(),
  } as unknown as UseMemoryStatsResult);
}

/** The element carrying the dead-letter fragment (whitespace-nowrap span). */
function findDeadLetterEl(container: HTMLElement): HTMLElement | undefined {
  return Array.from(container.querySelectorAll<HTMLElement>("span")).find((el) =>
    /^dead letters/.test(el.textContent ?? ""),
  );
}

/** The element carrying the catalog-drift fragment (whitespace-nowrap span). */
function findDriftEl(container: HTMLElement): HTMLElement | undefined {
  return Array.from(container.querySelectorAll<HTMLElement>("span")).find((el) =>
    /^drifted \d/.test(el.textContent ?? ""),
  );
}

describe("MemoryOverture", () => {
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

  it("renders the static display headline", () => {
    setStats(makeStats());
    act(() => {
      root.render(<MemoryOverture />);
    });
    expect(container.textContent).toContain("What the house believes.");
  });

  it("renders the templated Voice sentence (pending > 0)", () => {
    setStats(makeStats({ unconsolidated_episodes: 41, last_consolidation_facts_produced: 12 }));
    act(() => {
      root.render(<MemoryOverture />);
    });
    expect(container.textContent).toContain(
      "Forty-one observations await the evening write-up; the last ran at 06:00 and produced twelve facts.",
    );
  });

  it("renders the idle Voice sentence (pending == 0)", () => {
    setStats(makeStats({ unconsolidated_episodes: 0 }));
    act(() => {
      root.render(<MemoryOverture />);
    });
    expect(container.textContent).toContain("The pipeline is idle. Nothing pending since 06:00.");
  });

  it("renders the never-run Voice sentence", () => {
    setStats(makeStats({ last_consolidation_at: null, last_consolidation_facts_produced: null }));
    act(() => {
      root.render(<MemoryOverture />);
    });
    expect(container.textContent).toContain("The first write-up has not run yet.");
  });

  it("renders the four KPI strip values", () => {
    setStats(makeStats({ unconsolidated_episodes: 41, active_facts: 3182, proven_rules: 9 }));
    act(() => {
      root.render(<MemoryOverture />);
    });
    const text = container.textContent ?? "";
    expect(text).toContain("Pending");
    expect(text).toContain("41");
    expect(text).toContain("Active facts");
    expect(text).toContain("3,182");
    expect(text).toContain("Proven rules");
    expect(text).toContain("Last write-up");
    // LAST WRITE-UP cell: HH:MM + mono "· N facts" sub-line.
    expect(text).toContain("06:00");
    expect(text).toContain("· 12 facts");
  });

  it("renders an em-dash for LAST WRITE-UP when consolidation has never run", () => {
    setStats(makeStats({ last_consolidation_at: null, last_consolidation_facts_produced: null }));
    act(() => {
      root.render(<MemoryOverture />);
    });
    expect(container.textContent).toContain("—");
  });

  it("renders the pipeline band numerals", () => {
    setStats(makeStats());
    act(() => {
      root.render(<MemoryOverture />);
    });
    const text = container.textContent ?? "";
    expect(text).toContain("episodes");
    expect(text).toContain("1,204");
    expect(text).toContain("pending");
    expect(text).toContain("facts");
    expect(text).toContain("fading");
    expect(text).toContain("207");
    expect(text).toContain("rules");
    expect(text).toContain("proven");
    expect(text).toContain("dead letters");
  });

  it("keeps the dead-letter fragment muted (no red) when dead_letter == 0", () => {
    setStats(makeStats({ dead_letter_episodes: 0 }));
    act(() => {
      root.render(<MemoryOverture />);
    });
    const el = findDeadLetterEl(container);
    expect(el).toBeDefined();
    expect(el!.textContent).toBe("dead letters 0");
    // Muted token, NOT the red token — zero red pixels above the fold.
    expect(el!.className).toContain("text-[var(--mfg)]");
    expect(el!.className).not.toContain("text-[var(--red-text)]");
    // No other element on the band should carry the red token.
    const reds = Array.from(container.querySelectorAll<HTMLElement>("[class*='--red']"));
    expect(reds).toHaveLength(0);
  });

  it("turns ONLY the dead-letter fragment red when dead_letter > 0", () => {
    setStats(makeStats({ dead_letter_episodes: 3 }));
    act(() => {
      root.render(<MemoryOverture />);
    });
    const el = findDeadLetterEl(container);
    expect(el).toBeDefined();
    expect(el!.textContent).toBe("dead letters 3");
    expect(el!.className).toContain("text-[var(--red-text)]");
    // Exactly one red-bearing element: the dead-letter fragment, nothing else.
    const reds = Array.from(container.querySelectorAll<HTMLElement>("[class*='--red']"));
    expect(reds).toHaveLength(1);
    expect(reds[0]).toBe(el);
  });

  it("renders an error state (not a blank calm band) when stats fail to load", () => {
    // bu-mkd5r three-way contract: a first-load outage must surface an honest
    // error-with-retry, never render the blank KPI/pipeline bands (which read
    // as still-loading forever).
    setStatsError();
    act(() => {
      root.render(<MemoryOverture />);
    });
    const errorEl = container.querySelector('[data-testid="memory-overture-error"]');
    expect(errorEl).not.toBeNull();
    expect(errorEl!.getAttribute("role")).toBe("alert");
    expect(errorEl!.textContent).toContain("load memory stats");
    // The pipeline band's "dead letters" numeral must NOT render on outage.
    expect(container.textContent).not.toContain("dead letters");
  });

  it("names the failed pools inline (not an all-clear) when meta.pools_failed is set", () => {
    // Degraded fan-out: the backend answered 200 with partial totals and named
    // the dropped pools in meta.pools_failed. The overture must surface a named
    // SourceDegradedNote so the confident KPI/pipeline totals are not read as a
    // complete verdict — never suppress the missing source (bu-jad4j.1).
    setStats(makeStats(), { pools_failed: ["relationship", "finance"] });
    act(() => {
      root.render(<MemoryOverture />);
    });
    const note = container.querySelector('[data-testid="memory-overture-pools-degraded"]');
    expect(note).not.toBeNull();
    expect(note!.getAttribute("role")).toBe("alert");
    // The failed pools are named inline (em-dash qualifier), not hidden.
    expect(note!.textContent).toContain("relationship, finance");
    expect(note!.textContent).toContain("—");
    // The pipeline band still renders its (partial) totals — the note qualifies
    // them, it does not swap the band out.
    expect(container.textContent).toContain("dead letters");
  });

  it("shows no degraded note on the happy path (meta.pools_failed absent)", () => {
    // Mutation guard: the degraded note must depend on the flag. With every pool
    // answering, the note is absent and the band renders its all-clear totals.
    setStats(makeStats());
    act(() => {
      root.render(<MemoryOverture />);
    });
    expect(
      container.querySelector('[data-testid="memory-overture-pools-degraded"]'),
    ).toBeNull();
    expect(container.textContent).toContain("dead letters");
  });

  // -------------------------------------------------------------------------
  // Catalog-drift gauge (bu-i8jlt)
  // -------------------------------------------------------------------------

  it("renders the catalog gauge counts from meta (healthy: drift muted, no red)", () => {
    setStats(makeStats(), {
      catalog_live: 812,
      catalog_stale: 14,
      catalog_drifted: 0,
    });
    act(() => {
      root.render(<MemoryOverture />);
    });
    const text = container.textContent ?? "";
    expect(text).toContain("catalog live");
    expect(text).toContain("812");
    expect(text).toContain("stale");
    expect(text).toContain("drifted 0");
    // Healthy: the drift fragment is muted, not red (zero red pixels).
    const el = findDriftEl(container);
    expect(el).toBeDefined();
    expect(el!.className).toContain("text-[var(--mfg)]");
    expect(el!.className).not.toContain("text-[var(--red-text)]");
    const reds = Array.from(container.querySelectorAll<HTMLElement>("[class*='--red']"));
    expect(reds).toHaveLength(0);
    // Healthy: no catalog degraded note.
    expect(
      container.querySelector('[data-testid="memory-overture-catalog-degraded"]'),
    ).toBeNull();
  });

  it("turns ONLY the drift fragment red when catalog_drifted > 0", () => {
    setStats(makeStats({ dead_letter_episodes: 0 }), {
      catalog_live: 800,
      catalog_stale: 20,
      catalog_drifted: 5,
    });
    act(() => {
      root.render(<MemoryOverture />);
    });
    const el = findDriftEl(container);
    expect(el).toBeDefined();
    expect(el!.textContent).toBe("drifted 5");
    expect(el!.className).toContain("text-[var(--red-text)]");
    // Exactly one red-bearing element: the drift fragment (dead letters == 0).
    const reds = Array.from(container.querySelectorAll<HTMLElement>("[class*='--red']"));
    expect(reds).toHaveLength(1);
    expect(reds[0]).toBe(el);
  });

  it("names failed catalog pools inline (not a clean gauge) when meta.catalog_pools_failed is set", () => {
    // Degraded catalog fan-out: the backend answered 200 but a catalog pool
    // errored, so the drift counts undercount. The gauge must NOT read as a
    // clean all-clear; name the dropped pools inline (bu-i8jlt).
    setStats(makeStats(), {
      catalog_live: 400,
      catalog_stale: 3,
      catalog_drifted: 0,
      catalog_pools_failed: ["relationship", "finance"],
    });
    act(() => {
      root.render(<MemoryOverture />);
    });
    const note = container.querySelector('[data-testid="memory-overture-catalog-degraded"]');
    expect(note).not.toBeNull();
    expect(note!.getAttribute("role")).toBe("alert");
    expect(note!.textContent).toContain("relationship, finance");
    // The gauge still renders its (partial) counts; the note qualifies them.
    expect(container.textContent).toContain("catalog live");
    // No em-dash in the new degraded copy (em-dash-copy ratchet, PR #3232).
    const catalogNoteText = note!.textContent ?? "";
    expect(catalogNoteText).not.toContain("—");
  });

  it("shows no catalog degraded note on the happy path (catalog_pools_failed absent)", () => {
    // Mutation guard: the catalog note must depend on the flag.
    setStats(makeStats(), { catalog_live: 100, catalog_stale: 0, catalog_drifted: 0 });
    act(() => {
      root.render(<MemoryOverture />);
    });
    expect(
      container.querySelector('[data-testid="memory-overture-catalog-degraded"]'),
    ).toBeNull();
    expect(container.textContent).toContain("catalog live");
  });

  it("renders the headline while stats are still loading (reserved height, no shift)", () => {
    setStats(undefined);
    act(() => {
      root.render(<MemoryOverture />);
    });
    // Headline + eyebrow render immediately; numerals/voice are absent but
    // their reserved-height containers exist so the layout does not shift.
    expect(container.textContent).toContain("What the house believes.");
    expect(container.textContent).not.toContain("dead letters");
    const reserved = container.querySelectorAll<HTMLElement>("[class*='min-h-']");
    expect(reserved.length).toBeGreaterThanOrEqual(3);
  });
});
