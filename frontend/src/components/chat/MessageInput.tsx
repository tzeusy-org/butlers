/**
 * Message input area with auto-growing textarea, send and stop buttons.
 */

import { useRef, useEffect } from "react";
import { ArrowUpIcon, SquareIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

export interface MessageInputProps {
  value: string;
  onChange: (value: string) => void;
  onSend: () => void;
  onStop: () => void;
  disabled: boolean;
  /** True while an assistant response is streaming. */
  isStreaming: boolean;
  placeholder?: string;
}

export function MessageInput({
  value,
  onChange,
  onSend,
  onStop,
  disabled,
  isStreaming,
  placeholder = "Type a message...",
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

  return (
    <div className={cn("border-t bg-background p-3", "flex items-end gap-2")}>
      <Textarea
        ref={textareaRef}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder={placeholder}
        disabled={disabled || isStreaming}
        rows={1}
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
          className="shrink-0 size-9"
          onClick={onStop}
          title="Stop generation"
        >
          <SquareIcon className="size-4" />
        </Button>
      ) : (
        <Button
          type="button"
          variant="default"
          size="icon"
          className="shrink-0 size-9"
          disabled={!canSend}
          onClick={onSend}
          title="Send message"
        >
          <ArrowUpIcon className="size-4" />
        </Button>
      )}
    </div>
  );
}
