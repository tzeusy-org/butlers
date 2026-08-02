// @vitest-environment jsdom

import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { ConversationHeader } from "./ConversationHeader.tsx";

afterEach(() => cleanup());

describe("ConversationHeader — current-turn accountability", () => {
  it("keeps the addressed Butler as plain text before any receipt", () => {
    render(
      <ConversationHeader
        butlerName="switchboard"
        conversation={null}
        messages={[]}
        pricingMap={null}
      />,
    );

    expect(screen.getByText("switchboard")).toBeDefined();
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("does not turn a historical route into current-turn accountability", () => {
    render(
      <ConversationHeader
        butlerName="switchboard"
        conversation={{
          id: "conversation-1",
          butler_name: "switchboard",
          title: "Existing conversation",
          status: "active",
          created_at: "2026-08-02T00:00:00.000Z",
          updated_at: "2026-08-02T00:00:00.000Z",
          message_count: 1,
          routed_butler: "relationship",
        }}
        messages={[]}
        pricingMap={null}
      />,
    );

    expect(screen.getByText("switchboard")).toBeDefined();
    expect(screen.queryByRole("link", { name: "relationship" })).toBeNull();
  });

  it("links only the exact named receipt for the current stream", () => {
    render(
      <ConversationHeader
        butlerName="switchboard"
        conversation={{
          id: "conversation-1",
          butler_name: "switchboard",
          title: "Existing conversation",
          status: "active",
          created_at: "2026-08-02T00:00:00.000Z",
          updated_at: "2026-08-02T00:00:00.000Z",
          message_count: 1,
          routed_butler: "relationship",
        }}
        messages={[]}
        pricingMap={null}
        routedButler="finance and planning"
      />,
    );

    const link = screen.getByRole("link", { name: "finance and planning" });
    expect(link.getAttribute("href")).toBe("/butlers/finance%20and%20planning");
    expect(link.parentElement?.textContent).toContain("Routed to");
    expect(link.parentElement?.textContent).not.toContain("Handled by");
    expect(screen.queryByRole("link", { name: "relationship" })).toBeNull();
  });
});
