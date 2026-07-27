// @vitest-environment jsdom

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { MessageThread, type StreamingState } from "./MessageThread.tsx";

describe("MessageThread — pending conversation activity", () => {
  it("announces submission before a new conversation has received its server id", () => {
    const streaming: StreamingState = {
      conversationId: "pending",
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
});
