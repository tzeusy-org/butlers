/**
 * Collapsible tool call details displayed below an assistant message.
 */

import { useState } from "react";
import { ChevronDownIcon, ChevronRightIcon, WrenchIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import type { MessageToolCall } from "@/api/types.ts";

// ---------------------------------------------------------------------------
// Single tool call entry
// ---------------------------------------------------------------------------

interface ToolCallEntryProps {
  toolCall: MessageToolCall;
}

function ToolCallEntry({ toolCall }: ToolCallEntryProps) {
  const [expanded, setExpanded] = useState(false);

  // Build a short argument summary (first 80 chars of stringified args)
  const argsSummary = (() => {
    try {
      const s = JSON.stringify(toolCall.arguments);
      return s.length > 80 ? `${s.slice(0, 80)}…` : s;
    } catch {
      return "";
    }
  })();

  return (
    <div className="border rounded-md text-xs">
      <button
        type="button"
        className="flex items-center gap-1.5 w-full px-2 py-1.5 text-left hover:bg-muted/50 transition-colors"
        onClick={() => setExpanded(!expanded)}
      >
        {expanded ? (
          <ChevronDownIcon className="size-3 shrink-0 text-muted-foreground" />
        ) : (
          <ChevronRightIcon className="size-3 shrink-0 text-muted-foreground" />
        )}
        <WrenchIcon className="size-3 shrink-0 text-muted-foreground" />
        <span className="font-mono font-medium">{toolCall.name}</span>
        {!expanded && argsSummary && (
          <span className="text-muted-foreground truncate ml-1">{argsSummary}</span>
        )}
      </button>

      {expanded && (
        <div className="border-t px-2 pb-2 space-y-1.5">
          <div>
            <p className="text-[10px] uppercase tracking-wide text-muted-foreground font-medium mt-1.5 mb-0.5">
              Arguments
            </p>
            <pre className="overflow-x-auto overflow-y-auto bg-muted/30 rounded p-1.5 text-[11px] max-h-32">
              {JSON.stringify(toolCall.arguments, null, 2)}
            </pre>
          </div>
          {toolCall.result !== undefined && toolCall.result !== null && (
            <div>
              <p className="text-[10px] uppercase tracking-wide text-muted-foreground font-medium mb-0.5">
                Result
              </p>
              <pre className="overflow-x-auto overflow-y-auto bg-muted/30 rounded p-1.5 text-[11px] max-h-32">
                {JSON.stringify(toolCall.result, null, 2)}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// ToolCallDetails (collapsible section)
// ---------------------------------------------------------------------------

interface ToolCallDetailsProps {
  toolCalls: MessageToolCall[];
}

export function ToolCallDetails({ toolCalls }: ToolCallDetailsProps) {
  const [open, setOpen] = useState(false);

  if (toolCalls.length === 0) return null;

  return (
    <div className="mt-2">
      <button
        type="button"
        className={cn(
          "flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors",
        )}
        onClick={() => setOpen(!open)}
      >
        {open ? (
          <ChevronDownIcon className="size-3" />
        ) : (
          <ChevronRightIcon className="size-3" />
        )}
        <WrenchIcon className="size-3" />
        <span>
          {toolCalls.length} tool call{toolCalls.length !== 1 ? "s" : ""}
        </span>
      </button>

      {open && (
        <div className="mt-1.5 space-y-1">
          {toolCalls.map((tc, i) => (
            <ToolCallEntry key={tc.id ?? i} toolCall={tc} />
          ))}
        </div>
      )}
    </div>
  );
}
