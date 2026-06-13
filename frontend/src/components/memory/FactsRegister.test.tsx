// @vitest-environment jsdom
/**
 * Component tests for FactsRegister — the ledger (bu-2ix8d.3).
 *
 * Acceptance ((memory house-ledger redesign, graduated) prompts/02-register-facts.md):
 *   - Fading rows render every cell at --dim; active rows render at --fg. The
 *     ONLY difference is foreground color (no color/italic/opacity), so a
 *     grayscale screenshot makes the distinction obvious.
 *   - Zero red/amber/green pixels under any data.
 *   - validity pill writes the `validity` URL param and resets `offset`.
 *   - Entity-anchored subject links to /entities/:id; the row links to the
 *     fact detail; the anchor stops propagation.
 *   - `↳` provenance glyph appears only on rows with source_episode_id.
 *   - Belief column shows two-decimal effective confidence + permanence tag.
 *   - Offset pagination footer reads `1–50 of N`.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, useLocation } from "react-router";

import FactsRegister from "@/components/memory/FactsRegister";
import { useFacts } from "@/hooks/use-memory";
import type { Fact } from "@/api/types";

vi.mock("@/hooks/use-memory", () => ({
  useFacts: vi.fn(),
}));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

type UseFactsResult = ReturnType<typeof useFacts>;

// Fixed reference instant so effectiveConfidence() is deterministic.
const NOW = new Date("2026-06-13T00:00:00Z");

function makeFact(overrides: Partial<Fact> = {}): Fact {
  return {
    id: "fact-1",
    subject: "Owner",
    predicate: "preferred_pain_relief",
    content: "ibuprofen, after meals",
    importance: 5,
    confidence: 0.94,
    // decay_rate 0 keeps effective == confidence for predictable assertions.
    decay_rate: 0,
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
    reference_count: 1,
    created_at: "2026-06-13T00:00:00Z",
    last_referenced_at: null,
    last_confirmed_at: "2026-06-13T00:00:00Z",
    tags: [],
    metadata: {},
    ...overrides,
  };
}

let lastFactsParams: unknown;

function setFacts(
  facts: Fact[],
  meta: Partial<{ total: number; offset: number; limit: number; has_more: boolean }> = {},
) {
  vi.mocked(useFacts).mockImplementation((params?: unknown) => {
    lastFactsParams = params;
    return {
      data: {
        data: facts,
        meta: {
          total: meta.total ?? facts.length,
          offset: meta.offset ?? 0,
          limit: meta.limit ?? 50,
          has_more: meta.has_more ?? false,
        },
      },
    } as unknown as UseFactsResult;
  });
}

// Records the current location pathname so row-navigation can be asserted.
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
        <FactsRegister now={NOW} />
        <LocationProbe />
      </MemoryRouter>,
    );
  });
  return { container, root };
}

describe("FactsRegister — the ledger", () => {
  let mounted: { container: HTMLDivElement; root: Root } | null = null;

  beforeEach(() => {
    vi.resetAllMocks();
    lastFactsParams = undefined;
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

  it("requests active validity and page size 50 by default", () => {
    setFacts([makeFact()]);
    mounted = renderRegister();
    expect(lastFactsParams).toMatchObject({ validity: "active", offset: 0, limit: 50 });
  });

  it("renders subject · predicate, content, and a two-decimal belief numeral", () => {
    setFacts([makeFact({ confidence: 0.94, permanence: "stable" })]);
    mounted = renderRegister();
    const text = mounted.container.textContent ?? "";
    expect(text).toContain("Owner");
    expect(text).toContain("preferred_pain_relief");
    expect(text).toContain("ibuprofen, after meals");
    // Effective confidence, two places, no percent sign. Permanence tag `st`.
    expect(text).toContain("0.94");
    expect(text).toContain("st");
    expect(text).not.toContain("%");
  });

  it("dims the WHOLE fading row to --dim and keeps active rows at --fg", () => {
    setFacts([
      makeFact({ id: "active", validity: "active" }),
      makeFact({ id: "fading", validity: "fading", subject: "Wei" }),
    ]);
    mounted = renderRegister(["/memory?validity=fading"]);

    const rows = Array.from(
      mounted.container.querySelectorAll<HTMLDivElement>('[role="link"]'),
    );
    // Two ledger rows in render order: active first, fading second.
    const active = rows[0];
    const fading = rows[1];
    expect(active).toBeDefined();
    expect(fading).toBeDefined();

    // The single decay affordance: fading row foreground is --dim; active --fg.
    expect(fading!.className).toContain("text-[var(--dim)]");
    expect(active!.className).toContain("text-[var(--fg)]");
    // No italic / opacity transition difference on the fading row.
    expect(fading!.className).not.toContain("italic");
    expect(fading!.className).not.toContain("opacity");
  });

  it("renders zero state-color (red/amber/green) pixels under any data", () => {
    setFacts([
      makeFact({ id: "a", validity: "active" }),
      makeFact({ id: "b", validity: "fading" }),
    ]);
    mounted = renderRegister(["/memory?validity=fading"]);
    const html = mounted.container.innerHTML;
    for (const banned of [
      "--red",
      "--amber",
      "--green",
      "emerald",
      "amber-",
      "text-amber",
      "destructive",
      "bg-sky",
    ]) {
      expect(html).not.toContain(banned);
    }
  });

  it("navigates the row to the fact detail and links the entity subject to /entities/:id", () => {
    setFacts([
      makeFact({ id: "fact-x", entity_id: "ent-7", entity_name: "Owner" }),
    ]);
    mounted = renderRegister();

    // Entity-anchored subject is a real anchor out to the entity page.
    const anchors = Array.from(mounted.container.querySelectorAll<HTMLAnchorElement>("a"));
    expect(anchors.map((a) => a.getAttribute("href"))).toContain("/entities/ent-7");

    // The row itself is a role=link target that navigates to the fact detail.
    const row = mounted.container.querySelector<HTMLDivElement>('[role="link"]');
    expect(row).toBeDefined();
    act(() => {
      row!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(lastPathname).toBe("/memory/facts/fact-x");
  });

  it("clicking the entity anchor does not open the fact (stops propagation)", () => {
    setFacts([
      makeFact({ id: "fact-y", entity_id: "ent-9", entity_name: "Owner" }),
    ]);
    mounted = renderRegister();
    const entityAnchor = Array.from(
      mounted.container.querySelectorAll<HTMLAnchorElement>("a"),
    ).find((a) => a.getAttribute("href") === "/entities/ent-9");
    expect(entityAnchor).toBeDefined();
    act(() => {
      entityAnchor!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    // Navigation went to the entity, NOT the fact detail.
    expect(lastPathname).toBe("/entities/ent-9");
  });

  it("renders the provenance glyph only when source_episode_id is set", () => {
    setFacts([
      makeFact({ id: "with-src", source_episode_id: "ep-12345678abc" }),
      makeFact({ id: "no-src", source_episode_id: null }),
    ]);
    mounted = renderRegister();
    const rows = Array.from(
      mounted.container.querySelectorAll<HTMLDivElement>('[role="link"]'),
    );
    // Render order: with-src first, no-src second.
    const withSrc = rows[0];
    const noSrc = rows[1];
    expect(withSrc.textContent).toContain("↳");
    expect(noSrc.textContent).not.toContain("↳");
  });

  it("shows the offset pagination footer as `1–50 of N`", () => {
    setFacts([makeFact()], { total: 3182, offset: 0, has_more: true });
    mounted = renderRegister();
    expect(mounted.container.textContent).toContain("1–1 of 3,182");
    expect(mounted.container.textContent).toContain("prev");
    expect(mounted.container.textContent).toContain("next");
  });

  it("renders the filtered-empty Voice line for a non-active filter", () => {
    setFacts([], { total: 0 });
    mounted = renderRegister(["/memory?validity=superseded"]);
    expect(mounted.container.textContent).toContain("No facts answer this.");
  });

  it("renders the empty-ledger Voice line when the active filter is empty", () => {
    setFacts([], { total: 0 });
    mounted = renderRegister();
    expect(mounted.container.textContent).toContain("The ledger is empty.");
  });

  it("renders the five validity filter pills with active selected by default", () => {
    setFacts([makeFact()]);
    mounted = renderRegister();
    const pills = Array.from(
      mounted.container.querySelectorAll<HTMLButtonElement>('button[role="switch"]'),
    );
    const labels = pills.map((p) => p.textContent);
    for (const v of ["active", "fading", "superseded", "expired", "retracted"]) {
      expect(labels).toContain(v);
    }
    const active = pills.find((p) => p.textContent === "active");
    expect(active!.getAttribute("aria-checked")).toBe("true");
  });

  it("selecting a validity pill refetches with the new validity and offset reset", () => {
    setFacts([makeFact()], { total: 200, has_more: true });
    mounted = renderRegister(["/memory?offset=100"]);
    const pills = Array.from(
      mounted.container.querySelectorAll<HTMLButtonElement>('button[role="switch"]'),
    );
    const fadingPill = pills.find((p) => p.textContent === "fading");
    expect(fadingPill).toBeDefined();

    act(() => {
      fadingPill!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    // After the click the register re-renders and refetches with validity=fading
    // and offset reset to 0.
    expect(lastFactsParams).toMatchObject({ validity: "fading", offset: 0 });
  });
});
