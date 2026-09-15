/**
 * Removable pre-send page-context chip (bu-0ynlk.4).
 *
 * Rendered above MessageInput's textarea by both chat surfaces (ChatPanel,
 * FloatingChatWidget) so the owner sees exactly what page context would be
 * attached to their next message — and can remove it for just that one
 * message before sending. `aria-describedby` on the textarea points at this
 * chip's `id` so screen readers announce what's attached while composing.
 *
 * Three render states, driven by `policy` (see `@/lib/page-context-registry`):
 *   - "none": static notice, no removal affordance — nothing is attached.
 *   - included: label + expandable exact payload; Backspace/Delete or the
 *     × button detaches (calls `onToggleIncluded`).
 *   - detached: a re-attach affordance; clicking (or the next send) restores it.
 */

import { useState, type KeyboardEvent } from "react";
import { ChevronRightIcon, ChevronDownIcon, XIcon } from "lucide-react";

import { cn } from "@/lib/utils";
import type { PageContext } from "@/api/types.ts";
import type { ContextPolicy } from "@/lib/page-context.tsx";

export interface ContextChipProps {
  /** DOM id — the composing textarea's `aria-describedby` points at this. */
  id: string;
  label: string;
  policy: ContextPolicy;
  /** The exact object that would be sent (null only when `policy === "none"`). */
  payload: PageContext | null;
  /** Whether this message will include `payload`. Ignored when `policy === "none"`. */
  included: boolean;
  onToggleIncluded: () => void;
}

export function ContextChip({
  id,
  label,
  policy,
  payload,
  included,
  onToggleIncluded,
}: ContextChipProps) {
  const [expanded, setExpanded] = useState(false);

  const baseClass = "flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs";

  if (policy === "none") {
    return (
      <div
        id={id}
        data-testid="context-chip"
        data-policy="none"
        className={cn(baseClass, "border-dashed text-muted-foreground")}
      >
        Context not attached on this page.
      </div>
    );
  }

  if (!included) {
    return (
      <button
        type="button"
        id={id}
        data-testid="context-chip"
        data-included="false"
        onClick={onToggleIncluded}
        className={cn(
          baseClass,
          "border-dashed text-muted-foreground hover:text-foreground hover:border-foreground/40",
        )}
      >
        Context removed, click to re-attach
      </button>
    );
  }

  function handleKeyDown(e: KeyboardEvent<HTMLButtonElement>) {
    if (e.key === "Backspace" || e.key === "Delete") {
      e.preventDefault();
      onToggleIncluded();
    }
  }

  return (
    <div
      id={id}
      data-testid="context-chip"
      data-included="true"
      className={cn(baseClass, "flex-col items-stretch border-input bg-muted/40")}
    >
      <div className="flex items-center gap-1.5">
        <button
          type="button"
          data-testid="context-chip-toggle"
          aria-expanded={expanded}
          aria-label={`Page context attached: ${label}. Press Backspace or Delete to remove.`}
          onClick={() => setExpanded((prev) => !prev)}
          onKeyDown={handleKeyDown}
          className="flex items-center gap-1 text-foreground/80 hover:text-foreground"
        >
          {expanded ? (
            <ChevronDownIcon className="size-3" />
          ) : (
            <ChevronRightIcon className="size-3" />
          )}
          <span>
            {label}
            {policy === "ref-only" ? " (reference only)" : ""}
          </span>
        </button>
        <button
          type="button"
          data-testid="context-chip-remove"
          aria-label="Remove page context from this message"
          onClick={onToggleIncluded}
          onKeyDown={handleKeyDown}
          className="ml-auto text-muted-foreground hover:text-foreground"
        >
          <XIcon className="size-3" />
        </button>
      </div>
      {expanded && (
        <pre
          data-testid="context-chip-payload"
          className="mt-1 overflow-x-auto rounded bg-background/60 p-1.5 font-mono text-[11px]"
        >
          {JSON.stringify(payload, null, 2)}
        </pre>
      )}
    </div>
  );
}
