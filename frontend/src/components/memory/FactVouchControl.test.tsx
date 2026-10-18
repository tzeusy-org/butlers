// @vitest-environment jsdom
/**
 * Component tests for the owner Vouch action on a held fact (bu-jv1moa.14).
 *
 *   - FactDetailPage renders Vouch only for a held fact.
 *   - Clicking Vouch calls the vouch mutation with the fact id.
 *   - A vouched, owner-authored or otherwise not-held fact shows no Vouch button.
 *   - The state line says the fact is reported, then vouched.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";

import FactDetailPage from "@/pages/FactDetailPage";
import { useConfirmFact, useFact, useRetractFact, useVouchFact } from "@/hooks/use-memory";
import type { Fact } from "@/api/types";

vi.mock("react-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router")>();
  return { ...actual, useParams: vi.fn(() => ({ factId: "fact-001" })) };
});

vi.mock("@/hooks/use-memory", () => ({
  useFact: vi.fn(),
  useConfirmFact: vi.fn(),
  useRetractFact: vi.fn(),
  useVouchFact: vi.fn(),
}));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

const NOW = new Date("2026-06-13T00:00:00Z");
const vouchMutate = vi.fn();

function makeFact(overrides: Partial<Fact> = {}): Fact {
  return {
    id: "7a3f21c9-0000-0000-0000-000000000000",
    subject: "Owner",
    predicate: "allergic_to",
    content: "peanuts",
    importance: 5,
    confidence: 0.9,
    decay_rate: 0.002,
    permanence: "standard",
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
    created_at: "2026-06-01T00:00:00Z",
    last_referenced_at: null,
    last_confirmed_at: null,
    tags: [],
    metadata: {},
    content_authority: "third_party",
    held: true,
    ...overrides,
  };
}

function setFact(fact: Fact, vouch: { isPending?: boolean; isError?: boolean } = {}) {
  vi.mocked(useFact).mockReturnValue({
    data: { data: fact },
    isLoading: false,
    error: null,
  } as ReturnType<typeof useFact>);
  vi.mocked(useConfirmFact).mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
  } as unknown as ReturnType<typeof useConfirmFact>);
  vi.mocked(useRetractFact).mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
  } as unknown as ReturnType<typeof useRetractFact>);
  vi.mocked(useVouchFact).mockReturnValue({
    mutate: vouchMutate,
    isPending: vouch.isPending ?? false,
    isError: vouch.isError ?? false,
  } as unknown as ReturnType<typeof useVouchFact>);
}

function vouchButton(container: HTMLElement): HTMLButtonElement | undefined {
  return Array.from(container.querySelectorAll("button")).find(
    (b) => b.textContent?.trim() === "Vouch",
  );
}

describe("Vouch on a held fact", () => {
  let mounted: { container: HTMLDivElement; root: Root } | null = null;

  function render() {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    act(() => {
      root.render(
        <MemoryRouter>
          <FactDetailPage now={NOW} />
        </MemoryRouter>,
      );
    });
    mounted = { container, root };
    return container;
  }

  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    if (mounted) {
      act(() => mounted!.root.unmount());
      mounted.container.remove();
      mounted = null;
    }
  });

  it("shows Vouch for a held fact and calls the mutation with its id", () => {
    setFact(makeFact());
    const container = render();
    const button = vouchButton(container);
    expect(button).toBeDefined();
    act(() => button!.click());
    expect(vouchMutate).toHaveBeenCalledWith("7a3f21c9-0000-0000-0000-000000000000");
    expect(container.textContent).toContain("reported, not vouched for");
  });

  it.each([
    ["an owner-authored fact", { held: false, content_authority: "owner" }],
    ["a legacy fact", { held: false, content_authority: null }],
    ["a retracted report", { held: false, validity: "retracted" }],
  ])("shows no Vouch button for %s", (_label, overrides) => {
    setFact(makeFact(overrides));
    const container = render();
    expect(vouchButton(container)).toBeUndefined();
    expect(container.textContent).not.toContain("reported, not vouched for");
  });

  it("shows no Vouch button once vouched and says so", () => {
    setFact(makeFact({ held: false, endorsed_at: "2026-06-12T00:00:00Z" }));
    const container = render();
    expect(vouchButton(container)).toBeUndefined();
    expect(container.textContent).toContain("vouched by you");
    // Vouching is additive: the original authority is still what the API reports.
    expect(container.textContent).not.toContain("reported, not vouched for");
  });

  it("disables Vouch while the request is in flight and surfaces a failure", () => {
    setFact(makeFact(), { isPending: true });
    expect(vouchButton(render())!.disabled).toBe(true);
    act(() => mounted!.root.unmount());
    mounted!.container.remove();
    mounted = null;

    setFact(makeFact(), { isError: true });
    expect(render().textContent).toContain("Could not vouch");
  });
});
