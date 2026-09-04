// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { MessageThread, type StreamingState } from "./MessageThread.tsx";
import type { Message } from "@/api/types.ts";

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

    expect(screen.getByRole("status").textContent).toBe("Sending to Switchboard.");
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

    expect(screen.getAllByRole("status")).toHaveLength(1);
    expect(screen.getByRole("status").textContent).toBe(
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
    expect(screen.queryByRole("status")).toBeNull();

    rerender(
      <MessageThread
        messages={[]}
        streaming={{ ...streaming, cancelled: true, pending: false }}
        pricingMap={null}
        conversationId="conversation-1"
      />,
    );
    expect(screen.queryByTestId("chat-activity-status")).toBeNull();
    expect(screen.queryByRole("status")).toBeNull();
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
