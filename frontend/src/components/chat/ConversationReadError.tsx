import { RefreshCwIcon } from "lucide-react";

import { Button } from "@/components/ui/button";

export interface ConversationReadErrorProps {
  label: string;
  onRetry: () => void;
  compact?: boolean;
}

/** A retryable read failure that must never be mistaken for an empty result. */
export function ConversationReadError({
  label,
  onRetry,
  compact = false,
}: ConversationReadErrorProps) {
  const message = `Could not load ${label}.`;

  if (compact) {
    return (
      <div className="flex justify-center p-1" role="alert" aria-atomic="true">
        <Button
          variant="ghost"
          size="icon"
          className="size-8 text-destructive"
          onClick={onRetry}
          aria-label={`${message} Try again.`}
          title={`${message} Try again.`}
        >
          <RefreshCwIcon className="size-3.5" />
        </Button>
      </div>
    );
  }

  return (
    <div
      className="flex items-center justify-between gap-2 border-t bg-muted/40 px-3 py-2 text-xs"
      role="alert"
      aria-atomic="true"
    >
      <span className="text-destructive">{message}</span>
      <Button size="xs" variant="outline" onClick={onRetry}>
        <RefreshCwIcon className="size-3" />
        Try again
      </Button>
    </div>
  );
}
