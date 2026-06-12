// @vitest-environment jsdom
/**
 * Component tests for RulesRegister — standing orders (bu-2ix8d.4).
 *
 * Acceptance (pr/overview/memory-redesign/prompts/03-register-rules.md):
 *   - A dataset with zero anti_pattern, zero-harmful rules renders zero red
 *     pixels (no --red anywhere in the markup).
 *   - One rule with harmful > 0 reddens exactly the `harmful N` fragment; an
 *     anti_pattern rule additionally carries the 2px --red left sliver.
 *   - §NN numbering is zero-padded and global in render order; it recomputes on
 *     filter change.
 *   - Maturity renders the raw API word (lowercase, no title-casing, no chip).
 *   - anti_pattern rows pin to the top.
 *   - Maturity filter pills are single-select and write the `maturity` URL param
 *     (resetting offset).
 *   - Row click opens /memory/rules/:id.
 *   - Offset pagination footer reads `1–N of M`.
 *   - Serif-italic empty states for no-rules and filter-empty.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, useLocation } from "react-router";

import RulesRegister from "@/components/memory/RulesRegister";
import { useRules } from "@/hooks/use-memory";
import type { MemoryRule } from "@/api/types";

vi.mock("@/hooks/use-memory", () => ({
  useRules: vi.fn(),
}));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

type UseRulesResult = ReturnType<typeof useRules>;

function makeRule(overrides: Partial<MemoryRule> = {}): MemoryRule {
  return {
    id: "rule-1",
    content: "Suggest a sleep study when fatigue is reported three days running.",
    scope: "lifestyle",
    maturity: "proven",
    confidence: 0.86,
    decay_rate: 0,
    permanence: "stable",
    effectiveness_score: 0.9,
    applied_count: 41,
    success_count: 38,
    harmful_count: 0,
    source_episode_id: null,
    source_butler: "lifestyle",
    created_at: "2026-06-13T00:00:00Z",
    last_applied_at: null,
    last_evaluated_at: null,
    tags: [],
    metadata: {},
    ...overrides,
  };
}

let lastRulesParams: unknown;

function setRules(
  rules: MemoryRule[],
  meta: Partial<{ total: number; offset: number; limit: number; has_more: boolean }> = {},
) {
  vi.mocked(useRules).mockImplementation((params?: unknown) => {
    lastRulesParams = params;
    return {
      data: {
        data: rules,
        meta: {
          total: meta.total ?? rules.length,
          offset: meta.offset ?? 0,
          limit: meta.limit ?? 50,
          has_more: meta.has_more ?? false,
        },
      },
    } as unknown as UseRulesResult;
  });
}

let lastPathname = "";
function LocationProbe() {
  const pathname = useLocation().pathname;
  useEffect(() => {
    lastPathname = pathname;
  }, [pathname]);
  return null;
}

function renderRegister(initialEntries: string[] = ["/memory"]) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => {
    root.render(
      <MemoryRouter initialEntries={initialEntries}>
        <RulesRegister />
        <LocationProbe />
      </MemoryRouter>,
    );
  });
  return { container, root };
}

describe("RulesRegister — standing orders", () => {
  let mounted: { container: HTMLDivElement; root: Root } | null = null;

  beforeEach(() => {
    vi.resetAllMocks();
    lastRulesParams = undefined;
    lastPathname = "";
  });

  afterEach(() => {
    if (mounted) {
      act(() => mounted!.root.unmount());
      mounted.container.remove();
      mounted = null;
    }
    vi.restoreAllMocks();
  });

  it("requests page size 50 and no maturity filter by default (all)", () => {
    setRules([makeRule()]);
    mounted = renderRegister();
    expect(lastRulesParams).toMatchObject({ offset: 0, limit: 50 });
    // `all` => no concrete maturity filter sent to the API.
    expect((lastRulesParams as { maturity?: string }).maturity).toBeUndefined();
  });

  it("renders §NN numbering zero-padded in render order", () => {
    setRules([
      makeRule({ id: "a", maturity: "proven", confidence: 0.9 }),
      makeRule({ id: "b", maturity: "proven", confidence: 0.5 }),
    ]);
    mounted = renderRegister();
    const text = mounted.container.textContent ?? "";
    expect(text).toContain("§01");
    expect(text).toContain("§02");
  });

  it("renders the directive content and a two-decimal confidence numeral", () => {
    setRules([makeRule({ confidence: 0.86 })]);
    mounted = renderRegister();
    const text = mounted.container.textContent ?? "";
    expect(text).toContain("Suggest a sleep study");
    expect(text).toContain("0.86");
    expect(text).not.toContain("%");
  });

  it("renders the tally line as applied · helpful · harmful", () => {
    setRules([makeRule({ applied_count: 41, success_count: 38, harmful_count: 1 })]);
    mounted = renderRegister();
    const text = mounted.container.textContent ?? "";
    expect(text).toContain("applied 41");
    expect(text).toContain("helpful 38");
    expect(text).toContain("harmful 1");
  });

  it("renders zero red pixels for a zero-harm, zero-anti_pattern dataset", () => {
    setRules([
      makeRule({ id: "a", maturity: "proven", harmful_count: 0 }),
      makeRule({ id: "b", maturity: "established", harmful_count: 0 }),
    ]);
    mounted = renderRegister();
    expect(mounted.container.innerHTML).not.toContain("--red");
  });

  it("reddens exactly the harmful fragment when harmful > 0", () => {
    setRules([makeRule({ harmful_count: 4 })]);
    mounted = renderRegister();
    // The --red class appears, and it wraps the harmful fragment text.
    const redEl = mounted.container.querySelector('[class*="text-[var(--red)]"]');
    expect(redEl).not.toBeNull();
    expect(redEl!.textContent).toContain("harmful 4");
    // The applied/helpful fragments are NOT inside the red element.
    expect(redEl!.textContent).not.toContain("applied");
    expect(redEl!.textContent).not.toContain("helpful");
  });

  it("gives anti_pattern rows the 2px red left sliver and others a transparent one", () => {
    setRules([
      makeRule({ id: "anti", maturity: "anti_pattern" }),
      makeRule({ id: "ok", maturity: "proven" }),
    ]);
    mounted = renderRegister();
    const rows = Array.from(
      mounted.container.querySelectorAll<HTMLDivElement>('[role="link"]'),
    );
    // anti_pattern pins to the top → first row.
    expect(rows[0]!.className).toContain("border-l-[var(--red)]");
    expect(rows[1]!.className).toContain("border-l-transparent");
    expect(rows[1]!.className).not.toContain("border-l-[var(--red)]");
  });

  it("pins anti_pattern rules to the top regardless of input order", () => {
    setRules([
      makeRule({ id: "proven", maturity: "proven", confidence: 0.99 }),
      makeRule({ id: "anti", maturity: "anti_pattern", confidence: 0.2 }),
      makeRule({ id: "candidate", maturity: "candidate", confidence: 0.5 }),
    ]);
    mounted = renderRegister();
    const rows = Array.from(
      mounted.container.querySelectorAll<HTMLDivElement>('[role="link"]'),
    );
    // Order: anti_pattern (pinned), proven, candidate.
    expect(rows[0]!.textContent).toContain("anti_pattern");
    expect(rows[1]!.textContent).toContain("proven");
    expect(rows[2]!.textContent).toContain("candidate");
    // §NN renumbered in this render order.
    expect(rows[0]!.textContent).toContain("§01");
    expect(rows[2]!.textContent).toContain("§03");
  });

  it("renders the maturity word verbatim (lowercase API vocabulary, no chip)", () => {
    setRules([makeRule({ maturity: "anti_pattern" })]);
    mounted = renderRegister();
    const text = mounted.container.textContent ?? "";
    // Raw API word, no title-casing or hyphen rewrite.
    expect(text).toContain("anti_pattern");
    expect(text).not.toContain("Anti");
    expect(text).not.toContain("anti-pattern");
  });

  it("renders the five maturity filter pills with all selected by default", () => {
    setRules([makeRule()]);
    mounted = renderRegister();
    const pills = Array.from(
      mounted.container.querySelectorAll<HTMLButtonElement>('button[role="switch"]'),
    );
    const labels = pills.map((p) => p.textContent);
    for (const m of ["all", "candidate", "established", "proven", "anti_pattern"]) {
      expect(labels).toContain(m);
    }
    const all = pills.find((p) => p.textContent === "all");
    expect(all!.getAttribute("aria-checked")).toBe("true");
  });

  it("selecting a maturity pill refetches with that maturity and offset reset", () => {
    setRules([makeRule()], { total: 200, has_more: true });
    mounted = renderRegister(["/memory?offset=100"]);
    const pills = Array.from(
      mounted.container.querySelectorAll<HTMLButtonElement>('button[role="switch"]'),
    );
    const antiPill = pills.find((p) => p.textContent === "anti_pattern");
    expect(antiPill).toBeDefined();

    act(() => {
      antiPill!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(lastRulesParams).toMatchObject({ maturity: "anti_pattern", offset: 0 });
  });

  it("navigates the row to the rule detail on click", () => {
    setRules([makeRule({ id: "rule-x" })]);
    mounted = renderRegister();
    const row = mounted.container.querySelector<HTMLDivElement>('[role="link"]');
    expect(row).not.toBeNull();
    act(() => {
      row!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(lastPathname).toBe("/memory/rules/rule-x");
  });

  it("shows the offset pagination footer as `1–N of M`", () => {
    setRules([makeRule()], { total: 312, offset: 0, has_more: true });
    mounted = renderRegister();
    const text = mounted.container.textContent ?? "";
    expect(text).toContain("1–1 of 312");
    expect(text).toContain("prev");
    expect(text).toContain("next");
  });

  it("renders the no-rules empty Voice line when the all filter is empty", () => {
    setRules([], { total: 0 });
    mounted = renderRegister();
    expect(mounted.container.textContent).toContain("No standing orders yet.");
  });

  it("renders the filtered-empty Voice line for a non-all filter", () => {
    setRules([], { total: 0 });
    mounted = renderRegister(["/memory?maturity=anti_pattern"]);
    expect(mounted.container.textContent).toContain("Nothing of this maturity.");
  });
});
