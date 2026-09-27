import { describe, expect, it } from "vitest";

import { prepareStreamingMarkdown, safeMarkdownUrl } from "./markdown";

describe("streaming markdown preparation", () => {
  it("closes only a half-open fence in the render copy", () => {
    const partial = "Before\n\n```ts\nconst answer = 42";
    expect(prepareStreamingMarkdown(partial)).toBe(`${partial}\n\n\`\`\``);
    expect(prepareStreamingMarkdown(`${partial}\n\`\`\``)).toBe(`${partial}\n\`\`\``);
  });

  it("allows https links and rejects image, script, data, and relative targets", () => {
    expect(safeMarkdownUrl("https://example.com/reference")).toBe(
      "https://example.com/reference",
    );
    expect(safeMarkdownUrl("javascript:alert(1)")).toBe("");
    expect(safeMarkdownUrl("data:text/html,boom")).toBe("");
    expect(safeMarkdownUrl("/spend")).toBe("");
  });
});
