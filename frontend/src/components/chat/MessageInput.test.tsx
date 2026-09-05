// @vitest-environment jsdom
/**
 * MessageInput — ContextChip wiring (bu-0ynlk.4).
 *
 * Covers: the composing textarea's `aria-describedby` points at the
 * rendered ContextChip's id only when a `contextChip` prop is supplied;
 * omitted entirely otherwise.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { MessageInput } from "./MessageInput.tsx";

afterEach(() => cleanup());

const noop = () => undefined;

describe("MessageInput — ContextChip aria wiring", () => {
  it("points aria-describedby at the rendered chip's id when contextChip is supplied", () => {
    render(
      <MessageInput
        value=""
        onChange={noop}
        onSend={noop}
        onStop={noop}
        disabled={false}
        isStreaming={false}
        contextChip={{
          label: "Overview",
          policy: "snapshot",
          payload: { route: "/" },
          included: true,
          onToggleIncluded: vi.fn(),
        }}
      />,
    );

    const textarea = screen.getByPlaceholderText("Type a message...");
    const describedBy = textarea.getAttribute("aria-describedby");
    expect(describedBy).toBeTruthy();
    expect(document.getElementById(describedBy!)).toBe(screen.getByTestId("context-chip"));
  });

  it("omits aria-describedby and renders no chip when contextChip is not supplied", () => {
    render(
      <MessageInput
        value=""
        onChange={noop}
        onSend={noop}
        onStop={noop}
        disabled={false}
        isStreaming={false}
      />,
    );

    expect(screen.getByPlaceholderText("Type a message...").getAttribute("aria-describedby")).toBeNull();
    expect(screen.queryByTestId("context-chip")).toBeNull();
  });
});
