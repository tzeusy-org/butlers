// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { SendErrorBanner } from "./send-error.tsx";

afterEach(() => cleanup());

describe.each([
  {
    error: {
      kind: "timeout" as const,
      message: "No reply yet.",
      failedText: "hello",
      messageId: "message-1",
      sessionId: null,
    },
  },
  {
    error: {
      kind: "ambiguous" as const,
      message: "This request may still have completed.",
      failedText: "hello",
      messageId: "message-1",
    },
  },
  {
    error: {
      kind: "pending" as const,
      message: "This message is already being submitted.",
      failedText: "hello",
      messageId: "message-1",
    },
  },
])("SendErrorBanner — terminal uncertainty", ({ error }) => {
  it("announces the $error.kind state as an atomic alert", () => {
    render(
      <SendErrorBanner
        error={error}
        onRetry={vi.fn()}
        onCheckAgain={vi.fn()}
        onDismiss={vi.fn()}
      />,
    );

    const alert = screen.getByRole("alert");
    expect(alert.getAttribute("aria-atomic")).toBe("true");
    expect(alert.textContent).toContain(error.message);
  });
});
