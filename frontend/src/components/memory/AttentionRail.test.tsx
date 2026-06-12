// @vitest-environment jsdom
/**
 * Component tests for AttentionRail (bu-2ix8d.6).
 *
 * Acceptance (pr/overview/memory-redesign/prompts/05-search-and-rail.md Part 2):
 *   - Five condition rows render only when their condition holds.
 *   - write-up overdue is ACTION-LESS (no link).
 *   - A fully healthy dataset shows the eyebrow + "Nothing waiting." and zero
 *     red/amber state rows.
 *   - Each actionable row lands on the pre-filtered register it names.
 *   - Recent activity is de-carded (no Card chrome).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";

import AttentionRail from "@/components/memory/AttentionRail";
import {
  useFacts,
  useMemoryActivity,
  useMemoryStats,
} from "@/hooks/use-memory";
import { useReembedPending } from "@/hooks/use-memory-reembed";
import type { MemoryStats } from "@/api/types";

vi.mock("@/hooks/use-memory", () => ({
  useFacts: vi.fn(),
  useMemoryActivity: vi.fn(),
  useMemoryStats: vi.fn(),
}));
vi.mock("@/hooks/use-memory-reembed", () => ({
  useReembedPending: vi.fn(),
}));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const NOW = new Date("2026-06-13T00:00:00Z");

function healthyStats(overrides: Partial<MemoryStats> = {}): MemoryStats {
  return {
    total_episodes: 1000,
    unconsolidated_episodes: 0,
    total_facts: 3000,
    active_facts: 2800,
    fading_facts: 0,
    total_rules: 50,
    candidate_rules: 10,
    established_rules: 30,
    proven_rules: 10,
    anti_pattern_rules: 0,
    // Recent run — not overdue.
    last_consolidation_at: "2026-06-12T18:00:00Z",
    last_consolidation_facts_produced: 12,
    dead_letter_episodes: 0,
    ...overrides,
  };
}

function prime({
  stats,
  fadingTotal = 0,
  staleTotal = 0,
  activity = [],
}: {
  stats: MemoryStats;
  fadingTotal?: number;
  staleTotal?: number;
  activity?: { id: string; type: string; summary: string; butler: string | null; created_at: string }[];
}) {
  vi.mocked(useMemoryStats).mockReturnValue({
    data: { data: stats },
  } as unknown as ReturnType<typeof useMemoryStats>);
  vi.mocked(useFacts).mockReturnValue({
    data: { data: [], meta: { total: fadingTotal, offset: 0, limit: 1, has_more: false } },
  } as unknown as ReturnType<typeof useFacts>);
  vi.mocked(useReembedPending).mockReturnValue({
    data: { data: { counts: {}, total: staleTotal, current_model: "m" } },
  } as unknown as ReturnType<typeof useReembedPending>);
  vi.mocked(useMemoryActivity).mockReturnValue({
    data: { data: activity },
  } as unknown as ReturnType<typeof useMemoryActivity>);
}

function render() {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => {
    root.render(
      <MemoryRouter>
        <AttentionRail now={NOW} />
      </MemoryRouter>,
    );
  });
  return { container, root };
}

describe("AttentionRail", () => {
  let mounted: { container: HTMLDivElement; root: Root } | null = null;

  beforeEach(() => {
    vi.resetAllMocks();
  });

  afterEach(() => {
    if (mounted) {
      act(() => mounted!.root.unmount());
      mounted.container.remove();
      mounted = null;
    }
    vi.restoreAllMocks();
  });

  it("shows 'Nothing waiting.' when the dataset is fully healthy", () => {
    prime({ stats: healthyStats() });
    mounted = render();
    expect(mounted.container.textContent).toContain("Needs attention");
    expect(mounted.container.textContent).toContain("Nothing waiting.");
  });

  it("renders the dead-letter row with an episodes-filtered action", () => {
    prime({ stats: healthyStats({ dead_letter_episodes: 3 }) });
    mounted = render();
    expect(mounted.container.textContent).toContain("3 episodes dead-lettered");
    const link = mounted.container.querySelector(
      'a[href*="register=episodes"][href*="status=dead_letter"]',
    );
    expect(link).not.toBeNull();
  });

  it("renders the anti-pattern row with a rules-filtered action", () => {
    prime({ stats: healthyStats({ anti_pattern_rules: 2 }) });
    mounted = render();
    expect(mounted.container.textContent).toContain("2 anti-pattern rules");
    expect(
      mounted.container.querySelector('a[href*="maturity=anti_pattern"]'),
    ).not.toBeNull();
  });

  it("renders write-up overdue WITHOUT an action link", () => {
    // last run 60h before NOW → overdue (>48h).
    const last = new Date(NOW.getTime() - 60 * 60 * 60 * 1000).toISOString();
    prime({ stats: healthyStats({ last_consolidation_at: last }) });
    mounted = render();
    expect(mounted.container.textContent).toContain("Write-up overdue");
    // No link anywhere in the rail (this is the only attention row present).
    expect(mounted.container.querySelector("a")).toBeNull();
  });

  it("renders the important-fading-facts row from the importance_min count", () => {
    prime({ stats: healthyStats(), fadingTotal: 2 });
    mounted = render();
    expect(mounted.container.textContent).toContain("2 important facts fading");
    expect(
      mounted.container.querySelector('a[href*="validity=fading"]'),
    ).not.toBeNull();
  });

  it("renders the stale-embeddings row anchoring to housekeeping", () => {
    prime({ stats: healthyStats(), staleTotal: 412 });
    mounted = render();
    expect(mounted.container.textContent).toContain("412 rows on an old embedding");
    expect(
      mounted.container.querySelector('a[href*="#housekeeping"]'),
    ).not.toBeNull();
  });

  it("requests the importance_min=8 fading count for the rail", () => {
    prime({ stats: healthyStats() });
    mounted = render();
    expect(useFacts).toHaveBeenCalledWith(
      expect.objectContaining({ validity: "fading", importance_min: 8 }),
    );
  });

  it("renders de-carded recent activity rows", () => {
    prime({
      stats: healthyStats(),
      activity: [
        {
          id: "e1",
          type: "episode",
          summary: "fact stored — Owner · preferred_pain_relief",
          butler: "lifestyle",
          created_at: "2026-06-13T14:21:00Z",
        },
      ],
    });
    mounted = render();
    expect(mounted.container.textContent).toContain("Recent activity");
    expect(mounted.container.textContent).toContain("fact stored — Owner");
  });
});
