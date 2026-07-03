// @vitest-environment jsdom

/**
 * Unit tests for FetchingDim (bu-86c4c.13, JARVIS audit move 10 — never-blank
 * lists). Pairs with placeholderData:(prev)=>prev on the list query: while a
 * background refetch is in flight the stale rows dim instead of the list
 * blanking to a skeleton.
 */

import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { FetchingDim } from "./fetching-dim";

describe("FetchingDim", () => {
  it("does not dim when isFetching is false", () => {
    const { container } = render(
      <FetchingDim isFetching={false}>
        <p>rows</p>
      </FetchingDim>,
    );
    const wrapper = container.firstElementChild as HTMLElement;
    expect(wrapper.className).not.toContain("opacity-60");
    expect(wrapper.getAttribute("aria-busy")).toBe("false");
  });

  it("dims and marks aria-busy when isFetching is true", () => {
    const { container } = render(
      <FetchingDim isFetching={true}>
        <p>rows</p>
      </FetchingDim>,
    );
    const wrapper = container.firstElementChild as HTMLElement;
    expect(wrapper.className).toContain("opacity-60");
    expect(wrapper.getAttribute("aria-busy")).toBe("true");
  });

  it("still renders children regardless of fetching state", () => {
    const { getByText } = render(
      <FetchingDim isFetching={true}>
        <p>stale-but-visible rows</p>
      </FetchingDim>,
    );
    expect(getByText("stale-but-visible rows")).toBeDefined();
  });

  it("merges a caller-provided className", () => {
    const { container } = render(
      <FetchingDim isFetching={false} className="custom-class">
        <p>rows</p>
      </FetchingDim>,
    );
    const wrapper = container.firstElementChild as HTMLElement;
    expect(wrapper.className).toContain("custom-class");
  });
});
