// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";

import { MessageThread, type StreamingState } from "./MessageThread.tsx";
import { usePrefersReducedMotion } from "@/hooks/use-prefers-reduced-motion";
import type { Message } from "@/api/types.ts";

vi.mock("@/hooks/use-prefers-reduced-motion", () => ({
  usePrefersReducedMotion: vi.fn(() => false),
}));

beforeEach(() => {
  vi.mocked(usePrefersReducedMotion).mockReturnValue(false);
});

afterEach(() => cleanup());

function makeAssistantMessage(overrides: Partial<Message> = {}): Message {
  return {
    id: "message-1",
    conversation_id: "conversation-1",
    role: "assistant",
    content: "Recorded — correct?",
    tool_calls: null,
    error: null,
    model: null,
    input_tokens: null,
    output_tokens: null,
    duration_ms: null,
    session_id: null,
    request_id: null,
    created_at: "2026-09-05T00:00:00Z",
    ...overrides,
  };
}

describe("MessageThread — pending conversation activity", () => {
  it("announces submission before a new conversation has received its server id", () => {
    const streaming: StreamingState = {
      conversationId: "pending",
      messageId: "message-1",
      content: "",
      pending: true,
      interrupted: false,
    };

    render(
      <MessageThread
        messages={[]}
        streaming={streaming}
        pricingMap={null}
        conversationId={null}
      />,
    );

    expect(screen.getByTestId("chat-activity-status").textContent).toBe("Sending to Switchboard.");
    expect(screen.queryByText("No messages yet. Start the conversation below.")).toBeNull();
  });

  it("keeps three animated decorative dots beside one polite status", () => {
    const streaming: StreamingState = {
      conversationId: "conversation-1",
      messageId: "message-1",
      content: "",
      pending: true,
      interrupted: false,
      dispatchReceipt: { routedButler: "finance" },
    };

    const { container } = render(
      <MessageThread
        messages={[]}
        streaming={streaming}
        pricingMap={null}
        conversationId="conversation-1"
      />,
    );

    expect(screen.getByTestId("chat-activity-status").textContent).toBe(
      "Routed to finance; waiting for a reply.",
    );
    const dots = container.querySelectorAll("span.animate-bounce");
    expect(dots).toHaveLength(3);
    for (const dot of dots) {
      expect(dot.getAttribute("aria-hidden")).toBe("true");
    }
  });

  it("suppresses receipt activity while Stop is settling or confirmed", () => {
    const streaming: StreamingState = {
      conversationId: "conversation-1",
      messageId: "message-1",
      content: "",
      pending: true,
      interrupted: false,
      dispatchReceipt: { routedButler: "finance" },
    };
    const { rerender } = render(
      <MessageThread
        messages={[]}
        streaming={streaming}
        pricingMap={null}
        conversationId="conversation-1"
      />,
    );

    expect(screen.getByTestId("chat-activity-status").textContent).toBe(
      "Routed to finance; waiting for a reply.",
    );

    rerender(
      <MessageThread
        messages={[]}
        streaming={{ ...streaming, cancelling: true }}
        pricingMap={null}
        conversationId="conversation-1"
      />,
    );
    expect(screen.queryByTestId("chat-activity-status")).toBeNull();

    rerender(
      <MessageThread
        messages={[]}
        streaming={{ ...streaming, cancelled: true, pending: false }}
        pricingMap={null}
        conversationId="conversation-1"
      />,
    );
    expect(screen.queryByTestId("chat-activity-status")).toBeNull();
  });

  it("smoothly scrolls when live conversation activity changes", () => {
    const originalScrollIntoView = HTMLElement.prototype.scrollIntoView;
    const scrollIntoView = vi.fn();
    HTMLElement.prototype.scrollIntoView = scrollIntoView;

    try {
      render(
        <MessageThread
          messages={[]}
          streaming={{
            conversationId: "conversation-1",
            messageId: "message-1",
            content: "",
            pending: true,
            interrupted: false,
          }}
          pricingMap={null}
          conversationId="conversation-1"
        />,
      );

      expect(scrollIntoView).toHaveBeenCalledWith({ behavior: "smooth" });
    } finally {
      HTMLElement.prototype.scrollIntoView = originalScrollIntoView;
    }
  });

  it("scrolls instantly (not smoothly) under prefers-reduced-motion", () => {
    vi.mocked(usePrefersReducedMotion).mockReturnValue(true);
    const originalScrollIntoView = HTMLElement.prototype.scrollIntoView;
    const scrollIntoView = vi.fn();
    HTMLElement.prototype.scrollIntoView = scrollIntoView;

    try {
      render(
        <MessageThread
          messages={[]}
          streaming={{
            conversationId: "conversation-1",
            messageId: "message-1",
            content: "",
            pending: true,
            interrupted: false,
          }}
          pricingMap={null}
          conversationId="conversation-1"
        />,
      );

      expect(scrollIntoView).toHaveBeenCalledWith({ behavior: "auto" });
    } finally {
      HTMLElement.prototype.scrollIntoView = originalScrollIntoView;
    }
  });

  it("updates the status text in order as phase events arrive (bu-0ynlk.7)", () => {
    const base: StreamingState = {
      conversationId: "conversation-1",
      messageId: "message-1",
      content: "",
      pending: true,
      interrupted: false,
    };

    const { rerender } = render(
      <MessageThread
        messages={[]}
        streaming={{ ...base, phase: { name: "classifying" } }}
        pricingMap={null}
        conversationId="conversation-1"
      />,
    );
    expect(screen.getByTestId("chat-activity-status").textContent).toBe("Classifying your message.");

    rerender(
      <MessageThread
        messages={[]}
        streaming={{ ...base, phase: { name: "routed", target: "Finance" } }}
        pricingMap={null}
        conversationId="conversation-1"
      />,
    );
    expect(screen.getByTestId("chat-activity-status").textContent).toBe("Routed to Finance.");

    rerender(
      <MessageThread
        messages={[]}
        streaming={{
          ...base,
          phase: { name: "thinking", tool: "spend_summary" },
        }}
        pricingMap={null}
        conversationId="conversation-1"
      />,
    );
    expect(screen.getByTestId("chat-activity-status").textContent).toBe("Thinking (tool: spend_summary).");

    rerender(
      <MessageThread
        messages={[]}
        streaming={{ ...base, phase: { name: "writing" } }}
        pricingMap={null}
        conversationId="conversation-1"
      />,
    );
    expect(screen.getByTestId("chat-activity-status").textContent).toBe("Writing a reply.");
  });

  it("throttles the scroll effect to at most once per 100ms under a burst of updates", () => {
    vi.useFakeTimers();
    const originalScrollIntoView = HTMLElement.prototype.scrollIntoView;
    const scrollIntoView = vi.fn();
    HTMLElement.prototype.scrollIntoView = scrollIntoView;

    try {
      const base: StreamingState = {
        conversationId: "conversation-1",
        messageId: "message-1",
        content: "",
        pending: false,
        interrupted: false,
      };

      const { rerender } = render(
        <MessageThread
          messages={[]}
          streaming={base}
          pricingMap={null}
          conversationId="conversation-1"
        />,
      );
      // The leading update in the burst scrolls immediately.
      expect(scrollIntoView).toHaveBeenCalledTimes(1);

      // 50 rapid content updates, all within one 100ms window.
      for (let i = 0; i < 50; i++) {
        rerender(
          <MessageThread
            messages={[]}
            streaming={{ ...base, content: "x".repeat(i + 1) }}
            pricingMap={null}
            conversationId="conversation-1"
          />,
        );
      }
      // No more than the leading call so far -- nothing in the burst was
      // allowed to fire on its own within the throttle window.
      expect(scrollIntoView).toHaveBeenCalledTimes(1);

      // Advance past the throttle window: exactly one trailing call fires.
      vi.advanceTimersByTime(150);
      expect(scrollIntoView).toHaveBeenCalledTimes(2);
    } finally {
      HTMLElement.prototype.scrollIntoView = originalScrollIntoView;
      vi.useRealTimers();
    }
  });
});

describe("MessageThread — sr-only live announcer region (bu-0ynlk.13)", () => {
  function streamAt(content: string, overrides: Partial<StreamingState> = {}): StreamingState {
    return {
      conversationId: "conversation-1",
      messageId: "message-1",
      content,
      pending: false,
      interrupted: false,
      ...overrides,
    };
  }

  it("renders a polite, non-atomic sr-only status region alongside the message list", () => {
    render(
      <MessageThread
        messages={[]}
        streaming={streamAt("")}
        pricingMap={null}
        conversationId="conversation-1"
      />,
    );

    const region = screen.getByTestId("chat-reply-live-region");
    expect(region.getAttribute("role")).toBe("status");
    expect(region.getAttribute("aria-live")).toBe("polite");
    expect(region.getAttribute("aria-atomic")).toBe("false");
    expect(region.className).toContain("sr-only");
  });

  it("batches a streamed reply into sentence spans — not one child per token", () => {
    const { rerender } = render(
      <MessageThread
        messages={[]}
        streaming={streamAt("")}
        pricingMap={null}
        conversationId="conversation-1"
      />,
    );

    const fullText = "This is the first sentence. This is the second sentence.";
    let acc = "";
    for (const token of fullText.split(" ")) {
      acc = acc ? `${acc} ${token}` : token;
      rerender(
        <MessageThread
          messages={[]}
          streaming={streamAt(acc)}
          pricingMap={null}
          conversationId="conversation-1"
        />,
      );
    }
    // One more whitespace token — the trailing sentence only crosses its
    // boundary once whitespace follows it, same as the first sentence did.
    rerender(
      <MessageThread
        messages={[]}
        streaming={streamAt(`${fullText} `)}
        pricingMap={null}
        conversationId="conversation-1"
      />,
    );

    const region = screen.getByTestId("chat-reply-live-region");
    expect(region.children).toHaveLength(2);
    expect(region.textContent).toBe(
      "This is the first sentence.This is the second sentence.",
    );
  });

  it("appends a 'Reply complete' span once streaming ends, only when the reply ran long", () => {
    vi.useFakeTimers();
    try {
      const { rerender } = render(
        <MessageThread
          messages={[]}
          streaming={streamAt("Slow reply.", { pending: false })}
          pricingMap={null}
          conversationId="conversation-1"
        />,
      );

      vi.advanceTimersByTime(3500);

      const committedMessage: Message = {
        id: "message-1",
        conversation_id: "conversation-1",
        role: "assistant",
        content: "Slow reply.",
        tool_calls: null,
        error: null,
        model: null,
        input_tokens: null,
        output_tokens: null,
        duration_ms: null,
        session_id: null,
        request_id: null,
        created_at: "2026-09-05T00:00:00Z",
      };
      act(() => {
        rerender(
          <MessageThread
            messages={[committedMessage]}
            streaming={null}
            pricingMap={null}
            conversationId="conversation-1"
          />,
        );
      });

      const region = screen.getByTestId("chat-reply-live-region");
      expect(region.textContent).toContain("Reply complete");
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("MessageThread — session link (bu-0ynlk.5)", () => {
  // openspec/specs/dashboard-chat-ui/spec.md:282-291
  // Requirement: Session Linkage Navigation
  // Scenario: Session link on assistant message
  it("renders the View session link when the message carries a session_id", () => {
    const message = makeAssistantMessage({ session_id: "11111111-1111-1111-1111-111111111111" });

    render(
      <MessageThread
        messages={[message]}
        streaming={null}
        pricingMap={null}
        conversationId="conversation-1"
      />,
    );

    const link = screen.getByTitle("View session");
    expect(link.getAttribute("href")).toBe(`/sessions/${message.session_id}`);
  });

  it("omits the View session link when session_id is absent", () => {
    const message = makeAssistantMessage({ session_id: null });

    render(
      <MessageThread
        messages={[message]}
        streaming={null}
        pricingMap={null}
        conversationId="conversation-1"
      />,
    );

    expect(screen.queryByTitle("View session")).toBeNull();
  });
});

describe("MessageThread — tool call visibility (bu-0ynlk.5)", () => {
  // openspec/specs/dashboard-chat-ui/spec.md:80-86
  // Requirement: Message Thread Display
  // Scenario: Tool call visibility
  it("renders a collapsible tool calls section when the message carries tool_calls", () => {
    const message = makeAssistantMessage({
      tool_calls: [{ id: null, name: "finance.get_budget", arguments: { month: "2026-09" } }],
    });

    render(
      <MessageThread
        messages={[message]}
        streaming={null}
        pricingMap={null}
        conversationId="conversation-1"
      />,
    );

    expect(screen.getByText("1 tool call")).toBeTruthy();
  });

  it("omits the tool calls section when tool_calls is null", () => {
    const message = makeAssistantMessage({ tool_calls: null });

    render(
      <MessageThread
        messages={[message]}
        streaming={null}
        pricingMap={null}
        conversationId="conversation-1"
      />,
    );

    expect(screen.queryByText(/tool call/)).toBeNull();
  });
});
