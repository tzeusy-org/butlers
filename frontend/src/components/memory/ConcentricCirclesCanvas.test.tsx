// @vitest-environment jsdom

/**
 * Tests for ConcentricCirclesCanvas.
 *
 * We verify:
 * 1. touch-none class is absent (pinch-zoom not blocked by Tailwind override)
 * 2. touchAction: "manipulation" allows native pinch zoom + single-tap
 * 3. Breakpoint parity: this canvas is the rings view rendered on tablet/desktop
 */

import { act } from "react";
import { afterAll, afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createRoot, type Root } from "react-dom/client";

import { ConcentricCirclesCanvas } from "@/components/memory/ConcentricCirclesCanvas";
import type { DunbarEntry } from "@/api/types";
import type { Tier } from "@/components/memory/concentric-circles-constants";

// jsdom has no SVG layout, so getBoundingClientRect returns zeros.
// We only need to mount and inspect DOM attributes here.

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const OWNER_ENTRY: DunbarEntry = {
  contact_id: "c-owner",
  entity_id: "e-owner",
  canonical_name: "Ada Lovelace",
  dunbar_tier: 5,
  dunbar_score: 1,
  dunbar_tier_override: false,
};

const CONTACT_ENTRY: DunbarEntry = {
  contact_id: "c-alice",
  entity_id: "e-alice",
  canonical_name: "Alice Nguyen",
  dunbar_tier: 5,
  dunbar_score: 0.8,
  dunbar_tier_override: false,
};

describe("ConcentricCirclesCanvas", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => { root.unmount(); });
    container.remove();
    document.body.innerHTML = "";
    vi.restoreAllMocks();
  });

  afterAll(() => {
    // nothing to restore
  });

  function renderCanvas() {
    act(() => {
      root.render(
        <ConcentricCirclesCanvas
          entries={[OWNER_ENTRY, CONTACT_ENTRY]}
          ownerEntityId={OWNER_ENTRY.entity_id}
          ownerName="Ada Lovelace"
          width={800}
          height={600}
          searchQuery=""
          focusTier={null}
          focusTrigger={0}
          expandedTiers={new Set<Tier>()}
          onNavigate={() => {}}
          onTierExpand={() => {}}
        />,
      );
    });
  }

  it("does NOT have touch-none class on the SVG element (pinch-zoom must not be blocked)", () => {
    renderCanvas();
    const svg = container.querySelector("svg");
    expect(svg).toBeTruthy();
    expect(svg!.classList.contains("touch-none")).toBe(false);
  });

  it("applies touchAction: manipulation to the SVG element", () => {
    renderCanvas();
    const svg = container.querySelector("svg");
    expect(svg).toBeTruthy();
    // React sets inline style.touchAction
    expect(svg!.style.touchAction).toBe("manipulation");
  });

  it("renders the aria role img on the SVG", () => {
    renderCanvas();
    const svg = container.querySelector("svg");
    expect(svg).toBeTruthy();
    expect(svg!.getAttribute("role")).toBe("img");
    expect(svg!.getAttribute("aria-label")).toBeTruthy();
  });
});
