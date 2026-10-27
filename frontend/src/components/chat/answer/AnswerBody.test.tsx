// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { AnswerBody } from "./AnswerBody";

afterEach(cleanup);

describe("AnswerBody", () => {
  it("renders the allowed answer vocabulary with Dispatch type roles", () => {
    const { container } = render(
      <AnswerBody
        content={[
          "## Budget summary",
          "",
          "A *steady* month with `three` changes.",
          "",
          "- Groceries",
          "- Transit",
          "",
          "> Review the outlier.",
          "",
          "| Month | Spend |",
          "| --- | ---: |",
          "| May | 312 |",
        ].join("\n")}
      />,
    );

    expect(screen.getByRole("heading", { name: "Budget summary" })).toBeTruthy();
    expect(screen.getByRole("list").children).toHaveLength(2);
    expect(container.querySelector("em")?.textContent).toBe("steady");
    expect(container.querySelector("code")?.textContent).toBe("three");
    expect(container.querySelector("blockquote")?.textContent).toContain("Review the outlier");
    expect(container.querySelector("[data-answer-prose]")?.className).toContain("font-serif");
    expect(container.querySelector("[data-answer-table-region]")?.className).toContain(
      "overflow-x-auto",
    );
    expect(container.querySelector("td:last-child")?.className).toContain("tabular-nums");
  });

  it("strips raw executable markup, images, and unsafe markdown links", () => {
    const { container } = render(
      <AnswerBody
        content={[
          "<script>window.pwned = true</script>",
          '<img src=x onerror="window.pwned = true">',
          "![tracking pixel](https://example.com/pixel.png)",
          "[unsafe](javascript:alert(1))",
        ].join("\n")}
      />,
    );

    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector('a[href^="javascript"]')).toBeNull();
  });

  it("renders a half-open fence as local code and re-renders cleanly when completed", () => {
    const partial = "```ts\nconst answer = 42";
    const { container, rerender } = render(<AnswerBody content={partial} />);

    const region = screen.getByRole("region", { name: "Code block" });
    expect(region.className).toContain("overflow-x-auto");
    expect(region.getAttribute("tabindex")).toBe("0");
    expect(region.textContent).toContain("const answer = 42");

    rerender(<AnswerBody content={`${partial}\n\`\`\``} />);
    expect(container.querySelectorAll("pre")).toHaveLength(1);
    expect(container.textContent?.match(/const answer = 42/g)).toHaveLength(1);
  });

  it("contains wide answer content inside a min-width-safe message boundary", () => {
    const { container } = render(
      <AnswerBody content={`| Value |\n| --- |\n| ${"9".repeat(180)} |`} />,
    );
    expect(container.firstElementChild?.className).toContain("min-w-0");
    expect(container.firstElementChild?.className).toContain("max-w-full");
    expect(screen.getByRole("region", { name: "Answer table" })).toBeTruthy();
  });
});
