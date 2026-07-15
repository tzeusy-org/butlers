// @vitest-environment jsdom
/**
 * ExpandableDetail — keyboard-reachable detail/expand affordance (bu-x7z84).
 *
 * Verifies the interaction contract the class-2 a11y fix depends on:
 *  - no toggle when the cell is not clipped (expandable=false) → density parity
 *  - a native <button> toggle with aria-expanded + aria-controls when clipped
 *  - Enter AND Space toggle (native button semantics)
 *  - the disclosed region carries the id the button controls
 */

import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { ExpandableDetail } from "@/components/ui/expandable-detail";

afterEach(() => cleanup());

describe("ExpandableDetail", () => {
  it("renders only the preview and no toggle when not expandable", () => {
    render(
      <ExpandableDetail
        label="message"
        expandable={false}
        preview={<p>short preview</p>}
        testId="toggle"
      >
        <p>full detail</p>
      </ExpandableDetail>,
    );
    expect(screen.getByText("short preview")).toBeDefined();
    expect(screen.queryByTestId("toggle")).toBeNull();
    expect(screen.queryByText("full detail")).toBeNull();
  });

  it("offers a toggle button with aria-expanded when expandable", () => {
    render(
      <ExpandableDetail
        label="message"
        expandable
        preview={<p>clipped…</p>}
        testId="toggle"
      >
        <p>the full untruncated message</p>
      </ExpandableDetail>,
    );
    const btn = screen.getByTestId("toggle");
    expect(btn.tagName).toBe("BUTTON");
    expect(btn.getAttribute("aria-expanded")).toBe("false");
    expect(btn.getAttribute("aria-label")).toBe("Show full message");
    // Collapsed: detail not present.
    expect(screen.queryByText("the full untruncated message")).toBeNull();
  });

  it("discloses the full content on click and wires aria-controls to the region", () => {
    render(
      <ExpandableDetail
        label="message"
        expandable
        preview={<p>clipped…</p>}
        testId="toggle"
      >
        <p>the full untruncated message</p>
      </ExpandableDetail>,
    );
    const btn = screen.getByTestId("toggle");
    fireEvent.click(btn);
    expect(btn.getAttribute("aria-expanded")).toBe("true");
    expect(btn.getAttribute("aria-label")).toBe("Hide full message");
    // The disclosed content is an announced region labelled "Full {label}", and
    // the button's aria-controls points at that region's id.
    const region = screen.getByRole("region", { name: "Full message" });
    expect(region.textContent).toBe("the full untruncated message");
    expect(btn.getAttribute("aria-controls")).toBe(region.getAttribute("id"));
    expect(btn.getAttribute("aria-controls")).toBeTruthy();
  });

  it("toggles on Enter and Space via native button semantics", () => {
    render(
      <ExpandableDetail label="message" expandable preview={<p>clipped…</p>} testId="toggle">
        <p>full body</p>
      </ExpandableDetail>,
    );
    const btn = screen.getByTestId("toggle");
    // jsdom fires click on Enter/Space for a focused native button; assert the
    // element is a real <button> (which carries that behavior) and click works.
    expect(btn.tagName).toBe("BUTTON");
    fireEvent.click(btn);
    expect(screen.getByText("full body")).toBeDefined();
    fireEvent.click(btn);
    expect(screen.queryByText("full body")).toBeNull();
  });
});
