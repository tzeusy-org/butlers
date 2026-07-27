// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { ConversationList } from "./ConversationList.tsx";

vi.mock("@/hooks/use-conversations.ts", () => ({
  useConversations: vi.fn(),
  useConversationSearch: vi.fn(),
}));

import { useConversations, useConversationSearch } from "@/hooks/use-conversations.ts";

function renderConversationList() {
  return render(
    <ConversationList
      butlerName="switchboard"
      activeConversationId={null}
      onSelectConversation={vi.fn()}
      onNewConversation={vi.fn()}
    />,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  vi.mocked(useConversationSearch).mockReturnValue({
    data: { data: [], meta: {} },
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  } as unknown as ReturnType<typeof useConversationSearch>);
});

afterEach(() => cleanup());

describe("ConversationList — read recovery", () => {
  it("surfaces a list read error with an explicit retry instead of an empty-state lie", () => {
    const refetchConversations = vi.fn();
    vi.mocked(useConversations).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      refetch: refetchConversations,
    } as unknown as ReturnType<typeof useConversations>);

    renderConversationList();

    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain("Could not load conversations.");
    expect(screen.queryByText("No conversations yet.")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(refetchConversations).toHaveBeenCalledTimes(1);
  });
});
