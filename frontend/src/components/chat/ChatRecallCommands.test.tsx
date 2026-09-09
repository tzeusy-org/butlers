// @vitest-environment jsdom
/**
 * ChatRecallCommands — recent-thread cmdk recall (bu-0ynlk.11).
 *
 * Acceptance criterion #4: recent threads appear as commands and navigate to
 * /chat/{id}.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, cleanup } from "@testing-library/react";
import { MemoryRouter } from "react-router";

import type { PaletteCommand } from "@/lib/command-registry";
import type { ConversationSummary } from "@/api/types";

const navigateMock = vi.fn();
vi.mock("react-router", async () => {
  const actual = await vi.importActual<typeof import("react-router")>("react-router");
  return { ...actual, useNavigate: () => navigateMock };
});

vi.mock("@/hooks/use-conversations.ts", () => ({ useConversations: vi.fn() }));

let registeredCommands: PaletteCommand[] = [];
vi.mock("@/lib/command-registry.tsx", () => ({
  useRegisterCommands: (commands: PaletteCommand[]) => {
    registeredCommands = commands;
  },
}));

import { ChatRecallCommands } from "./ChatRecallCommands";
import { useConversations } from "@/hooks/use-conversations.ts";

function makeConversation(overrides: Partial<ConversationSummary> = {}): ConversationSummary {
  return {
    id: "conversation-1",
    butler_name: "switchboard",
    title: "Landlord follow-up",
    status: "active",
    created_at: "2026-09-05T00:00:00Z",
    updated_at: "2026-09-05T00:00:00Z",
    message_count: 4,
    ...overrides,
  };
}

function renderRecall() {
  return render(
    <MemoryRouter>
      <ChatRecallCommands />
    </MemoryRouter>,
  );
}

describe("ChatRecallCommands — recent-thread recall (bu-0ynlk.11)", () => {
  beforeEach(() => {
    registeredCommands = [];
  });
  afterEach(() => cleanup());

  it("registers a command per recent conversation, navigating to /chat/{id}", () => {
    vi.mocked(useConversations).mockReturnValue({
      data: { data: [makeConversation()] },
    } as unknown as ReturnType<typeof useConversations>);

    renderRecall();

    const command = registeredCommands.find((c) => c.id === "chat-recall:conversation-1");
    expect(command).toMatchObject({ label: "Landlord follow-up" });

    command?.perform();
    expect(navigateMock).toHaveBeenCalledWith("/chat/conversation-1");
  });

  it("registers no commands when there are no recent conversations", () => {
    vi.mocked(useConversations).mockReturnValue({
      data: { data: [] },
    } as unknown as ReturnType<typeof useConversations>);

    renderRecall();

    expect(registeredCommands).toEqual([]);
  });

  it("falls back to 'Untitled conversation' when a thread has no title", () => {
    vi.mocked(useConversations).mockReturnValue({
      data: { data: [makeConversation({ id: "conversation-2", title: null })] },
    } as unknown as ReturnType<typeof useConversations>);

    renderRecall();

    expect(registeredCommands).toMatchObject([{ label: "Untitled conversation" }]);
  });
});
