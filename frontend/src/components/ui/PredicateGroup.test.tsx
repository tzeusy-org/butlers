// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// PredicateGroup tests — bu-pcv1w
//
// Coverage:
//   - Renders the predicate label (hyphen → space replacement)
//   - Renders entry count in parentheses
//   - Renders one NeighbourRow per entry
//   - Click on a neighbour propagates to onSelect with entity_id
//   - Default testId format: predicate-group-{predicate} (Hop view)
//   - Column testId format: column-predicate-group-{columnIndex}-{predicate}
//   - Row testId: "neighbour-row" when no columnIndex
//   - Row testId: "column-neighbour-row-{columnIndex}" when columnIndex is set
//   - data-column-index forwarded to NeighbourRow when columnIndex provided
// ---------------------------------------------------------------------------

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { act } from "react";
import { createRoot } from "react-dom/client";

import type { NeighbourEntry } from "@/api/types";
import { PredicateGroup } from "./PredicateGroup";

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const ALICE: NeighbourEntry = {
  entity_id: "ent-alice-001",
  canonical_name: "Alice",
  direction: "forward",
  src: "relationship",
  conf: 1.0,
  last_seen: null,
  weight: null,
  verified: false,
  primary: null,
};

const BOB: NeighbourEntry = {
  entity_id: "ent-bob-002",
  canonical_name: "Bob",
  direction: "reverse",
  src: "relationship",
  conf: 0.9,
  last_seen: null,
  weight: null,
  verified: false,
  primary: null,
};

// ---------------------------------------------------------------------------
// Rendering — label
// ---------------------------------------------------------------------------

describe("PredicateGroup: predicate label", () => {
  it("renders the predicate with hyphens replaced by spaces", () => {
    const html = renderToStaticMarkup(
      <PredicateGroup predicate="family-of" entries={[ALICE]} onSelect={() => {}} />,
    );
    expect(html).toContain("family of");
  });

  it("renders a predicate with no hyphens unchanged", () => {
    const html = renderToStaticMarkup(
      <PredicateGroup predicate="knows" entries={[ALICE]} onSelect={() => {}} />,
    );
    expect(html).toContain("knows");
  });

  it("renders the entry count in parentheses", () => {
    const html = renderToStaticMarkup(
      <PredicateGroup predicate="knows" entries={[ALICE, BOB]} onSelect={() => {}} />,
    );
    expect(html).toContain("(2)");
  });
});

// ---------------------------------------------------------------------------
// Rendering — entries
// ---------------------------------------------------------------------------

describe("PredicateGroup: entries", () => {
  it("renders one row per entry", () => {
    const html = renderToStaticMarkup(
      <PredicateGroup predicate="knows" entries={[ALICE, BOB]} onSelect={() => {}} />,
    );
    expect(html).toContain("Alice");
    expect(html).toContain("Bob");
  });

  it("renders zero rows when entries is empty", () => {
    const html = renderToStaticMarkup(
      <PredicateGroup predicate="knows" entries={[]} onSelect={() => {}} />,
    );
    // Section still renders (for the label), but no entity names
    expect(html).not.toContain("Alice");
    expect(html).not.toContain("Bob");
    expect(html).toContain("(0)");
  });
});

// ---------------------------------------------------------------------------
// Ranked truncation — remainder "+N more" affordance
// ---------------------------------------------------------------------------

describe("PredicateGroup: remainder (+N more)", () => {
  it("renders +N more when remainder > 0", () => {
    const html = renderToStaticMarkup(
      <PredicateGroup predicate="knows" entries={[ALICE, BOB]} remainder={34} onSelect={() => {}} />,
    );
    expect(html).toContain("+34 more");
    expect(html).toContain('data-testid="predicate-more-knows"');
  });

  it("includes the remainder in the parenthesized group count", () => {
    const html = renderToStaticMarkup(
      <PredicateGroup predicate="knows" entries={[ALICE, BOB]} remainder={34} onSelect={() => {}} />,
    );
    // 2 returned + 34 truncated = 36 total in the group.
    expect(html).toContain("(36)");
  });

  it("does not render the affordance when remainder is 0", () => {
    const html = renderToStaticMarkup(
      <PredicateGroup predicate="knows" entries={[ALICE, BOB]} remainder={0} onSelect={() => {}} />,
    );
    expect(html).not.toContain("more");
    expect(html).not.toContain("predicate-more-knows");
    expect(html).toContain("(2)");
  });

  it("does not render the affordance when remainder is undefined", () => {
    const html = renderToStaticMarkup(
      <PredicateGroup predicate="knows" entries={[ALICE, BOB]} onSelect={() => {}} />,
    );
    expect(html).not.toContain("predicate-more-knows");
    expect(html).toContain("(2)");
  });

  it("uses the Columns-view testId for the remainder when columnIndex is set", () => {
    const html = renderToStaticMarkup(
      <PredicateGroup
        predicate="knows"
        entries={[ALICE]}
        remainder={5}
        columnIndex={2}
        onSelect={() => {}}
      />,
    );
    expect(html).toContain('data-testid="column-predicate-more-2-knows"');
    expect(html).toContain("+5 more");
  });
});

// ---------------------------------------------------------------------------
// Test IDs — Hop view (no columnIndex)
// ---------------------------------------------------------------------------

describe("PredicateGroup: testId — Hop view (no columnIndex)", () => {
  it("uses predicate-group-{predicate} as section testId", () => {
    const html = renderToStaticMarkup(
      <PredicateGroup predicate="knows" entries={[ALICE]} onSelect={() => {}} />,
    );
    expect(html).toContain('data-testid="predicate-group-knows"');
  });

  it("uses predicate-group-{predicate} for hyphenated predicates", () => {
    const html = renderToStaticMarkup(
      <PredicateGroup predicate="family-of" entries={[ALICE]} onSelect={() => {}} />,
    );
    expect(html).toContain('data-testid="predicate-group-family-of"');
  });

  it("uses neighbour-row as row testId", () => {
    const html = renderToStaticMarkup(
      <PredicateGroup predicate="knows" entries={[ALICE]} onSelect={() => {}} />,
    );
    expect(html).toContain('data-testid="neighbour-row"');
  });

  it("does not add data-column-index to rows", () => {
    const html = renderToStaticMarkup(
      <PredicateGroup predicate="knows" entries={[ALICE]} onSelect={() => {}} />,
    );
    expect(html).not.toContain("data-column-index");
  });
});

// ---------------------------------------------------------------------------
// Test IDs — Columns view (with columnIndex)
// ---------------------------------------------------------------------------

describe("PredicateGroup: testId — Columns view (columnIndex provided)", () => {
  it("uses column-predicate-group-{columnIndex}-{predicate} as section testId", () => {
    const html = renderToStaticMarkup(
      <PredicateGroup predicate="knows" entries={[ALICE]} columnIndex={0} onSelect={() => {}} />,
    );
    expect(html).toContain('data-testid="column-predicate-group-0-knows"');
  });

  it("uses correct column index in section testId", () => {
    const html = renderToStaticMarkup(
      <PredicateGroup
        predicate="family-of"
        entries={[ALICE]}
        columnIndex={2}
        onSelect={() => {}}
      />,
    );
    expect(html).toContain('data-testid="column-predicate-group-2-family-of"');
  });

  it("uses column-neighbour-row-{columnIndex} as row testId", () => {
    const html = renderToStaticMarkup(
      <PredicateGroup predicate="knows" entries={[ALICE]} columnIndex={1} onSelect={() => {}} />,
    );
    expect(html).toContain('data-testid="column-neighbour-row-1"');
  });

  it("forwards data-column-index to rows", () => {
    const html = renderToStaticMarkup(
      <PredicateGroup predicate="knows" entries={[ALICE]} columnIndex={0} onSelect={() => {}} />,
    );
    expect(html).toContain('data-column-index="0"');
  });
});

// ---------------------------------------------------------------------------
// Accessible labels — getRowAriaLabel
// ---------------------------------------------------------------------------

describe("PredicateGroup: getRowAriaLabel", () => {
  it("forwards the per-entry aria label to each NeighbourRow button", () => {
    const html = renderToStaticMarkup(
      <PredicateGroup
        predicate="knows"
        entries={[ALICE]}
        onSelect={() => {}}
        getRowAriaLabel={(entry) => `Re-centre on entity ${entry.canonical_name || entry.entity_id}`}
      />,
    );
    expect(html).toContain('aria-label="Re-centre on entity Alice"');
  });

  it("falls back to NeighbourRow default when getRowAriaLabel is absent", () => {
    const html = renderToStaticMarkup(
      <PredicateGroup predicate="knows" entries={[ALICE]} onSelect={() => {}} />,
    );
    expect(html).toContain('aria-label="Select entity Alice"');
  });
});

// ---------------------------------------------------------------------------
// Click propagation
// ---------------------------------------------------------------------------

describe("PredicateGroup: click propagation", () => {
  it("calls onSelect with entity_id when a neighbour button is clicked", async () => {
    const handleSelect = vi.fn();
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);

    await act(async () => {
      root.render(
        <PredicateGroup predicate="knows" entries={[ALICE]} onSelect={handleSelect} />,
      );
    });

    const btn = container.querySelector("button") as HTMLButtonElement;
    await act(async () => {
      btn.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(handleSelect).toHaveBeenCalledWith("ent-alice-001");
    expect(handleSelect).toHaveBeenCalledTimes(1);

    act(() => {
      root.unmount();
    });
    container.remove();
  });

  it("calls onSelect once per click (not for sibling rows)", async () => {
    const handleSelect = vi.fn();
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);

    await act(async () => {
      root.render(
        <PredicateGroup
          predicate="knows"
          entries={[ALICE, BOB]}
          onSelect={handleSelect}
        />,
      );
    });

    // Click only Alice's button (first button)
    const buttons = container.querySelectorAll("button") as NodeListOf<HTMLButtonElement>;
    await act(async () => {
      buttons[0].dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(handleSelect).toHaveBeenCalledTimes(1);
    expect(handleSelect).toHaveBeenCalledWith("ent-alice-001");

    act(() => {
      root.unmount();
    });
    container.remove();
  });
});
