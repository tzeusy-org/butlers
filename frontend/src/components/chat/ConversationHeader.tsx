/**
 * Header bar inside the chat panel showing conversation title, total cost,
 * and the selected conversation's rename/archive actions
 * (REQ-dashboard-chat-ui-017), which are also registered as command-palette
 * commands so keyboard and pointer reach the same target-specific action.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { MessageCircleIcon } from "lucide-react";
import type { ConversationSummary, Message, PricingMap } from "@/api/types.ts";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  conversationTitle,
  useConversationMetadata,
  useConversationMetadataPending,
} from "@/hooks/use-conversation-metadata.ts";
import { useRegisterCommands, type PaletteCommand } from "@/lib/command-registry.tsx";
import { ConversationActionsMenu, ConversationTitleEditor } from "./ConversationActions.tsx";

// ---------------------------------------------------------------------------
// Cost calculation
// ---------------------------------------------------------------------------

function totalConversationCost(
  messages: Message[],
  pricingMap: PricingMap | null,
): string | null {
  if (!pricingMap) return null;

  let totalCost = 0;
  let hasCost = false;

  for (const msg of messages) {
    const modelName = msg.model_name ?? msg.model ?? null;
    if (msg.role !== "assistant" || !modelName) continue;
    const pricing = pricingMap[modelName];
    if (!pricing) continue;
    const input = msg.input_tokens ?? 0;
    const output = msg.output_tokens ?? 0;
    totalCost +=
      (input / 1_000_000) * pricing.input_per_million +
      (output / 1_000_000) * pricing.output_per_million;
    hasCost = true;
  }

  return hasCost ? `~$${totalCost.toFixed(4)}` : null;
}

// ---------------------------------------------------------------------------
// ConversationHeader
// ---------------------------------------------------------------------------

export interface ConversationHeaderProps {
  butlerName: string;
  conversation: ConversationSummary | null;
  messages: Message[];
  pricingMap: PricingMap | null;
  /** Durable route for the currently pending turn, if its stream proved one. */
  routedButler?: string | null;
}

interface RenameState {
  conversationId: string;
  initialTitle: string;
  error: string | null;
}

export function ConversationHeader({
  butlerName,
  conversation,
  messages,
  pricingMap,
  routedButler,
}: ConversationHeaderProps) {
  const title = conversation ? conversationTitle(conversation) : "New conversation";
  const costStr = totalConversationCost(messages, pricingMap);
  // A historical conversation route is not proof of responsibility for this
  // current message. Only the live stream's durable receipt can earn a link.
  const accountableButler = routedButler ?? null;

  const { rename: renameConversation, archive, unarchive } = useConversationMetadata();
  const pending = useConversationMetadataPending(conversation?.id);
  const archived = conversation?.status === "archived";

  // The editor belongs to one conversation: switching threads hides it, and a
  // late rename failure reopens it only if that conversation is still shown.
  const [rename, setRename] = useState<RenameState | null>(null);
  const editing = rename !== null && rename.conversationId === conversation?.id;
  const currentIdRef = useRef(conversation?.id);
  useEffect(() => {
    currentIdRef.current = conversation?.id;
  });

  const triggerRef = useRef<HTMLButtonElement>(null);
  const titleRef = useRef<HTMLParagraphElement>(null);
  const returnFocus = useRef(false);
  useEffect(() => {
    if (!returnFocus.current || editing) return;
    returnFocus.current = false;
    (triggerRef.current ?? titleRef.current)?.focus();
  });

  function closeEditor() {
    returnFocus.current = true;
    setRename(null);
  }

  function commitRename(nextTitle: string) {
    if (!conversation) return;
    const target = conversation;
    closeEditor();
    void renameConversation(target, nextTitle, {
      onFailed: (failedTitle) => {
        if (currentIdRef.current !== target.id) return;
        setRename({
          conversationId: target.id,
          initialTitle: failedTitle,
          error: "Rename not confirmed. Edit and save to retry.",
        });
      },
    });
  }

  const commands = useMemo<PaletteCommand[]>(() => {
    if (!conversation) return [];
    const target = `${conversationTitle(conversation)} (${conversation.butler_name})`;
    const keywords = ["chat", "conversation", conversation.butler_name];
    return [
      {
        id: `conversation-metadata:rename:${conversation.id}`,
        label: `Rename conversation: ${target}`,
        keywords: [...keywords, "rename", "title"],
        perform: () =>
          setRename({
            conversationId: conversation.id,
            initialTitle: conversation.title ?? "",
            error: null,
          }),
      },
      conversation.status === "archived"
        ? {
            id: `conversation-metadata:unarchive:${conversation.id}`,
            label: `Unarchive conversation: ${target}`,
            keywords: [...keywords, "unarchive", "restore"],
            perform: () => void unarchive(conversation),
          }
        : {
            id: `conversation-metadata:archive:${conversation.id}`,
            label: `Archive conversation: ${target}`,
            keywords: [...keywords, "archive", "hide"],
            perform: () => void archive(conversation),
          },
    ];
  }, [conversation, archive, unarchive]);
  useRegisterCommands(commands);

  return (
    <div className="flex items-center gap-2 px-4 py-3 border-b">
      <MessageCircleIcon className="size-4 text-muted-foreground shrink-0" />
      <div className="flex-1 min-w-0">
        {editing && conversation ? (
          <ConversationTitleEditor
            key={`${rename.conversationId}:${rename.error ?? ""}`}
            initialTitle={rename.initialTitle}
            initialError={rename.error}
            label={`Title for ${title}`}
            onCommit={commitRename}
            onCancel={closeEditor}
          />
        ) : (
          <div className="flex items-center gap-1.5 min-w-0">
            <p ref={titleRef} tabIndex={-1} className="text-sm font-medium truncate rounded-sm focus-visible:ring-2 focus-visible:ring-focus">
              {title}
            </p>
            {archived && (
              <Badge variant="outline" className="text-[10px] h-4 px-1 shrink-0">
                Archived
              </Badge>
            )}
          </div>
        )}
        <p className="text-xs text-muted-foreground">
          {accountableButler ? (
            <>
              Routed to{" "}
              <a
                href={`/butlers/${encodeURIComponent(accountableButler)}`}
                className="underline underline-offset-4 hover:text-foreground"
              >
                {accountableButler}
              </a>
            </>
          ) : (
            butlerName
          )}
          {costStr && ` · ${costStr}`}
          {archived && " · Sending a message makes it active again."}
        </p>
      </div>
      {conversation && archived && !editing && (
        <Button
          variant="outline"
          size="xs"
          disabled={pending}
          onClick={() => void unarchive(conversation)}
        >
          Unarchive
        </Button>
      )}
      {conversation && !editing && (
        <ConversationActionsMenu
          conversation={conversation}
          triggerRef={triggerRef}
          onRename={() =>
            setRename({
              conversationId: conversation.id,
              initialTitle: conversation.title ?? "",
              error: null,
            })
          }
        />
      )}
    </div>
  );
}
