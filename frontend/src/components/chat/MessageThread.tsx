/**
 * Scrollable message thread displaying the conversation history.
 *
 * Handles:
 * - Empty state when no messages exist
 * - Loading skeleton
 * - Error rendering (destructive border + error text)
 * - Interrupted indicator for cancelled streams
 * - Typing indicator while awaiting first token
 * - Auto-scroll to bottom
 */

import { useCallback, useEffect, useId, useRef, useState, useSyncExternalStore } from "react";
import { ChevronDownIcon, LinkIcon } from "lucide-react";
import { Time } from "@/components/ui/time";
import { cn } from "@/lib/utils";
import { ButlerMark } from "@/components/ui/ButlerMark";
import { InlineActionLink } from "@/components/ui/inline-action-link";
import { Mono } from "@/components/ui/Mono";
import { Skeleton } from "@/components/ui/skeleton";
import { TypingIndicator } from "./TypingIndicator";
import { ToolCallDetails } from "./ToolCallDetails";
import { AnswerBody } from "./answer/AnswerBody";
import { CitationRow } from "./answer/CitationRow";
import { chatMessageDeepLink, messageAnchorId } from "./message-id.ts";
import { LiveAnnouncer } from "./live-announcer.ts";
import { usePrefersReducedMotion } from "@/hooks/use-prefers-reduced-motion";
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
  if (inputTokens == null || outputTokens == null || !model || !pricingMap) return null;
  const pricing = pricingMap[model];
  if (!pricing) return null;
  const cost =
    (inputTokens / 1_000_000) * pricing.input_per_million +
    (outputTokens / 1_000_000) * pricing.output_per_million;
  return `~$${cost.toFixed(4)}`;
}

// ---------------------------------------------------------------------------
// Loading skeleton for message thread
// ---------------------------------------------------------------------------

export function MessageThreadSkeleton() {
  return (
    <div className="flex-1 p-4 space-y-4">
      {Array.from({ length: 4 }, (_, i) => (
        <div key={i} className={cn("flex", i % 2 === 0 ? "justify-start" : "justify-end")}>
          <Skeleton className={cn("h-10 rounded-2xl", i % 2 === 0 ? "w-3/4" : "w-1/2")} />
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Single message bubble
// ---------------------------------------------------------------------------

interface MessageBubbleProps {
  message: Message;
  pricingMap: PricingMap | null;
  /** Owning conversation, for the copy-link deep link (bu-0ynlk.11) — null
   * before the create-conversation response has assigned one yet. */
  conversationId: string | null;
  /** Streaming content appended to this message (while SSE is active). */
  streamingContent?: string;
  interrupted?: boolean;
  /** Server confirmed this stream's session was killed (bu-ep4ks.2). */
  cancelled?: boolean;
  /** A Stop attempt failed — must render honestly, never as "stopped". */
  cancelError?: string | null;
}

function ResponseDetails({
  model,
  inputTokens,
  outputTokens,
  durationMs,
  cost,
}: {
  model: string | null;
  inputTokens: number | null;
  outputTokens: number | null;
  durationMs: number | null;
  cost: string | null;
}) {
  const [open, setOpen] = useState(false);
  const regionId = `response-details-${useId()}`;
  const hasDetails =
    model !== null || inputTokens !== null || outputTokens !== null || durationMs !== null || cost;
  if (!hasDetails) return null;

  return (
    <div className="relative">
      <InlineActionLink
        aria-expanded={open}
        aria-controls={regionId}
        aria-label={open ? "Hide response details" : "Show response details"}
        onClick={() => setOpen((current) => !current)}
        className="gap-1 px-2"
      >
        Details
        <ChevronDownIcon
          aria-hidden="true"
          className={cn("size-3 transition-transform", open && "rotate-180")}
        />
      </InlineActionLink>
      {open && (
        <div
          id={regionId}
          role="region"
          aria-label="Response details"
          className="absolute left-0 top-full z-10 mt-1 flex w-max max-w-[min(20rem,calc(100vw-2rem))] flex-col gap-1 rounded-md border bg-popover p-3 shadow-md"
        >
          {model && <Mono muted>{model.split("/").pop() ?? model}</Mono>}
          {(inputTokens !== null || outputTokens !== null) && (
            <Mono muted>
              {inputTokens ?? 0} + {outputTokens ?? 0} tokens
            </Mono>
          )}
          {durationMs !== null && <Mono muted>{durationMs}ms</Mono>}
          {cost && <Mono muted>{cost}</Mono>}
        </div>
      )}
    </div>
  );
}

function MessageBubble({
  message,
  pricingMap,
  conversationId,
  streamingContent,
  interrupted,
  cancelled,
  cancelError,
}: MessageBubbleProps) {
  const isUser = message.role === "user";
  const displayContent = streamingContent !== undefined ? streamingContent : message.content;
  const modelName = message.model_name ?? message.model ?? null;
  const costStr = estimateCost(
    message.input_tokens,
    message.output_tokens,
    modelName,
    pricingMap,
  );
  const [linkCopied, setLinkCopied] = useState(false);

  function handleCopyLink() {
    if (!conversationId || !navigator.clipboard) return;
    navigator.clipboard.writeText(
      `${window.location.origin}${chatMessageDeepLink(conversationId, message.id)}`,
    );
    setLinkCopied(true);
    setTimeout(() => setLinkCopied(false), 1200);
  }

  const deepLinkPath = conversationId ? chatMessageDeepLink(conversationId, message.id) : null;

  return (
    <div
      id={messageAnchorId(message.id)}
      tabIndex={-1}
      className={cn(
        "flex min-w-0 max-w-[85%] flex-col gap-1",
        isUser ? "self-end items-end" : "self-start items-start",
      )}
    >
      <div
        className={cn(
          "w-full min-w-0 max-w-full overflow-hidden rounded-2xl px-4 py-2.5",
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
          <AnswerBody content={displayContent} />
        )}

        {!isUser && (message.citations?.length ?? 0) > 0 && (
          <CitationRow citations={message.citations ?? []} />
        )}

        {/* Error display for assistant messages */}
        {!isUser && message.error && (
          <p className="text-destructive text-sm mt-2">{message.error}</p>
        )}

        {/* Cancelled indicator — only rendered once the server confirmed the
            kill (bu-ep4ks.2); takes precedence over the generic Interrupted
            label below, which also fires on unrelated client-side aborts
            (unmount, conversation switch). */}
        {cancelled ? (
          <p className="text-muted-foreground text-xs mt-1 italic">Cancelled by owner</p>
        ) : (
          interrupted && (
            <p className="text-muted-foreground text-xs mt-1 italic">Interrupted</p>
          )
        )}
        {cancelError && <p className="text-destructive text-xs mt-1">{cancelError}</p>}
      </div>

      {/* Tool calls */}
      {!isUser && message.tool_calls && message.tool_calls.length > 0 && (
        <div className="w-full max-w-xs">
          <ToolCallDetails toolCalls={message.tool_calls} />
        </div>
      )}

      {/* Primary attribution and secondary message metadata */}
      <div
        className={cn(
          "flex min-w-0 flex-wrap items-center gap-2 font-sans text-xs text-muted-foreground",
          isUser ? "flex-row-reverse" : "flex-row",
        )}
      >
        {!isUser && message.routed_butler && (
          <>
            <ButlerMark name={message.routed_butler} size={16} />
            <span className="text-foreground">{message.routed_butler}</span>
          </>
        )}

        <span className="font-mono text-[11px] tabular-nums">
          <Time value={message.created_at} mode="relative" />
        </span>

        {/* Session link */}
        {!isUser && message.session_id && (
          <a
            href={`/sessions/${message.session_id}`}
            className="inline-flex min-h-11 items-center rounded-md px-2 font-mono text-[11px] uppercase tracking-wider underline decoration-border-strong underline-offset-4 transition-colors hover:text-foreground focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
            title="View session"
          >
            Session →
          </a>
        )}

        {!isUser && (
          <ResponseDetails
            model={modelName}
            inputTokens={message.input_tokens}
            outputTokens={message.output_tokens}
            durationMs={message.duration_ms}
            cost={costStr}
          />
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

        {/* Copy-link (bu-0ynlk.11): deep link to /chat/{conversationId}#m-{messageId} */}
        {!isUser && deepLinkPath && (
          <button
            type="button"
            onClick={handleCopyLink}
            className="inline-flex min-h-11 min-w-11 items-center justify-center rounded-md text-muted-foreground transition-colors hover:text-foreground focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
            title={linkCopied ? "Copied" : `Copy link (${deepLinkPath})`}
          >
            <LinkIcon className="size-3" />
          </button>
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
  /** Immutable dashboard user-message ID for this exact cancellable turn. */
  messageId: string;
  /** Content accumulated so far from SSE token events. */
  content: string;
  /** True while awaiting the first token (typing indicator phase). */
  pending: boolean;
  /** True if the client-side stream watch was aborted (any reason). */
  interrupted: boolean;
  /** True while a POST .../cancel call for this stream is in flight. */
  cancelling?: boolean;
  /** True after the create/send response proves the durable turn exists. */
  stopReady?: boolean;
  /** True once the server confirmed the in-flight session was killed. */
  cancelled?: boolean;
  /** Set when a cancel attempt failed — surfaced honestly, never dropped. */
  cancelError?: string | null;
  /** Switchboard's accepted-dispatch receipt, before an assistant reply arrives. */
  dispatchReceipt?: {
    routedButler: string | null;
  };
  /** Latest real-time phase update (bu-0ynlk.7) — supersedes dispatchReceipt's
   * text below once at least one phase event has arrived for this turn. */
  phase?: {
    name: string;
    target?: string | null;
    tool?: string | null;
  };
}

export interface MessageThreadProps {
  messages: Message[];
  streaming: StreamingState | null;
  pricingMap: PricingMap | null;
  conversationId: string | null;
  /** Avoid presenting an unavailable history query as a successful empty thread. */
  suppressEmptyState?: boolean;
}

function pendingActivityStatus(streaming: StreamingState): string {
  const phase = streaming.phase;
  if (phase) {
    switch (phase.name) {
      case "classifying":
        return "Classifying your message.";
      case "routed":
        return phase.target ? `Routed to ${phase.target}.` : "Routed.";
      case "starting_session":
        return phase.target
          ? `Starting session with ${phase.target}.`
          : "Starting session.";
      case "thinking":
        return phase.tool ? `Thinking (tool: ${phase.tool}).` : "Thinking.";
      case "writing":
        return "Writing a reply.";
      default:
        break;
    }
  }

  if (!streaming.dispatchReceipt) return "Sending to Switchboard.";

  return streaming.dispatchReceipt.routedButler
    ? `Routed to ${streaming.dispatchReceipt.routedButler}; waiting for a reply.`
    : "Received by Switchboard; waiting for a reply.";
}

/**
 * A per-instance (not module-scope) `LiveAnnouncer` store read via
 * `useSyncExternalStore` — same "small store, notify on change" shape as
 * `lib/shell-announcer.ts` and `use-chat-unread.ts`'s watermark, chosen here
 * over `useState` because a `LiveAnnouncer` decides on its own schedule
 * (sentence boundary, idle timer) when a new span is ready; committing that
 * straight to `useState` from inside the feeding effect trips this repo's
 * react-hooks/set-state-in-effect gate, and the effect must stay the trigger
 * (streamed content is an external input, not derived state).
 */
function createLiveAnnouncerStore() {
  let spans: readonly string[] = [];
  let messageId: string | null = null;
  let announcer: LiveAnnouncer | null = null;
  const listeners = new Set<() => void>();

  function notify(): void {
    for (const listener of listeners) listener();
  }

  function ensureAnnouncer(forMessageId: string): LiveAnnouncer {
    if (messageId !== forMessageId) {
      announcer?.dispose();
      messageId = forMessageId;
      spans = [];
      const created = new LiveAnnouncer((next) => {
        spans = next;
        notify();
      });
      announcer = created;
      notify();
      return created;
    }
    // Invariant: messageId only ever matches a forMessageId that was set by
    // the branch above, which always assigns `announcer` first.
    return announcer as LiveAnnouncer;
  }

  return {
    feed(forMessageId: string, content: string): void {
      ensureAnnouncer(forMessageId).feed(content);
    },
    complete(): void {
      announcer?.complete();
      messageId = null;
    },
    dispose(): void {
      announcer?.dispose();
    },
    subscribe(listener: () => void): () => void {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    getSnapshot(): readonly string[] {
      return spans;
    },
  };
}

/**
 * Feeds streamed/committed reply content into a `LiveAnnouncer` (bu-0ynlk.13)
 * and returns the sr-only spans it has emitted so far, keyed to the current
 * streaming turn — reset whenever a new `messageId` starts streaming, and
 * flushed (with the "Reply complete" marker, when earned) once the turn is
 * no longer streaming in this conversation.
 */
function useLiveAnnouncerSpans(
  isStreamingThisConversation: boolean,
  streaming: StreamingState | null,
): readonly string[] {
  const [store] = useState(createLiveAnnouncerStore);
  const wasStreamingRef = useRef(false);

  useEffect(() => {
    if (isStreamingThisConversation && streaming) {
      store.feed(streaming.messageId, streaming.content);
      wasStreamingRef.current = true;
    } else if (wasStreamingRef.current) {
      store.complete();
      wasStreamingRef.current = false;
    }
  }, [isStreamingThisConversation, streaming, store]);

  useEffect(() => () => store.dispose(), [store]);

  return useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot);
}

export function MessageThread({
  messages,
  streaming,
  pricingMap,
  conversationId,
  suppressEmptyState = false,
}: MessageThreadProps) {
  const bottomRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [userScrolledUp, setUserScrolledUp] = useState(false);
  const prefersReducedMotion = usePrefersReducedMotion();

  // Detect manual scroll-up
  function handleScroll() {
    const el = containerRef.current;
    if (!el) return;
    const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    setUserScrolledUp(distFromBottom > 100);
  }

  // Throttled auto-scroll (bu-0ynlk.7): real token streaming can update
  // `streaming.content` many times a second, and scrolling on every single
  // update is both wasted work and visually janky. Leading+trailing throttle
  // at SCROLL_THROTTLE_MS -- the first update in a burst scrolls right away,
  // a burst mid-window schedules exactly one trailing scroll at the window's
  // edge (never more than one pending timer), so a sustained burst still
  // follows along roughly every SCROLL_THROTTLE_MS rather than only jumping
  // once at the very end. Refs (not state) so scheduling survives across
  // rapid effect re-runs instead of being cancelled/restarted by each one.
  const SCROLL_THROTTLE_MS = 120;
  const lastScrollAtRef = useRef(0);
  const scrollTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const scrollToBottom = useCallback(() => {
    lastScrollAtRef.current = Date.now();
    bottomRef.current?.scrollIntoView({
      behavior: prefersReducedMotion ? "auto" : "smooth",
    });
  }, [prefersReducedMotion]);

  // Auto-scroll to bottom when new messages/phase/content arrive, unless the
  // user scrolled up.
  useEffect(() => {
    if (userScrolledUp) return;
    const elapsed = Date.now() - lastScrollAtRef.current;
    if (elapsed >= SCROLL_THROTTLE_MS) {
      scrollToBottom();
    } else if (scrollTimeoutRef.current === null) {
      scrollTimeoutRef.current = setTimeout(() => {
        scrollTimeoutRef.current = null;
        scrollToBottom();
      }, SCROLL_THROTTLE_MS - elapsed);
    }
  }, [
    messages.length,
    streaming?.cancelError,
    streaming?.cancelling,
    streaming?.cancelled,
    streaming?.content,
    streaming?.dispatchReceipt?.routedButler,
    streaming?.phase?.name,
    streaming?.phase?.target,
    streaming?.phase?.tool,
    streaming?.pending,
    userScrolledUp,
    prefersReducedMotion,
    scrollToBottom,
  ]);

  // Unmount cleanup only -- the throttle's pending timer must survive across
  // the effect above's own re-runs (see comment there).
  useEffect(() => {
    return () => {
      if (scrollTimeoutRef.current !== null) clearTimeout(scrollTimeoutRef.current);
    };
  }, []);

  const isStreamingThisConversation =
    streaming !== null &&
    (streaming.conversationId === conversationId ||
      (streaming.conversationId === "pending" && conversationId === null));
  // Stop owns the one authoritative live status while it is settling or has
  // completed. Keeping receipt progress here would duplicate that live region
  // and retain a routing claim after the current turn has been terminated.
  const showPendingActivity =
    isStreamingThisConversation &&
    streaming.pending &&
    !streaming.cancelling &&
    !streaming.cancelled &&
    !streaming.interrupted;

  // Sentence-batched sr-only announcement of the reply as it streams in
  // (bu-0ynlk.13) — a screen reader hears complete sentences, not tokens.
  const liveAnnouncerSpans = useLiveAnnouncerSpans(isStreamingThisConversation, streaming);

  if (messages.length === 0 && !isStreamingThisConversation && !suppressEmptyState) {
    return (
      <div className="flex-1 flex items-center justify-center text-muted-foreground text-sm">
        No messages yet. Start the conversation below.
      </div>
    );
  }

  return (
    <>
      <div
        role="status"
        aria-live="polite"
        aria-atomic="false"
        className="sr-only"
        data-testid="chat-reply-live-region"
      >
        {liveAnnouncerSpans.map((span, i) => (
          <span key={i}>{span}</span>
        ))}
      </div>
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
              conversationId={conversationId}
              streamingContent={isStreamingTarget ? streaming.content : undefined}
              interrupted={isStreamingTarget ? streaming.interrupted : undefined}
              cancelled={isStreamingTarget ? streaming.cancelled : undefined}
              cancelError={isStreamingTarget ? streaming.cancelError : undefined}
            />
          );
        })}

        {/* The visible dots are decorative; the status text communicates progress. */}
        {showPendingActivity && (
          <div className="flex flex-col gap-1">
            <p
              className="text-xs text-muted-foreground"
              role="status"
              aria-live="polite"
              aria-atomic="true"
              data-testid="chat-activity-status"
            >
              {pendingActivityStatus(streaming)}
            </p>
            <TypingIndicator />
          </div>
        )}

        {/* Streaming assistant message (before it's committed to messages list) */}
        {isStreamingThisConversation &&
          !streaming.pending &&
          (messages.length === 0 || messages[messages.length - 1].role === "user") && (
            <div className="flex min-w-0 max-w-[85%] flex-col gap-1 self-start items-start">
              <div className="w-full min-w-0 max-w-full overflow-hidden rounded-2xl rounded-bl-sm bg-muted px-4 py-2.5">
                <AnswerBody content={streaming.content} />
                {streaming.cancelled ? (
                  <p className="text-muted-foreground text-xs mt-1 italic">Cancelled by owner</p>
                ) : (
                  streaming.interrupted && (
                    <p className="text-muted-foreground text-xs mt-1 italic">Interrupted</p>
                  )
                )}
                {streaming.cancelError && (
                  <p className="text-destructive text-xs mt-1">{streaming.cancelError}</p>
                )}
              </div>
            </div>
          )}

        <div ref={bottomRef} />
      </div>
    </>
  );
}
