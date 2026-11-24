/**
 * Conversation metadata controls shared by the list row and the shared
 * conversation header (REQ-dashboard-chat-ui-017): the per-conversation
 * action menu and the inline rename editor.
 */

import type { Ref } from "react";
import { useEffect, useId, useRef, useState } from "react";
import { MoreHorizontalIcon } from "lucide-react";

import type { ConversationSummary } from "@/api/types.ts";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";
import {
  conversationTitle,
  useConversationMetadata,
  useConversationMetadataPending,
  validateConversationTitle,
} from "@/hooks/use-conversation-metadata.ts";
import { TITLE_EDITOR_ATTRIBUTE } from "./title-editor-escape.ts";

// ---------------------------------------------------------------------------
// ConversationActionsMenu
// ---------------------------------------------------------------------------

export interface ConversationActionsMenuProps {
  conversation: ConversationSummary;
  /** Open this surface's rename editor for `conversation`. */
  onRename: () => void;
  /** Called after an archive is issued (e.g. to move focus off a leaving row). */
  onArchive?: () => void;
  triggerRef?: Ref<HTMLButtonElement>;
  className?: string;
}

export function ConversationActionsMenu({
  conversation,
  onRename,
  onArchive,
  triggerRef,
  className,
}: ConversationActionsMenuProps) {
  const { archive, unarchive } = useConversationMetadata();
  const pending = useConversationMetadataPending(conversation.id);
  // Where focus goes once the menu closes. `undefined` keeps the default
  // return to the trigger; the rename editor takes focus itself (`null`), and
  // an archive may move focus off a row that is about to leave the listing.
  const afterClose = useRef<(() => void) | null | undefined>(undefined);
  const archived = conversation.status === "archived";

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          ref={triggerRef}
          variant="ghost"
          size="icon-sm"
          className={cn("shrink-0 text-muted-foreground hover:text-foreground", className)}
          aria-label={`Actions for ${conversationTitle(conversation)} (${conversation.butler_name})`}
          aria-busy={pending || undefined}
        >
          <MoreHorizontalIcon />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="end"
        onCloseAutoFocus={(event) => {
          if (afterClose.current === undefined) return;
          event.preventDefault();
          afterClose.current?.();
          afterClose.current = undefined;
        }}
      >
        <DropdownMenuItem
          disabled={pending}
          onSelect={() => {
            afterClose.current = null;
            onRename();
          }}
        >
          Rename conversation
        </DropdownMenuItem>
        {archived ? (
          <DropdownMenuItem disabled={pending} onSelect={() => void unarchive(conversation)}>
            Unarchive conversation
          </DropdownMenuItem>
        ) : (
          <DropdownMenuItem
            disabled={pending}
            onSelect={() => {
              afterClose.current = onArchive;
              void archive(conversation);
            }}
          >
            Archive conversation
          </DropdownMenuItem>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

// ---------------------------------------------------------------------------
// ConversationTitleEditor
// ---------------------------------------------------------------------------

export interface ConversationTitleEditorProps {
  initialTitle: string;
  /** Accessible name of the input, naming the conversation being renamed. */
  label: string;
  /** Called with a validated, trimmed title. */
  onCommit: (title: string) => void;
  onCancel: () => void;
  /** Error to show on open, e.g. a rename that was not confirmed. */
  initialError?: string | null;
  className?: string;
}

export function ConversationTitleEditor({
  initialTitle,
  label,
  onCommit,
  onCancel,
  initialError = null,
  className,
}: ConversationTitleEditorProps) {
  const [value, setValue] = useState(initialTitle);
  const [error, setError] = useState<string | null>(initialError);
  // The shared Input does not forward refs (React 18), so reach it by id.
  const inputId = useId();
  const errorId = useId();
  const focusInput = (select = false) => {
    const input = document.getElementById(inputId);
    if (!(input instanceof HTMLInputElement)) return;
    input.focus();
    if (select) input.select();
  };
  useEffect(() => {
    focusInput(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- focus once on open
  }, []);

  function commit() {
    const result = validateConversationTitle(value);
    if (!result.ok) {
      setError(result.error);
      focusInput();
      return;
    }
    onCommit(result.title);
  }

  return (
    <form
      className={cn("flex flex-col gap-1 min-w-0", className)}
      onSubmit={(event) => {
        event.preventDefault();
        commit();
      }}
    >
      <Input
        id={inputId}
        value={value}
        aria-label={label}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? errorId : undefined}
        className="h-7 text-sm"
        onChange={(event) => {
          setValue(event.target.value);
          if (error) setError(null);
        }}
        {...{ [TITLE_EDITOR_ATTRIBUTE]: "" }}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            // Escape belongs to the editor, not a containing chat surface.
            event.preventDefault();
            event.stopPropagation();
            if (!event.nativeEvent.isComposing) onCancel();
            return;
          }
          if (event.key !== "Enter") return;
          // Never let Enter reach a page action or message send; during IME
          // composition it only confirms the composed text.
          event.preventDefault();
          event.stopPropagation();
          if (event.nativeEvent.isComposing || event.keyCode === 229) return;
          commit();
        }}
      />
      {error && (
        <p id={errorId} role="alert" className="text-xs text-destructive">
          {error}
        </p>
      )}
      <div className="flex gap-1">
        <Button type="submit" size="xs">
          Save
        </Button>
        <Button type="button" size="xs" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  );
}
