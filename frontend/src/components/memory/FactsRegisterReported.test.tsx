// @vitest-environment jsdom
/**
 * The "reported" filter on the facts ledger (bu-jv1moa.14): held facts are those
 * a non-owner reported and the owner has not vouched for.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";

import FactsRegister from "@/components/memory/FactsRegister";
import { useFacts } from "@/hooks/use-memory";
import type { Fact } from "@/api/types";

vi.mock("@/hooks/use-memory", () => ({
  useFacts: vi.fn(),
}));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

type UseFactsResult = ReturnType<typeof useFacts>;

const NOW = new Date("2026-06-13T00:00:00Z");
let paramsSeen: Array<Record<string, unknown> | undefined> = [];

function makeFact(id: string, overrides: Partial<Fact> = {}): Fact {
  return {
    id,
    subject: "Owner",
    predicate: "allergic_to",
    content: "peanuts",
    importance: 5,
    confidence: 0.9,
    decay_rate: 0,
    permanence: "stable",
    source_butler: "health",
    source_episode_id: null,
    session_id: null,
    supersedes_id: null,
    entity_id: null,
    entity_name: null,
    object_entity_id: null,
    object_entity_name: null,
    validity: "active",
    scope: "health",
    reference_count: 1,
    created_at: "2026-06-13T00:00:00Z",
    last_referenced_at: null,
    last_confirmed_at: null,
    tags: [],
    metadata: {},
    ...overrides,
  };
}

function setFacts(facts: Fact[]) {
  vi.mocked(useFacts).mockImplementation((params?: unknown) => {
    paramsSeen.push(params as Record<string, unknown> | undefined);
    return {
      data: { data: facts, meta: { total: facts.length, offset: 0, limit: 50, has_more: false } },
    } as unknown as UseFactsResult;
  });
}

function pill(container: HTMLElement, label: string): HTMLElement {
  const found = Array.from(container.querySelectorAll("button")).find(
    (b) => b.textContent?.trim() === label,
  );
  expect(found, `pill ${label}`).toBeDefined();
  return found as HTMLElement;
}

describe("FactsRegister reported filter", () => {
  let mounted: { container: HTMLDivElement; root: Root } | null = null;

  beforeEach(() => {
    paramsSeen = [];
    const container = document.createElement("div");
    document.body.appendChild(container);
    mounted = { container, root: createRoot(container) };
  });

  afterEach(() => {
    act(() => mounted!.root.unmount());
    mounted!.container.remove();
    mounted = null;
  });

  function render() {
    act(() => {
      mounted!.root.render(
        <MemoryRouter initialEntries={["/memory"]}>
          <FactsRegister now={NOW} />
        </MemoryRouter>,
      );
    });
    return mounted!.container;
  }

  it("does not ask for held facts by default", () => {
    setFacts([makeFact("a")]);
    render();
    expect(paramsSeen.at(-1)?.held).toBeUndefined();
  });

  it("asks for held=true when the reported pill is selected, and again clears it", () => {
    setFacts([makeFact("a", { held: true })]);
    const container = render();
    act(() => pill(container, "reported").click());
    expect(paramsSeen.at(-1)?.held).toBe(true);
    act(() => pill(container, "reported").click());
    expect(paramsSeen.at(-1)?.held).toBeUndefined();
  });

  it("marks held rows as reported and leaves vouched or owner rows unmarked", () => {
    setFacts([
      makeFact("a", { held: true, content: "held claim" }),
      makeFact("b", { held: false, content: "vouched claim" }),
    ]);
    const container = render();
    const rows = Array.from(container.querySelectorAll('[role="link"]'));
    const held = rows.find((r) => r.textContent?.includes("held claim"))!;
    const vouched = rows.find((r) => r.textContent?.includes("vouched claim"))!;
    expect(held.textContent).toContain("reported");
    expect(vouched.textContent).not.toContain("reported");
  });

  it("says nothing is waiting when the reported view is empty", () => {
    setFacts([]);
    const container = render();
    act(() => pill(container, "reported").click());
    expect(container.textContent).toContain("Nothing is waiting on your vouch.");
  });
});
