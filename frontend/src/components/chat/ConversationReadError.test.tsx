// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ConversationReadError } from "./ConversationReadError.tsx";

afterEach(() => cleanup());

describe.each([
  { compact: false, buttonName: "Try again" },
  { compact: true, buttonName: "Could not load conversations. Try again." },
])("ConversationReadError — iconography", ({ compact, buttonName }) => {
  it(`uses the dashboard icon stroke in ${compact ? "compact" : "expanded"} mode`, () => {
    render(
      <ConversationReadError
        label="conversations"
        onRetry={vi.fn()}
        compact={compact}
      />,
    );

    const icon = screen.getByRole("button", { name: buttonName }).querySelector("svg");
    expect(icon?.getAttribute("stroke-width")).toBe("1.5");
  });
});
