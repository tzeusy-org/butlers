/**
 * Shared send-error banner (bu-o0ab2) — renders the classified `SendError`
 * produced by `./send-error-utils.ts` (see that module's header for the
 * offline/timeout/pending/generic contract). Used by both FloatingChatWidget.tsx and
 * ChatPanel.tsx so the two surfaces render send failures identically.
 */

import { ExternalLinkIcon, RefreshCwIcon, XIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { RetryableSendError, SendError } from "./send-error-utils.ts";

export interface SendErrorBannerProps {
  error: SendError;
  onRetry: (error: RetryableSendError) => void;
  onCheckAgain: () => void;
  onDismiss: () => void;
}

export function SendErrorBanner({ error, onRetry, onCheckAgain, onDismiss }: SendErrorBannerProps) {
  if (error.kind === "timeout" || error.kind === "ambiguous" || error.kind === "pending") {
    return (
      <div
        className="flex items-center justify-between gap-2 border-t bg-muted/40 px-3 py-2 text-xs"
        data-testid={
          error.kind === "timeout"
            ? "chat-widget-timeout-banner"
            : error.kind === "ambiguous"
              ? "chat-widget-ambiguous-banner"
              : "chat-widget-pending-banner"
        }
      >
        <span className="text-muted-foreground">{error.message}</span>
        <div className="flex shrink-0 items-center gap-2">
          {error.kind === "timeout" && error.sessionId && (
            <a
              href={`/sessions/${error.sessionId}`}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-primary underline-offset-2 hover:underline"
              data-testid="chat-widget-timeout-session-link"
            >
              Inspect session
              <ExternalLinkIcon className="size-3" />
            </a>
          )}
          <Button size="xs" variant="outline" onClick={onCheckAgain}>
            <RefreshCwIcon className="size-3" />
            Check again
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div
      className="flex items-center justify-between gap-2 border-t bg-muted/40 px-3 py-2 text-xs"
      role="alert"
      aria-atomic="true"
      data-testid="chat-widget-error-banner"
    >
      <span className="text-destructive">{error.message}</span>
      <div className="flex shrink-0 items-center gap-2">
        <Button size="xs" variant="outline" onClick={() => onRetry(error)}>
          Retry
        </Button>
        <button
          type="button"
          onClick={onDismiss}
          aria-label="Dismiss"
          className="text-muted-foreground hover:text-foreground"
        >
          <XIcon className="size-3" />
        </button>
      </div>
    </div>
  );
}
