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
import type { MemoryStats } from "@/api/types";

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

function setStats(stats: MemoryStats | undefined) {
  // useMemoryStats() resolves to ApiResponse<MemoryStats> = { data, meta },
  // so the component reads response.data. Mirror that envelope here.
  vi.mocked(useMemoryStats).mockReturnValue({
    data: stats == null ? undefined : { data: stats, meta: {} },
    isLoading: stats == null,
  } as unknown as UseMemoryStatsResult);
}

/** The element carrying the dead-letter fragment (whitespace-nowrap span). */
function findDeadLetterEl(container: HTMLElement): HTMLElement | undefined {
  return Array.from(container.querySelectorAll<HTMLElement>("span")).find((el) =>
    /^dead letters/.test(el.textContent ?? ""),
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
    expect(el!.className).not.toContain("text-[var(--red)]");
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
    expect(el!.className).toContain("text-[var(--red)]");
    // Exactly one red-bearing element: the dead-letter fragment, nothing else.
    const reds = Array.from(container.querySelectorAll<HTMLElement>("[class*='--red']"));
    expect(reds).toHaveLength(1);
    expect(reds[0]).toBe(el);
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
