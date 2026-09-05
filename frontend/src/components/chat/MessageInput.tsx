/**
 * Message input area with auto-growing textarea, send and stop buttons.
 */

import { useRef, useEffect } from "react";
import { ArrowUpIcon, Loader2, SquareIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import { ContextChip, type ContextChipProps } from "./ContextChip.tsx";

const CONTEXT_CHIP_ID = "message-input-context-chip";

export interface MessageInputProps {
  value: string;
  onChange: (value: string) => void;
  onSend: () => void;
  onStop: () => void;
  disabled: boolean;
  /** True while an assistant response is streaming. */
  isStreaming: boolean;
  /** True while a Stop click's POST .../cancel call is in flight (bu-ep4ks.2) —
   * disables the button and shows a spinner so a second click can't race the
   * first cancel attempt. */
  stopPending?: boolean;
  /** False until the create/send response establishes an addressable durable turn. */
  stopAvailable?: boolean;
  /** Polite, non-visual progress/result announcement for the Stop control. */
  stopStatus?: string | null;
  placeholder?: string;
  /** Removable page-context chip shown above the textarea (bu-0ynlk.4). */
  contextChip?: Omit<ContextChipProps, "id"> | null;
}

export function MessageInput({
  value,
  onChange,
  onSend,
  onStop,
  disabled,
  isStreaming,
  stopPending = false,
  stopAvailable = true,
  stopStatus,
  placeholder = "Type a message...",
  contextChip,
}: MessageInputProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Auto-grow textarea up to 200px
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [value]);

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (!disabled && !isStreaming && value.trim()) {
        onSend();
      }
    }
  }

  const canSend = !disabled && !isStreaming && value.trim().length > 0;
  const canStop = stopAvailable && !stopPending;

  return (
    <div className={cn("border-t bg-background p-3", "flex flex-col gap-2")}>
      {stopStatus && (
        <div className="sr-only" role="status" aria-live="polite" aria-atomic="true">
          {stopStatus}
        </div>
      )}
      {contextChip && <ContextChip id={CONTEXT_CHIP_ID} {...contextChip} />}
      <div className="flex items-end gap-2">
        <Textarea
          ref={textareaRef}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={placeholder}
          disabled={disabled || isStreaming}
          rows={1}
          aria-describedby={contextChip ? CONTEXT_CHIP_ID : undefined}
          className={cn(
            "flex-1 resize-none min-h-[40px] max-h-[200px] overflow-y-auto",
            "rounded-xl border-input focus-visible:ring-1",
          )}
        />

        {isStreaming ? (
          <Button
            type="button"
            variant="outline"
            size="icon"
            className="shrink-0 size-11"
            onClick={onStop}
            disabled={!canStop}
            title={
              stopPending ? "Stopping…" : stopAvailable ? "Stop generation" : "Preparing stop control…"
            }
            aria-label={
              stopPending ? "Stopping this turn" : stopAvailable ? "Stop this turn" : "Preparing stop control"
            }
            data-testid="chat-stop-button"
          >
            {stopPending ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <SquareIcon className="size-4" />
            )}
          </Button>
        ) : (
          <Button
            type="button"
            variant="default"
            size="icon"
            className="shrink-0 size-11"
            disabled={!canSend}
            onClick={onSend}
            title="Send message"
            aria-label="Send message"
          >
            <ArrowUpIcon className="size-4" />
          </Button>
        )}
      </div>
    </div>
  );
}
