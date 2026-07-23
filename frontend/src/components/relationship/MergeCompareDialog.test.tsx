// @vitest-environment jsdom
/**
 * Component tests for MergeCompareDialog — the single-pair merge-review compare
 * view (relationship-merge-review, bu-b2qg8).
 *
 * Covers:
 * - opening the dialog POSTs compare and renders a/b blocks + shared/divergent;
 * - the merge action requires an accessible final confirmation before POSTing /merge;
 * - the dismiss-pair action POSTs dismiss-pair;
 * - commit buttons are disabled until the compare diff has rendered
 *   (no merge bypasses the compare view).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { toast } from "sonner";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

vi.mock("@/hooks/use-entities", () => ({
  useCompareEntities: vi.fn(),
  useDismissEntityPair: vi.fn(),
  useMergeRelationshipEntities: vi.fn(),
}));

import {
  useCompareEntities,
  useDismissEntityPair,
  useMergeRelationshipEntities,
} from "@/hooks/use-entities";
import type { CompareEntitiesResponse } from "@/api/types";
import { MergeCompareDialog } from "./MergeCompareDialog";

const DIFF: CompareEntitiesResponse = {
  a: {
    entity: {
      id: "a1",
      canonical_name: "Alice One",
      entity_type: "person",
      aliases: ["Al"],
      tier: null,
      state: "active",
    },
    identity_facts: [
      {
        id: "fa1",
        entity_id: "a1",
        predicate: "has-email",
        object: "alice@x.com",
        object_kind: "literal",
        store: "identity",
        src: "telegram",
        conf: 1,
        verified: true,
        staleness_band: "fresh",
      },
    ],
    narrative_facts: [],
  },
  b: {
    entity: {
      id: "b1",
      canonical_name: "Alice Two",
      entity_type: "person",
      aliases: [],
      tier: 5,
      state: "active",
    },
    identity_facts: [],
    narrative_facts: [],
  },
  shared: [
    {
      id: "fa1",
      entity_id: "a1",
      predicate: "has-email",
      object: "alice@x.com",
      object_kind: "literal",
      store: "identity",
      src: "telegram",
      conf: 1,
      verified: true,
      staleness_band: "fresh",
    },
  ],
  divergent: [
    {
      id: "fa2",
      entity_id: "a1",
      predicate: "has-birthday",
      object: "1990-06-15",
      object_kind: "literal",
      store: "identity",
      src: "telegram",
      conf: 1,
      verified: true,
      staleness_band: "fresh",
    },
  ],
};

let container: HTMLDivElement;
let root: Root;
let compareMutate: ReturnType<typeof vi.fn>;
let mergeMutate: ReturnType<typeof vi.fn>;
let dismissMutate: ReturnType<typeof vi.fn>;

function setup() {
  compareMutate = vi.fn().mockResolvedValue(DIFF);
  mergeMutate = vi.fn().mockResolvedValue({
    kept_entity_id: "a1",
    tombstoned_entity_id: "b1",
    subject_facts_rewired: 0,
    object_facts_rewired: 0,
  });
  dismissMutate = vi.fn().mockResolvedValue({
    review_id: "r1",
    entity_a: "a1",
    entity_b: "b1",
    outcome: "dismissed",
    shared_facts: [],
  });

  vi.mocked(useCompareEntities).mockReturnValue({
    mutateAsync: compareMutate,
    reset: vi.fn(),
    isPending: false,
  } as unknown as ReturnType<typeof useCompareEntities>);
  vi.mocked(useMergeRelationshipEntities).mockReturnValue({
    mutateAsync: mergeMutate,
    isPending: false,
  } as unknown as ReturnType<typeof useMergeRelationshipEntities>);
  vi.mocked(useDismissEntityPair).mockReturnValue({
    mutateAsync: dismissMutate,
    isPending: false,
  } as unknown as ReturnType<typeof useDismissEntityPair>);
}

async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

beforeEach(() => {
  setup();
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.clearAllMocks();
});

describe("MergeCompareDialog", () => {
  it("POSTs compare on open and renders a/b blocks + shared + divergent", async () => {
    await act(async () => {
      root.render(
        <MergeCompareDialog pair={{ entityA: "a1", entityB: "b1" }} onOpenChange={() => {}} />,
      );
    });
    await flush();

    expect(compareMutate.mock.calls[0][0]).toEqual({ entity_a: "a1", entity_b: "b1" });

    const text = document.body.textContent ?? "";
    expect(text).toContain("Alice One");
    expect(text).toContain("Alice Two");
    expect(document.querySelector('[data-testid="compare-shared"]')).not.toBeNull();
    expect(document.querySelector('[data-testid="compare-divergent"]')).not.toBeNull();
    // Shared evidence shows the matched email; divergence shows the birthday conflict.
    expect(text).toContain("alice@x.com");
    expect(text).toContain("has birthday");
  });

  it("requires an accessible final confirmation before merging with the chosen survivor", async () => {
    await act(async () => {
      root.render(
        <MergeCompareDialog pair={{ entityA: "a1", entityB: "b1" }} onOpenChange={() => {}} />,
      );
    });
    await flush();

    // Select column B as the survivor.
    const radioB = document.querySelector(
      '[data-testid="compare-column-B"] input[type="radio"]',
    ) as HTMLInputElement;
    await act(async () => {
      radioB.click();
    });

    const mergeBtn = document.querySelector('[data-testid="compare-merge"]') as HTMLButtonElement;
    await act(async () => {
      mergeBtn.click();
    });
    await flush();

    expect(mergeMutate).not.toHaveBeenCalled();
    const confirmation = document.querySelector(
      '[data-testid="merge-confirmation"]',
    ) as HTMLElement;
    expect(confirmation).not.toBeNull();
    expect(confirmation.getAttribute("role")).toBe("alertdialog");
    expect(confirmation.getAttribute("aria-labelledby")).toBeTruthy();
    expect(confirmation.getAttribute("aria-describedby")).toBeTruthy();
    expect(confirmation.textContent).toContain("Alice Two");
    expect(confirmation.textContent).toContain("Alice One");

    const confirmBtn = document.querySelector(
      '[data-testid="merge-confirm"]',
    ) as HTMLButtonElement;
    await act(async () => {
      confirmBtn.click();
    });
    await flush();

    expect(mergeMutate).toHaveBeenCalledWith({ entityA: "a1", entityB: "b1", keepAs: "B" });
  });

  it("cancels the final confirmation without requesting a merge", async () => {
    await act(async () => {
      root.render(
        <MergeCompareDialog pair={{ entityA: "a1", entityB: "b1" }} onOpenChange={() => {}} />,
      );
    });
    await flush();

    const mergeBtn = document.querySelector('[data-testid="compare-merge"]') as HTMLButtonElement;
    await act(async () => {
      mergeBtn.click();
    });
    await flush();

    const cancelBtn = document.querySelector(
      '[data-testid="merge-confirm-cancel"]',
    ) as HTMLButtonElement;
    await act(async () => {
      cancelBtn.click();
    });
    await flush();

    expect(mergeMutate).not.toHaveBeenCalled();
    expect(document.querySelector('[data-testid="merge-confirmation"]')).toBeNull();
  });

  it("Escape cancels the final confirmation without requesting a merge", async () => {
    await act(async () => {
      root.render(
        <MergeCompareDialog pair={{ entityA: "a1", entityB: "b1" }} onOpenChange={() => {}} />,
      );
    });
    await flush();

    const mergeBtn = document.querySelector('[data-testid="compare-merge"]') as HTMLButtonElement;
    await act(async () => {
      mergeBtn.click();
    });
    await flush();

    const confirmation = document.querySelector(
      '[data-testid="merge-confirmation"]',
    ) as HTMLElement;
    await act(async () => {
      confirmation.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    });
    await flush();

    expect(mergeMutate).not.toHaveBeenCalled();
    expect(document.querySelector('[data-testid="merge-confirmation"]')).toBeNull();
  });

  it("retains merge error handling after confirmation", async () => {
    mergeMutate.mockRejectedValueOnce(new Error("merge service unavailable"));
    await act(async () => {
      root.render(
        <MergeCompareDialog pair={{ entityA: "a1", entityB: "b1" }} onOpenChange={() => {}} />,
      );
    });
    await flush();

    const mergeBtn = document.querySelector('[data-testid="compare-merge"]') as HTMLButtonElement;
    await act(async () => {
      mergeBtn.click();
    });
    await flush();

    const confirmBtn = document.querySelector(
      '[data-testid="merge-confirm"]',
    ) as HTMLButtonElement;
    await act(async () => {
      confirmBtn.click();
    });
    await flush();

    expect(mergeMutate).toHaveBeenCalledWith({ entityA: "a1", entityB: "b1", keepAs: "A" });
    expect(toast.error).toHaveBeenCalledWith("Merge failed: merge service unavailable");
  });

  it("dismiss action POSTs dismiss-pair", async () => {
    await act(async () => {
      root.render(
        <MergeCompareDialog pair={{ entityA: "a1", entityB: "b1" }} onOpenChange={() => {}} />,
      );
    });
    await flush();

    const dismissBtn = document.querySelector(
      '[data-testid="compare-dismiss"]',
    ) as HTMLButtonElement;
    await act(async () => {
      dismissBtn.click();
    });
    await flush();

    expect(dismissMutate).toHaveBeenCalledWith({ entity_a: "a1", entity_b: "b1" });
  });

  it("pre-highlights the triggering shared-evidence row", async () => {
    await act(async () => {
      root.render(
        <MergeCompareDialog
          pair={{ entityA: "a1", entityB: "b1" }}
          onOpenChange={() => {}}
          highlightFact={{ predicate: "has-email", object: "alice@x.com" }}
        />,
      );
    });
    await flush();

    const shared = document.querySelector('[data-testid="compare-shared"]');
    const highlighted = shared?.querySelector('[data-highlighted="true"]');
    expect(highlighted).not.toBeNull();
    expect(highlighted?.textContent).toContain("alice@x.com");
  });

  it("does not highlight any row when no highlightFact is supplied", async () => {
    await act(async () => {
      root.render(
        <MergeCompareDialog pair={{ entityA: "a1", entityB: "b1" }} onOpenChange={() => {}} />,
      );
    });
    await flush();

    expect(document.querySelector('[data-highlighted="true"]')).toBeNull();
  });

  it("renders shared/divergent counts as tabular numerals", async () => {
    await act(async () => {
      root.render(
        <MergeCompareDialog pair={{ entityA: "a1", entityB: "b1" }} onOpenChange={() => {}} />,
      );
    });
    await flush();

    const shared = document.querySelector('[data-testid="compare-shared"]');
    const count = shared?.querySelector(".tabular-nums");
    expect(count).not.toBeNull();
    expect(count?.textContent).toBe("1");
  });

  it("disables commit actions until the diff has rendered", async () => {
    // Compare never resolves → buttons stay disabled (no merge bypasses review).
    compareMutate.mockReturnValue(new Promise(() => {}));
    vi.mocked(useCompareEntities).mockReturnValue({
      mutateAsync: compareMutate,
      reset: vi.fn(),
      isPending: true,
    } as unknown as ReturnType<typeof useCompareEntities>);

    await act(async () => {
      root.render(
        <MergeCompareDialog pair={{ entityA: "a1", entityB: "b1" }} onOpenChange={() => {}} />,
      );
    });
    await flush();

    const mergeBtn = document.querySelector('[data-testid="compare-merge"]') as HTMLButtonElement;
    const dismissBtn = document.querySelector(
      '[data-testid="compare-dismiss"]',
    ) as HTMLButtonElement;
    expect(mergeBtn.disabled).toBe(true);
    expect(dismissBtn.disabled).toBe(true);
  });
});
