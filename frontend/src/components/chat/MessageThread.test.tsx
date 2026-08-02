// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { MessageThread, type StreamingState } from "./MessageThread.tsx";

afterEach(() => cleanup());

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
