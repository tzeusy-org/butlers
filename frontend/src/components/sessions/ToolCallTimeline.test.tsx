// @vitest-environment jsdom

import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";

import { ToolCallTimeline } from "./ToolCallTimeline";

describe("ToolCallTimeline outcome glyph", () => {
  it("uses its outcome as an accessible name without a duplicate native title", () => {
    const html = renderToStaticMarkup(
      <ToolCallTimeline toolCalls={[{ name: "calendar_create", status: "success" }]} />,
    );

    expect(html).toContain('aria-label="Tool call outcome: Success"');
    expect(html).not.toContain('title="Tool call outcome: Success"');
  });
});
