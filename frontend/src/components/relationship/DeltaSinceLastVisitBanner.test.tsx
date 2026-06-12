// @vitest-environment jsdom
/**
 * Component tests for DeltaSinceLastVisitBanner (entity v3, bu-xzh76).
 *
 * Covers:
 * - banner reports N new facts since the mark date when delta is non-empty;
 * - the view mark is posted (after the delta was read) on mount — exactly once;
 * - first visit (marked_at null) renders no banner but still posts the mark;
 * - empty delta (mark exists, 0 changed) renders no banner.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";

import type { DeltaFactEntry, DeltaFactsResponse } from "@/api/types";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

vi.mock("@/hooks/use-entities", () => ({
  useEntityDeltaFacts: vi.fn(),
  useMarkEntityView: vi.fn(),
}));

import { useEntityDeltaFacts, useMarkEntityView } from "@/hooks/use-entities";
import { DeltaSinceLastVisitBanner } from "./DeltaSinceLastVisitBanner";

const markMutate = vi.fn();

function mockDelta(resp: DeltaFactsResponse, isSuccess = true) {
  vi.mocked(useEntityDeltaFacts).mockReturnValue({
    data: resp,
    isSuccess,
  } as unknown as ReturnType<typeof useEntityDeltaFacts>);
}

function fact(id: string, store: DeltaFactEntry["store"]): DeltaFactEntry {
  return {
    id,
    subject: "ent-1",
    predicate: "has-note",
    object: "x",
    object_kind: "literal",
    src: "memory",
    conf: 1,
    store,
    validity: "active",
    created_at: "2026-06-10T00:00:00Z",
    changed_at: "2026-06-10T00:00:00Z",
  };
}

let container: HTMLDivElement;
let root: Root;

function render() {
  act(() => {
    root.render(<DeltaSinceLastVisitBanner entityId="ent-1" />);
  });
}

beforeEach(() => {
  vi.resetAllMocks();
  markMutate.mockReset();
  vi.mocked(useMarkEntityView).mockReturnValue({
    mutate: markMutate,
    isPending: false,
  } as unknown as ReturnType<typeof useMarkEntityView>);
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  document.body.innerHTML = "";
  vi.restoreAllMocks();
});

describe("DeltaSinceLastVisitBanner", () => {
  it("reports 2 new facts since the mark date and posts the mark once", () => {
    mockDelta({
      marked_at: "2026-06-01T00:00:00Z",
      items: [fact("f1", "identity"), fact("f2", "narrative")],
    });
    render();

    const banner = container.querySelector('[data-testid="delta-banner"]');
    expect(banner).not.toBeNull();
    expect(banner?.textContent).toContain("2 new facts");
    expect(banner?.textContent).toContain("Jun 1");

    // Mark posted after delta read, exactly once.
    expect(markMutate).toHaveBeenCalledTimes(1);
    expect(markMutate).toHaveBeenCalledWith("ent-1");
  });

  it("renders no banner on a first visit but still posts the mark", () => {
    mockDelta({ marked_at: null, items: [] });
    render();
    expect(container.querySelector('[data-testid="delta-banner"]')).toBeNull();
    expect(markMutate).toHaveBeenCalledTimes(1);
  });

  it("renders no banner when the mark exists but nothing changed", () => {
    mockDelta({ marked_at: "2026-06-01T00:00:00Z", items: [] });
    render();
    expect(container.querySelector('[data-testid="delta-banner"]')).toBeNull();
    expect(markMutate).toHaveBeenCalledTimes(1);
  });

  it("does not post the mark until the delta read has resolved", () => {
    mockDelta({ marked_at: null, items: [] }, /* isSuccess */ false);
    render();
    expect(markMutate).not.toHaveBeenCalled();
  });

  it("uses singular copy for a single new fact", () => {
    mockDelta({ marked_at: "2026-06-01T00:00:00Z", items: [fact("f1", "identity")] });
    render();
    expect(
      container.querySelector('[data-testid="delta-banner"]')?.textContent,
    ).toContain("1 new fact ");
  });
});
