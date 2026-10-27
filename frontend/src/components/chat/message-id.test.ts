// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  chatMessageDeepLink,
  createClientMessageId,
  messageAnchorId,
  scrollToMessageAnchor,
} from "./message-id.ts";

afterEach(() => {
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

describe("createClientMessageId", () => {
  it("uses crypto.randomUUID when available", () => {
    const randomUUID = vi.fn(() => "3d6f0a10-45a1-4dab-a22b-b8a321bd4e4f");
    vi.stubGlobal("crypto", { randomUUID });

    expect(createClientMessageId()).toBe("3d6f0a10-45a1-4dab-a22b-b8a321bd4e4f");
    expect(randomUUID).toHaveBeenCalledOnce();
  });

  it("falls back to a UUIDv4 when randomUUID is unavailable", () => {
    vi.stubGlobal("crypto", {
      getRandomValues: (bytes: Uint8Array) => bytes.fill(0),
    });

    expect(createClientMessageId()).toBe("00000000-0000-4000-8000-000000000000");
  });
});

describe("scrollToMessageAnchor (bu-0ynlk.9)", () => {
  it("scrolls the matching bubble into view, focuses it, and applies/removes the highlight class", () => {
    vi.useFakeTimers();
    try {
      const el = document.createElement("div");
      el.id = messageAnchorId("msg-1");
      el.tabIndex = -1;
      el.scrollIntoView = vi.fn();
      el.focus = vi.fn();
      document.body.appendChild(el);

      const found = scrollToMessageAnchor("msg-1");

      expect(found).toBe(true);
      expect(el.scrollIntoView).toHaveBeenCalledWith({ behavior: "smooth", block: "center" });
      // Focusing (bu-0ynlk.11) is what makes the #m-{id} URL fragment behave
      // like a real anchor for keyboard/screen-reader navigation, not just a
      // scroll position -- preventScroll avoids double-scrolling on top of
      // the smooth scrollIntoView above.
      expect(el.focus).toHaveBeenCalledWith({ preventScroll: true });
      expect(el.classList.contains("chat-message-highlight")).toBe(true);

      vi.advanceTimersByTime(2000);
      expect(el.classList.contains("chat-message-highlight")).toBe(false);
    } finally {
      vi.useRealTimers();
    }
  });

  it("returns false without throwing when the anchor isn't in the DOM yet", () => {
    expect(scrollToMessageAnchor("missing-message")).toBe(false);
  });
});

describe("chatMessageDeepLink (bu-0ynlk.11)", () => {
  it("builds the /chat/{conversationId}#m-{messageId} URL", () => {
    expect(chatMessageDeepLink("conv-1", "msg-1")).toBe("/chat/conv-1#m-msg-1");
  });
});
