/**
 * Scrollable message thread displaying the conversation history.
 */

import { useEffect, useRef, useState } from "react";
import { ExternalLinkIcon } from "lucide-react";
import { formatDistanceToNow } from "date-fns";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { TypingIndicator } from "./TypingIndicator";
import { ToolCallDetails } from "./ToolCallDetails";
import type { Message, PricingMap } from "@/api/types.ts";

// ---------------------------------------------------------------------------
// Cost estimation helper
// ---------------------------------------------------------------------------

function estimateCost(
  inputTokens: number | null,
  outputTokens: number | null,
  model: string | null,
  pricingMap: PricingMap | null,
): string | null {
  if (!inputTokens || !outputTokens || !model || !pricingMap) return null;
  const pricing = pricingMap[model];
  if (!pricing) return null;
  const cost =
    (inputTokens / 1_000_000) * pricing.input_per_million +
    (outputTokens / 1_000_000) * pricing.output_per_million;
  return `~$${cost.toFixed(4)}`;
}

// ---------------------------------------------------------------------------
// Simple inline markdown renderer (code blocks, paragraphs)
// ---------------------------------------------------------------------------

function SimpleMarkdown({ content }: { content: string }) {
  // Split on fenced code blocks
  const parts = content.split(/(```[\s\S]*?```)/g);
  return (
    <div className="space-y-2 text-sm leading-relaxed">
      {parts.map((part, i) => {
        if (part.startsWith("```")) {
          const firstNewline = part.indexOf("\n");
          const lang = firstNewline > 3 ? part.slice(3, firstNewline).trim() : "";
          const code = part.slice(firstNewline + 1, -3);
          return (
            <pre
              key={i}
              className="rounded-md bg-muted/50 border p-3 text-xs font-mono overflow-x-auto whitespace-pre"
              data-lang={lang || undefined}
            >
              {code}
            </pre>
          );
        }
        // Render normal text — preserve newlines
        return (
          <p key={i} className="whitespace-pre-wrap break-words">
            {part}
          </p>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Single message bubble
// ---------------------------------------------------------------------------

interface MessageBubbleProps {
  message: Message;
  pricingMap: PricingMap | null;
  /** Streaming content appended to this message (while SSE is active). */
  streamingContent?: string;
  interrupted?: boolean;
}

function MessageBubble({
  message,
  pricingMap,
  streamingContent,
  interrupted,
}: MessageBubbleProps) {
  const isUser = message.role === "user";
  const displayContent = streamingContent !== undefined ? streamingContent : message.content;
  const costStr = estimateCost(
    message.input_tokens,
    message.output_tokens,
    message.model,
    pricingMap,
  );

  return (
    <div
      className={cn("flex flex-col gap-1 max-w-[85%]", isUser ? "self-end items-end" : "self-start items-start")}
    >
      <div
        className={cn(
          "rounded-2xl px-4 py-2.5",
          isUser
            ? "bg-primary text-primary-foreground rounded-br-sm"
            : cn(
                "bg-muted rounded-bl-sm",
                message.error && "border-l-2 border-destructive",
              ),
        )}
      >
        {isUser ? (
          <p className="text-sm whitespace-pre-wrap break-words">{displayContent}</p>
        ) : (
          <SimpleMarkdown content={displayContent} />
        )}

        {/* Error display for assistant messages */}
        {!isUser && message.error && (
          <p className="text-destructive text-sm mt-2">{message.error}</p>
        )}

        {/* Interrupted indicator */}
        {interrupted && (
          <p className="text-muted-foreground text-xs mt-1 italic">Interrupted</p>
        )}
      </div>

      {/* Tool calls */}
      {!isUser && message.tool_calls && message.tool_calls.length > 0 && (
        <div className="w-full max-w-xs">
          <ToolCallDetails toolCalls={message.tool_calls} />
        </div>
      )}

      {/* Message metadata */}
      <div
        className={cn(
          "flex items-center gap-2 flex-wrap",
          isUser ? "flex-row-reverse" : "flex-row",
        )}
      >
        <span className="text-xs text-muted-foreground">
          {formatDistanceToNow(new Date(message.created_at), { addSuffix: true })}
        </span>

        {!isUser && message.model && (
          <Badge variant="outline" className="text-[10px] h-4 px-1 font-mono">
            {message.model.split("/").pop() ?? message.model}
          </Badge>
        )}

        {!isUser && message.input_tokens != null && message.output_tokens != null && (
          <span className="text-xs text-muted-foreground">
            {message.input_tokens}+{message.output_tokens} tokens
          </span>
        )}

        {!isUser && message.duration_ms != null && (
          <span className="text-xs text-muted-foreground">{message.duration_ms}ms</span>
        )}

        {!isUser && costStr && (
          <span className="text-xs text-muted-foreground">{costStr}</span>
        )}

        {/* Session link */}
        {!isUser && message.session_id && (
          <a
            href={`/sessions/${message.session_id}`}
            target="_blank"
            rel="noopener noreferrer"
            className="text-muted-foreground hover:text-foreground transition-colors"
            title="View session"
          >
            <ExternalLinkIcon className="size-3" />
          </a>
        )}

        {/* Request lineage link */}
        {!isUser && message.request_id && (
          <a
            href={`/ingestion?event=${message.request_id}`}
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs text-muted-foreground hover:text-foreground transition-colors underline"
            title="View lineage"
          >
            View lineage
          </a>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// MessageThread
// ---------------------------------------------------------------------------

export interface StreamingState {
  /** Conversation ID that is currently streaming. */
  conversationId: string;
  /** Content accumulated so far from SSE token events. */
  content: string;
  /** True while awaiting the first token (typing indicator phase). */
  pending: boolean;
  /** True if the user cancelled the stream. */
  interrupted: boolean;
}

export interface MessageThreadProps {
  messages: Message[];
  streaming: StreamingState | null;
  pricingMap: PricingMap | null;
  conversationId: string | null;
}

export function MessageThread({
  messages,
  streaming,
  pricingMap,
  conversationId,
}: MessageThreadProps) {
  const bottomRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [userScrolledUp, setUserScrolledUp] = useState(false);

  // Detect manual scroll-up
  function handleScroll() {
    const el = containerRef.current;
    if (!el) return;
    const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    setUserScrolledUp(distFromBottom > 100);
  }

  // Auto-scroll to bottom when new messages arrive, unless user scrolled up
  useEffect(() => {
    if (!userScrolledUp) {
      bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [messages.length, streaming?.content, userScrolledUp]);

  const isStreamingThisConversation =
    streaming !== null && streaming.conversationId === conversationId;

  if (messages.length === 0 && !isStreamingThisConversation) {
    return (
      <div className="flex-1 flex items-center justify-center text-muted-foreground text-sm">
        No messages yet. Start the conversation below.
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      onScroll={handleScroll}
      className="flex-1 overflow-y-auto px-4 py-4 flex flex-col gap-4"
    >
      {messages.map((msg) => {
        // If this is the last assistant message and streaming is active for
        // this conversation, show streamed content overlay
        const isStreamingTarget =
          isStreamingThisConversation &&
          !streaming.pending &&
          msg.role === "assistant" &&
          msg === messages[messages.length - 1];

        return (
          <MessageBubble
            key={msg.id}
            message={msg}
            pricingMap={pricingMap}
            streamingContent={
              isStreamingTarget ? streaming.content : undefined
            }
            interrupted={
              isStreamingTarget ? streaming.interrupted : undefined
            }
          />
        );
      })}

      {/* Typing indicator — shown while pending (before first token) */}
      {isStreamingThisConversation && streaming.pending && (
        <TypingIndicator />
      )}

      {/* Streaming assistant message (before it's committed to messages list) */}
      {isStreamingThisConversation &&
        !streaming.pending &&
        (messages.length === 0 ||
          messages[messages.length - 1].role === "user") && (
          <div className="flex flex-col gap-1 max-w-[85%] self-start items-start">
            <div className="rounded-2xl rounded-bl-sm bg-muted px-4 py-2.5">
              <SimpleMarkdown content={streaming.content} />
              {streaming.interrupted && (
                <p className="text-muted-foreground text-xs mt-1 italic">
                  Interrupted
                </p>
              )}
            </div>
          </div>
        )}

      <div ref={bottomRef} />
    </div>
  );
}
