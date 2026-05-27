// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// NeighbourRow tests — bu-ah35h
//
// Coverage:
//   - Renders entity name (canonical_name)
//   - Falls back to entity_id when canonical_name is empty
//   - Renders as an <li> element
//   - Name button has data-entity-id attribute
//   - Calls onClick with entity_id when button is clicked
//   - Weight shown when entry.weight != null
//   - Weight hidden when entry.weight is null
//   - Direction badge shows "→" for forward
//   - Direction badge shows "←" for reverse
//   - data-testid forwarded to <li>
//   - Custom ariaLabel forwarded to button
//   - data-column-index forwarded when provided
// ---------------------------------------------------------------------------

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { act } from "react";
import { createRoot } from "react-dom/client";

import type { NeighbourEntry } from "@/api/types";
import { NeighbourRow } from "./NeighbourRow";

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const BASE_ENTRY: NeighbourEntry = {
  entity_id: "ent-001",
  canonical_name: "Alice",
  direction: "forward",
  src: "relationship",
  conf: 1.0,
  last_seen: null,
  weight: null,
  verified: false,
  primary: null,
};

const ENTRY_WITH_WEIGHT: NeighbourEntry = {
  ...BASE_ENTRY,
  weight: 5,
};

const ENTRY_REVERSE: NeighbourEntry = {
  ...BASE_ENTRY,
  direction: "reverse",
};

const ENTRY_NO_NAME: NeighbourEntry = {
  ...BASE_ENTRY,
  canonical_name: "",
};

// ---------------------------------------------------------------------------
// Renders correctly
// ---------------------------------------------------------------------------

describe("NeighbourRow: rendering", () => {
  it("renders the canonical_name", () => {
    const html = renderToStaticMarkup(
      <NeighbourRow entry={BASE_ENTRY} onClick={() => {}} />,
    );
    expect(html).toContain("Alice");
  });

  it("falls back to entity_id when canonical_name is empty", () => {
    const html = renderToStaticMarkup(
      <NeighbourRow entry={ENTRY_NO_NAME} onClick={() => {}} />,
    );
    expect(html).toContain("ent-001");
  });

  it("renders as an <li> element", () => {
    const html = renderToStaticMarkup(
      <NeighbourRow entry={BASE_ENTRY} onClick={() => {}} />,
    );
    expect(html).toMatch(/^<li /);
  });

  it("button has data-entity-id attribute", () => {
    const html = renderToStaticMarkup(
      <NeighbourRow entry={BASE_ENTRY} onClick={() => {}} />,
    );
    expect(html).toContain('data-entity-id="ent-001"');
  });

  it("renders → badge for forward direction", () => {
    const html = renderToStaticMarkup(
      <NeighbourRow entry={BASE_ENTRY} onClick={() => {}} />,
    );
    expect(html).toContain("→");
  });

  it("renders ← badge for reverse direction", () => {
    const html = renderToStaticMarkup(
      <NeighbourRow entry={ENTRY_REVERSE} onClick={() => {}} />,
    );
    expect(html).toContain("←");
  });

  it("renders weight when entry.weight is set", () => {
    const html = renderToStaticMarkup(
      <NeighbourRow entry={ENTRY_WITH_WEIGHT} onClick={() => {}} />,
    );
    expect(html).toContain("w=5");
  });

  it("does not render weight when entry.weight is null", () => {
    const html = renderToStaticMarkup(
      <NeighbourRow entry={BASE_ENTRY} onClick={() => {}} />,
    );
    expect(html).not.toContain("w=");
  });
});

// ---------------------------------------------------------------------------
// testId forwarding
// ---------------------------------------------------------------------------

describe("NeighbourRow: testId prop", () => {
  it("applies default data-testid='neighbour-row' to <li>", () => {
    const html = renderToStaticMarkup(
      <NeighbourRow entry={BASE_ENTRY} onClick={() => {}} />,
    );
    expect(html).toContain('data-testid="neighbour-row"');
  });

  it("forwards custom testId to <li>", () => {
    const html = renderToStaticMarkup(
      <NeighbourRow entry={BASE_ENTRY} onClick={() => {}} testId="column-neighbour-row-0" />,
    );
    expect(html).toContain('data-testid="column-neighbour-row-0"');
  });
});

// ---------------------------------------------------------------------------
// ariaLabel forwarding
// ---------------------------------------------------------------------------

describe("NeighbourRow: ariaLabel prop", () => {
  it("uses default aria-label when ariaLabel not provided", () => {
    const html = renderToStaticMarkup(
      <NeighbourRow entry={BASE_ENTRY} onClick={() => {}} />,
    );
    expect(html).toContain("Select entity Alice");
  });

  it("forwards custom ariaLabel to button", () => {
    const html = renderToStaticMarkup(
      <NeighbourRow
        entry={BASE_ENTRY}
        onClick={() => {}}
        ariaLabel="Re-centre on entity Alice"
      />,
    );
    expect(html).toContain("Re-centre on entity Alice");
  });
});

// ---------------------------------------------------------------------------
// data-column-index forwarding
// ---------------------------------------------------------------------------

describe("NeighbourRow: data-column-index prop", () => {
  it("does not add data-column-index when not provided", () => {
    const html = renderToStaticMarkup(
      <NeighbourRow entry={BASE_ENTRY} onClick={() => {}} />,
    );
    expect(html).not.toContain("data-column-index");
  });

  it("forwards data-column-index to both <li> and button when provided", () => {
    const html = renderToStaticMarkup(
      <NeighbourRow entry={BASE_ENTRY} onClick={() => {}} data-column-index={2} />,
    );
    // Should appear at least twice: once on <li>, once on <button>
    const matches = html.match(/data-column-index="2"/g);
    expect(matches).toBeTruthy();
    expect(matches!.length).toBeGreaterThanOrEqual(2);
  });

  it("button has data-entity-id and data-column-index for compound selector queries", () => {
    const html = renderToStaticMarkup(
      <NeighbourRow entry={BASE_ENTRY} onClick={() => {}} data-column-index={0} />,
    );
    // The button must have both, enabling compound CSS selector queries
    // like [data-entity-id='ent-001'][data-column-index='0']
    expect(html).toContain('data-entity-id="ent-001"');
    expect(html).toContain('data-column-index="0"');
  });
});

// ---------------------------------------------------------------------------
// Interaction — onClick
// ---------------------------------------------------------------------------

describe("NeighbourRow: onClick interaction", () => {
  it("calls onClick with entity_id when name button is clicked", async () => {
    const handleClick = vi.fn();
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);

    await act(async () => {
      root.render(<NeighbourRow entry={BASE_ENTRY} onClick={handleClick} />);
    });

    const btn = container.querySelector("button") as HTMLButtonElement;
    await act(async () => {
      btn.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(handleClick).toHaveBeenCalledWith("ent-001");
    expect(handleClick).toHaveBeenCalledTimes(1);

    act(() => {
      root.unmount();
    });
    container.remove();
  });
});
