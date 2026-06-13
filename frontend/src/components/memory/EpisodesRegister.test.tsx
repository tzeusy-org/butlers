// @vitest-environment jsdom
/**
 * Component tests for EpisodesRegister — the daybook (bu-2ix8d.5).
 *
 * Acceptance ((memory house-ledger redesign, graduated) prompts/04-register-episodes.md):
 *   - Day headers render TODAY / YESTERDAY / dated correctly across a multi-day
 *     dataset, in reverse-chronological group order.
 *   - `✕` rows are the only red in the register; pending/consolidated rows are
 *     colorless.
 *   - Expansion reveals full content + an explicit `open ↗` link; the row toggles
 *     via an aria-expanded button.
 *   - Importance >= 8 brightens ONLY the time gutter (content/glyph stay neutral).
 *   - ButlerMark is the sole carrier of butler hue.
 *   - Status filter pills are single-select and write the `status` URL param
 *     (dead letter -> status=dead_letter), resetting offset.
 *   - Serif-italic empty states for no-episodes and filter-empty.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, useLocation } from "react-router";

import EpisodesRegister from "@/components/memory/EpisodesRegister";
import { useEpisodes } from "@/hooks/use-memory";
import { groupEpisodesByDay } from "@/lib/memory-derived";
import type { Episode } from "@/api/types";

vi.mock("@/hooks/use-memory", () => ({
  useEpisodes: vi.fn(),
}));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

type UseEpisodesResult = ReturnType<typeof useEpisodes>;

// Fixed reference instant for deterministic TODAY/YESTERDAY labels.
const NOW = new Date("2026-06-13T12:00:00");

function makeEpisode(overrides: Partial<Episode> = {}): Episode {
  return {
    id: "ep-1",
    butler: "health",
    session_id: null,
    content: "Owner mentioned fatigue again during the afternoon check-in.",
    importance: 5,
    reference_count: 0,
    consolidated: false,
    consolidation_status: "pending",
    created_at: "2026-06-13T14:21:00",
    last_referenced_at: null,
    expires_at: null,
    metadata: {},
    ...overrides,
  };
}

let lastEpisodeParams: unknown;

function setEpisodes(
  episodes: Episode[],
  meta: Partial<{ total: number; offset: number; limit: number; has_more: boolean }> = {},
) {
  vi.mocked(useEpisodes).mockImplementation((params?: unknown) => {
    lastEpisodeParams = params;
    return {
      data: {
        data: episodes,
        meta: {
          total: meta.total ?? episodes.length,
          offset: meta.offset ?? 0,
          limit: meta.limit ?? 50,
          has_more: meta.has_more ?? false,
        },
      },
    } as unknown as UseEpisodesResult;
  });
}

let lastSearch = "";
function LocationProbe() {
  const search = useLocation().search;
  useEffect(() => {
    lastSearch = search;
  }, [search]);
  return null;
}

function renderRegister(initialEntries: string[] = ["/memory"]) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => {
    root.render(
      <MemoryRouter initialEntries={initialEntries}>
        <EpisodesRegister now={NOW} />
        <LocationProbe />
      </MemoryRouter>,
    );
  });
  return { container, root };
}

describe("groupEpisodesByDay (pure)", () => {
  it("groups by local day in incoming order and labels TODAY / YESTERDAY / dated", () => {
    const groups = groupEpisodesByDay(
      [
        makeEpisode({ id: "today-a", created_at: "2026-06-13T14:21:00" }),
        makeEpisode({ id: "today-b", created_at: "2026-06-13T09:05:00" }),
        makeEpisode({ id: "yday", created_at: "2026-06-12T23:00:00" }),
        makeEpisode({ id: "older", created_at: "2026-06-10T08:00:00" }),
      ],
      NOW,
    );

    expect(groups.map((g) => g.label)).toEqual([
      "TODAY",
      "YESTERDAY",
      "WED 10 JUN",
    ]);
    expect(groups[0]!.episodes.map((e) => e.id)).toEqual(["today-a", "today-b"]);
    expect(groups[1]!.episodes.map((e) => e.id)).toEqual(["yday"]);
    expect(groups[2]!.episodes.map((e) => e.id)).toEqual(["older"]);
  });

  it("starts a fresh group when the same day reappears (does not merge non-adjacent days)", () => {
    const groups = groupEpisodesByDay(
      [
        makeEpisode({ id: "a", created_at: "2026-06-13T14:00:00" }),
        makeEpisode({ id: "b", created_at: "2026-06-12T14:00:00" }),
        makeEpisode({ id: "c", created_at: "2026-06-13T08:00:00" }),
      ],
      NOW,
    );
    // Three groups even though day repeats — order is preserved, never sorted.
    expect(groups.map((g) => g.label)).toEqual(["TODAY", "YESTERDAY", "TODAY"]);
  });
});

describe("EpisodesRegister — the daybook", () => {
  let mounted: { container: HTMLDivElement; root: Root } | null = null;

  beforeEach(() => {
    vi.resetAllMocks();
    lastEpisodeParams = undefined;
    lastSearch = "";
  });

  afterEach(() => {
    if (mounted) {
      act(() => mounted!.root.unmount());
      mounted.container.remove();
      mounted = null;
    }
    vi.restoreAllMocks();
  });

  it("requests page size 50 and no status filter by default (all)", () => {
    setEpisodes([makeEpisode()]);
    mounted = renderRegister();
    expect(lastEpisodeParams).toMatchObject({ offset: 0, limit: 50 });
    expect((lastEpisodeParams as { status?: string }).status).toBeUndefined();
  });

  it("renders TODAY / YESTERDAY / dated headers for a multi-day dataset", () => {
    setEpisodes([
      makeEpisode({ id: "t", created_at: "2026-06-13T14:21:00" }),
      makeEpisode({ id: "y", created_at: "2026-06-12T10:00:00" }),
      makeEpisode({ id: "o", created_at: "2026-06-10T08:00:00" }),
    ]);
    mounted = renderRegister();
    const text = mounted.container.textContent ?? "";
    expect(text).toContain("TODAY");
    expect(text).toContain("YESTERDAY");
    expect(text).toContain("WED 10 JUN");
  });

  it("renders the HH:MM time gutter", () => {
    setEpisodes([makeEpisode({ created_at: "2026-06-13T14:21:00" })]);
    mounted = renderRegister();
    expect(mounted.container.textContent).toContain("14:21");
  });

  it("renders zero red pixels for pending/consolidated rows", () => {
    setEpisodes([
      makeEpisode({ id: "p", consolidation_status: "pending" }),
      makeEpisode({ id: "c", consolidation_status: "consolidated" }),
    ]);
    mounted = renderRegister();
    expect(mounted.container.innerHTML).not.toContain("--red");
  });

  it("reddens exactly the dead_letter glyph and nothing else", () => {
    setEpisodes([
      makeEpisode({ id: "ok", consolidation_status: "consolidated" }),
      makeEpisode({ id: "dead", consolidation_status: "dead_letter" }),
    ]);
    mounted = renderRegister();
    const redEls = Array.from(
      mounted.container.querySelectorAll('[class*="text-[var(--red)]"]'),
    );
    expect(redEls).toHaveLength(1);
    expect(redEls[0]!.textContent).toBe("✕");
  });

  it("maps consolidation glyphs: pending ◦, consolidated •, dead_letter ✕", () => {
    setEpisodes([
      makeEpisode({ id: "p", consolidation_status: "pending" }),
      makeEpisode({ id: "c", consolidation_status: "consolidated" }),
      makeEpisode({ id: "d", consolidation_status: "dead_letter" }),
    ]);
    mounted = renderRegister();
    const text = mounted.container.textContent ?? "";
    expect(text).toContain("◦");
    expect(text).toContain("•");
    expect(text).toContain("✕");
  });

  it("brightens ONLY the time gutter when importance >= 8", () => {
    setEpisodes([makeEpisode({ importance: 9, created_at: "2026-06-13T07:08:00" })]);
    mounted = renderRegister();
    const row = mounted.container.querySelector<HTMLElement>('[role="button"]')!;
    // The time gutter (first cell, contains 07:08) is full-fg (NOT muted).
    const timeCell = Array.from(row.children).find((c) =>
      (c.textContent ?? "").includes("07:08"),
    ) as HTMLElement | undefined;
    expect(timeCell).toBeDefined();
    // Mono full-fg variant uses --fg; the muted variant uses --mfg.
    expect(timeCell!.className).toContain("--fg");
    expect(timeCell!.className).not.toContain("--mfg");
  });

  it("mutes the time gutter when importance < 8", () => {
    setEpisodes([makeEpisode({ importance: 5, created_at: "2026-06-13T07:08:00" })]);
    mounted = renderRegister();
    const row = mounted.container.querySelector<HTMLElement>('[role="button"]')!;
    const timeCell = Array.from(row.children).find((c) =>
      (c.textContent ?? "").includes("07:08"),
    ) as HTMLElement | undefined;
    expect(timeCell!.className).toContain("--mfg");
  });

  it("renders a ButlerMark carrying the butler hue", () => {
    setEpisodes([makeEpisode({ butler: "health" })]);
    mounted = renderRegister();
    // ButlerMark renders the butler initial with aria-label=name.
    const mark = mounted.container.querySelector('[aria-label="health"]');
    expect(mark).not.toBeNull();
    expect(mark!.textContent).toBe("H");
    // It carries a category hue via inline style (color or background).
    const style = (mark as HTMLElement).getAttribute("style") ?? "";
    expect(style).toContain("category");
  });

  it("toggles in-place expansion and reveals an explicit open link", () => {
    setEpisodes([makeEpisode({ id: "ep-x" })]);
    mounted = renderRegister();
    const row = mounted.container.querySelector<HTMLElement>('[role="button"]')!;
    expect(row.getAttribute("aria-expanded")).toBe("false");
    // Collapsed: no open link yet.
    expect(mounted.container.textContent).not.toContain("open");

    act(() => {
      row.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(row.getAttribute("aria-expanded")).toBe("true");
    const openLink = mounted.container.querySelector(
      'a[href="/memory/episodes/ep-x"]',
    );
    expect(openLink).not.toBeNull();
    expect(openLink!.textContent).toContain("open");
  });

  it("animates expansion via a height (max-height) transition, not opacity/scale", () => {
    setEpisodes([makeEpisode()]);
    mounted = renderRegister();
    const html = mounted.container.innerHTML;
    expect(html).toContain("transition-[max-height]");
    expect(html).toContain("duration-[120ms]");
    expect(html).not.toContain("transition-opacity");
  });

  it("navigates to the episode detail when the open link is clicked", () => {
    setEpisodes([makeEpisode({ id: "ep-nav" })]);
    mounted = renderRegister();
    const row = mounted.container.querySelector<HTMLElement>('[role="button"]')!;
    act(() => {
      row.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    const openLink = mounted.container.querySelector<HTMLAnchorElement>(
      'a[href="/memory/episodes/ep-nav"]',
    )!;
    act(() => {
      openLink.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    // Navigation does not throw and the row link points at the detail route.
    expect(openLink.getAttribute("href")).toBe("/memory/episodes/ep-nav");
  });

  it("renders the four status pills with all selected by default", () => {
    setEpisodes([makeEpisode()]);
    mounted = renderRegister();
    const pills = Array.from(
      mounted.container.querySelectorAll<HTMLButtonElement>('button[role="switch"]'),
    );
    const labels = pills.map((p) => p.textContent);
    for (const m of ["all", "pending", "consolidated", "dead letter"]) {
      expect(labels).toContain(m);
    }
    const all = pills.find((p) => p.textContent === "all");
    expect(all!.getAttribute("aria-checked")).toBe("true");
  });

  it("selecting a status pill writes the status URL param and resets offset", () => {
    setEpisodes([makeEpisode()], { total: 200, has_more: true });
    mounted = renderRegister(["/memory?offset=100"]);
    const pills = Array.from(
      mounted.container.querySelectorAll<HTMLButtonElement>('button[role="switch"]'),
    );
    const deadPill = pills.find((p) => p.textContent === "dead letter")!;

    act(() => {
      deadPill.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    // URL param uses the enum value, not the human label, and offset is dropped.
    expect(lastSearch).toContain("status=dead_letter");
    expect(lastSearch).not.toContain("offset=100");
    // The fetch is re-issued with the enum status + offset 0.
    expect(lastEpisodeParams).toMatchObject({ status: "dead_letter", offset: 0 });
  });

  it("reads an initial status param from the URL and marks that pill selected", () => {
    setEpisodes([makeEpisode({ consolidation_status: "consolidated" })]);
    mounted = renderRegister(["/memory?status=consolidated"]);
    expect((lastEpisodeParams as { status?: string }).status).toBe("consolidated");
    const pills = Array.from(
      mounted.container.querySelectorAll<HTMLButtonElement>('button[role="switch"]'),
    );
    const consolidated = pills.find((p) => p.textContent === "consolidated");
    expect(consolidated!.getAttribute("aria-checked")).toBe("true");
    const all = pills.find((p) => p.textContent === "all");
    expect(all!.getAttribute("aria-checked")).toBe("false");
  });

  it("shows the offset pagination footer as `1–N of M`", () => {
    setEpisodes([makeEpisode()], { total: 1204, offset: 0, has_more: true });
    mounted = renderRegister();
    const text = mounted.container.textContent ?? "";
    expect(text).toContain("1–1 of 1,204");
    expect(text).toContain("prev");
    expect(text).toContain("next");
  });

  it("renders the no-episodes empty Voice line when unfiltered", () => {
    setEpisodes([], { total: 0 });
    mounted = renderRegister();
    expect(mounted.container.textContent).toContain("Nothing observed yet.");
  });

  it("renders the filtered-empty Voice line for a status filter", () => {
    setEpisodes([], { total: 0 });
    mounted = renderRegister(["/memory?status=dead_letter"]);
    expect(mounted.container.textContent).toContain("Nothing in the daybook for this.");
  });
});
