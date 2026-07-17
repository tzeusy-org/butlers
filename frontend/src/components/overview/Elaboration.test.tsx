// @vitest-environment jsdom

import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { Elaboration } from "./Elaboration";

afterEach(cleanup);

describe("Elaboration", () => {
  it("uses the shared fetching dim while the briefing refreshes", () => {
    const { container, getByText } = render(
      <Elaboration text="The system is operating normally." isFetching />,
    );

    const wrapper = container.firstElementChild as HTMLElement;
    const paragraph = getByText("The system is operating normally.");

    expect(wrapper.getAttribute("aria-busy")).toBe("true");
    expect(wrapper.className).toContain("opacity-60");
    expect(paragraph.parentElement).toBe(wrapper);
    expect((paragraph as HTMLElement).style.opacity).toBe("");
  });

  it("keeps the Voice paragraph undimmed when the briefing is settled", () => {
    const { container } = render(
      <Elaboration text="The system is operating normally." isFetching={false} />,
    );

    const wrapper = container.firstElementChild as HTMLElement;

    expect(wrapper.getAttribute("aria-busy")).toBe("false");
    expect(wrapper.className).not.toContain("opacity-60");
  });
});
