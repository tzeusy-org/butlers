// @vitest-environment jsdom
/**
 * Component tests for FactDetailPage — the fact's editorial detail page
 * (bu-2ix8d.7).
 *
 * Acceptance ((memory house-ledger redesign, graduated) prompts/06-detail-pages.md):
 *   - Shared skeleton: eyebrow (FACT · <short id>), heading = content, state
 *     line, KV band — no "Details" chrome, exactly one <h1>.
 *   - The decay-arithmetic line renders in the mono `confidence … effective`
 *     format.
 *   - A fading fact dims its heading + state line.
 *   - Empty provenance OMITS the PROVENANCE section (no empty shell).
 *   - The Confirm/Retract commit footer ALWAYS renders (both endpoints live)
 *     and is wired (clicking Confirm calls the mutation; Retract is one-step).
 *   - The reverse `superseded by` link renders iff the payload carries
 *     superseded_by (bu-awo8k.8).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";

import FactDetailPage from "@/pages/FactDetailPage";
import { useConfirmFact, useFact, useRetractFact } from "@/hooks/use-memory";
import type { Fact } from "@/api/types";

vi.mock("react-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router")>();
  return { ...actual, useParams: vi.fn(() => ({ factId: "fact-001" })) };
});

vi.mock("@/hooks/use-memory", () => ({
  useFact: vi.fn(),
  useConfirmFact: vi.fn(),
  useRetractFact: vi.fn(),
}));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

const NOW = new Date("2026-06-13T00:00:00Z");

function makeFact(overrides: Partial<Fact> = {}): Fact {
  return {
    id: "7a3f21c9-0000-0000-0000-000000000000",
    subject: "Owner",
    predicate: "preferred_pain_relief",
    content: "ibuprofen, after meals",
    importance: 5,
    confidence: 0.94,
    decay_rate: 0.002,
    permanence: "standard",
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
    reference_count: 3,
    // 12 days before NOW.
    created_at: "2026-06-01T00:00:00Z",
    last_referenced_at: null,
    last_confirmed_at: "2026-06-01T00:00:00Z",
    tags: [],
    metadata: {},
    ...overrides,
  };
}

const confirmMutate = vi.fn();
const retractMutate = vi.fn();

function setFact(fact: Fact | null, isLoading = false) {
  vi.mocked(useFact).mockReturnValue({
    data: fact ? { data: fact } : undefined,
    isLoading,
    error: null,
  } as ReturnType<typeof useFact>);
  vi.mocked(useConfirmFact).mockReturnValue({
    mutate: confirmMutate,
    isPending: false,
  } as unknown as ReturnType<typeof useConfirmFact>);
  vi.mocked(useRetractFact).mockReturnValue({
    mutate: retractMutate,
    isPending: false,
  } as unknown as ReturnType<typeof useRetractFact>);
}

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
  return { container, root };
}

describe("FactDetailPage", () => {
  let mounted: { container: HTMLDivElement; root: Root } | null = null;

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

  it("renders the editorial skeleton: eyebrow, content heading, state line", () => {
    setFact(makeFact());
    mounted = render();
    const text = mounted.container.textContent ?? "";
    // Eyebrow: FACT · short id (8 hex, hyphens stripped, uppercase)
    expect(text).toContain("FACT · 7A3F21C9");
    // Heading = the content, in the single <h1>
    const h1 = mounted.container.querySelectorAll("h1");
    expect(h1.length).toBe(1);
    expect(h1[0].textContent).toContain("ibuprofen, after meals");
    // State line in the API's words — no colored status chip
    expect(text).toContain("active");
    expect(text).toContain("standard permanence");
    expect(text).toContain("lifestyle scope");
  });

  it("renders the record-identity subtitle (subject · predicate) below the heading", () => {
    setFact(makeFact({ subject: "Owner", predicate: "preferred_pain_relief" }));
    mounted = render();
    const text = mounted.container.textContent ?? "";
    expect(text).toContain("Owner · preferred_pain_relief");
  });

  it("renders the metadata as a mono code block when non-empty", () => {
    setFact(makeFact({ metadata: { confidence_source: "owner_confirmed" } }));
    mounted = render();
    const text = mounted.container.textContent ?? "";
    expect(text).toContain("METADATA");
    const pre = mounted.container.querySelector("pre");
    expect(pre).not.toBeNull();
    expect(pre?.textContent).toContain("confidence_source");
    expect(pre?.textContent).toContain("owner_confirmed");
  });

  it("omits the metadata block when the bag is empty", () => {
    setFact(makeFact({ metadata: {} }));
    mounted = render();
    const text = mounted.container.textContent ?? "";
    expect(text).not.toContain("METADATA");
    expect(mounted.container.querySelector("pre")).toBeNull();
  });

  it("renders the decay-arithmetic line in the mono format", () => {
    setFact(makeFact({ confidence: 0.94, decay_rate: 0.002 }));
    mounted = render();
    const text = mounted.container.textContent ?? "";
    expect(text).toContain("confidence 0.94");
    expect(text).toContain("decays 0.002/day");
    expect(text).toContain("last confirmed 12d ago");
    expect(text).toMatch(/effective 0\.9\d/);
  });

  it("dims the heading when the fact is fading", () => {
    setFact(makeFact({ validity: "fading" }));
    mounted = render();
    const h1 = mounted.container.querySelector("h1");
    expect(h1?.className).toContain("var(--dim)");
  });

  it("omits the PROVENANCE section when there is no provenance", () => {
    setFact(makeFact({ source_episode_id: null, supersedes_id: null }));
    mounted = render();
    const text = mounted.container.textContent ?? "";
    expect(text).not.toContain("PROVENANCE");
  });

  it("renders forward provenance links (episode + supersedes)", () => {
    setFact(
      makeFact({
        source_episode_id: "ep-12345678",
        supersedes_id: "old-87654321",
      }),
    );
    mounted = render();
    const text = mounted.container.textContent ?? "";
    expect(text).toContain("PROVENANCE");
    expect(text).toContain("derived from episode");
    expect(text).toContain("supersedes");
  });

  it("renders the reverse 'superseded by' link when the payload carries superseded_by", () => {
    setFact(makeFact({ source_episode_id: "ep-1", superseded_by: "new-13572468" }));
    mounted = render();
    const text = mounted.container.textContent ?? "";
    expect(text).toContain("superseded by");
    expect(text).toContain("new-1357");
  });

  it("omits the reverse link when superseded_by is absent", () => {
    setFact(makeFact({ source_episode_id: "ep-1" }) as Fact);
    mounted = render();
    const text = mounted.container.textContent ?? "";
    expect(text).not.toContain("superseded by");
  });

  it("always renders the Confirm/Retract commit footer (both endpoints live)", () => {
    setFact(makeFact());
    mounted = render();
    const labels = Array.from(mounted.container.querySelectorAll("button")).map(
      (b) => b.textContent,
    );
    expect(labels).toContain("Confirm");
    expect(labels).toContain("Retract");
  });

  it("wires Confirm to the confirm mutation", () => {
    setFact(makeFact());
    mounted = render();
    const confirmBtn = Array.from(
      mounted.container.querySelectorAll("button"),
    ).find((b) => b.textContent === "Confirm")!;
    act(() => {
      confirmBtn.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(confirmMutate).toHaveBeenCalledWith(
      "7a3f21c9-0000-0000-0000-000000000000",
    );
  });

  it("Retract is one-step: first click arms, second click commits", () => {
    setFact(makeFact());
    mounted = render();
    const getRetract = () =>
      Array.from(mounted!.container.querySelectorAll("button")).find((b) =>
        (b.textContent ?? "").startsWith("Retract"),
      )!;
    // First click: arm (does not call the mutation yet).
    act(() => {
      getRetract().dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(retractMutate).not.toHaveBeenCalled();
    expect(getRetract().textContent).toContain("confirm?");
    // Second click: commit.
    act(() => {
      getRetract().dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(retractMutate).toHaveBeenCalledWith(
      "7a3f21c9-0000-0000-0000-000000000000",
    );
  });

  it("renders a not-found voice line when the fact is absent", () => {
    setFact(null);
    mounted = render();
    const text = mounted.container.textContent ?? "";
    expect(text).toContain("not in the ledger");
  });
});
