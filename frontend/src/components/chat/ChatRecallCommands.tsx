/**
 * Registers recent Switchboard conversations as cmdk palette commands,
 * navigating to `/chat/{id}` (bu-0ynlk.11 — recall integration alongside
 * "Talk to Butlers", see FloatingChatWidget.tsx). Mounted once in
 * RootLayout, unconditionally of chat posture (dock vs popover), so recall
 * works the same regardless of which posture is currently showing.
 */

import { useMemo } from "react";
import { useNavigate } from "react-router";

import type { ConversationSummary } from "@/api/types.ts";
import { WIDGET_BUTLER } from "./chat-constants.ts";
import { useConversations } from "@/hooks/use-conversations.ts";
import { useRegisterCommands, type PaletteCommand } from "@/lib/command-registry.tsx";

/** How many recent threads to surface in the palette — a quick-recall list,
 * not a substitute for message search (bu-0ynlk.9 owns that). */
const RECENT_THREAD_LIMIT = 10;

export function ChatRecallCommands() {
  const navigate = useNavigate();
  const { data, isError, refetch } = useConversations(WIDGET_BUTLER, {
    limit: RECENT_THREAD_LIMIT,
  });
  const conversationsResult = data?.data;
  const conversations: ConversationSummary[] = useMemo(
    () => conversationsResult ?? [],
    [conversationsResult],
  );

  const commands = useMemo<PaletteCommand[]>(() => {
    // A failed fetch must not register zero commands identically to a
    // genuinely empty recent-threads list — name the degraded source inline
    // (query-boundary.tsx's SourceDegradedNote convention, adapted to the
    // command-palette medium since this registrar has no render surface).
    if (isError) {
      return [
        {
          id: "chat-recall:unavailable",
          label: "Recent conversations unavailable, retry",
          keywords: ["chat", "conversation", "recent", "recall", "error"],
          perform: () => void refetch(),
        },
      ];
    }
    return conversations.map((conversation) => ({
      id: `chat-recall:${conversation.id}`,
      label: conversation.title ?? "Untitled conversation",
      keywords: ["chat", "conversation", "recent", "recall"],
      perform: () => navigate(`/chat/${conversation.id}`),
    }));
  }, [conversations, isError, navigate, refetch]);

  useRegisterCommands(commands);

  return null;
}
