/**
 * Natural-language calendar quick-add bar (parse-then-confirm).
 *
 * Type a phrase like "lunch with Sarah Fri 1pm at Tartine" and press Enter:
 * the text is parsed (server-side, LLM, parse-only) into a draft event shown as
 * an editable preview chip. Confirming dispatches the draft through the caller's
 * `onConfirm` — which routes through the NORMAL user-event create path with a
 * fresh `request_id`. Nothing is written until the user confirms; an
 * accidental misparse never lands a real event.
 */

import { useState } from "react";

import { useParseCalendarQuickAdd } from "@/hooks/use-calendar-workspace";
import type { QuickAddDraft } from "@/api/types.ts";
import { Input } from "@/components/ui/input";
import { Eyebrow } from "@/components/ui/Eyebrow";
import { cn } from "@/lib/utils";

const PILL =
  "inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-full border px-3 py-1 text-[11px] font-medium transition-colors disabled:pointer-events-none disabled:opacity-50";

interface QuickAddBarProps {
  /** IANA timezone used to anchor relative phrases ("Fri 1pm"). */
  timezone: string;
  /** Butler whose catalog model overrides apply for parse resolution. */
  butlerName?: string;
  /** Whether the quick-add surface is usable (e.g. a writable calendar exists). */
  disabled?: boolean;
  /**
   * Confirm handler — receives the (possibly edited) draft. Implementations
   * MUST dispatch the standard create path with a fresh `request_id`. Resolves
   * when the create has been dispatched so the bar can reset.
   */
  onConfirm: (draft: QuickAddDraft) => Promise<void> | void;
}

/** Local, editable copy of a parsed draft (datetime fields kept as strings). */
type DraftEdits = QuickAddDraft;

export function QuickAddBar({ timezone, butlerName, disabled, onConfirm }: QuickAddBarProps) {
  const parseMutation = useParseCalendarQuickAdd();
  const [text, setText] = useState("");
  const [draft, setDraft] = useState<DraftEdits | null>(null);
  const [reason, setReason] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);

  const syncing = parseMutation.isPending;

  function reset() {
    setText("");
    setDraft(null);
    setReason(null);
    parseMutation.reset();
  }

  async function handleParse() {
    const phrase = text.trim();
    if (!phrase || syncing) return;
    setDraft(null);
    setReason(null);
    try {
      const response = await parseMutation.mutateAsync({
        text: phrase,
        timezone,
        butler_name: butlerName,
      });
      const data = response.data;
      if (data.parse_available && data.draft) {
        setDraft(data.draft);
      } else {
        setReason(data.reason ?? "The text could not be parsed into an event.");
      }
    } catch {
      setReason("Quick-add parsing failed. Try again or enter the event manually.");
    }
  }

  function patchDraft(patch: Partial<DraftEdits>) {
    setDraft((current) => (current ? { ...current, ...patch } : current));
  }

  async function handleConfirm() {
    if (!draft || confirming) return;
    setConfirming(true);
    try {
      await onConfirm(draft);
      reset();
    } finally {
      setConfirming(false);
    }
  }

  return (
    <div className="flex min-w-[16rem] flex-1 flex-col gap-2">
      <div className="flex items-center gap-2">
        <Eyebrow>Quick add</Eyebrow>
        <Input
          aria-label="Quick add event"
          placeholder="lunch with Sarah Fri 1pm at Tartine"
          value={text}
          disabled={disabled}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              void handleParse();
            }
          }}
          className="h-8 flex-1"
        />
        <button
          type="button"
          onClick={() => void handleParse()}
          disabled={disabled || syncing || text.trim().length === 0}
          className={cn(
            PILL,
            "bg-transparent text-[var(--mfg)] border-[var(--border-strong)] hover:text-[var(--fg)]",
          )}
        >
          {syncing ? "Parsing…" : "Parse"}
        </button>
      </div>

      {reason ? (
        <div
          role="status"
          className="flex items-start justify-between gap-2 rounded-[4px] border border-[var(--border)] px-2.5 py-1.5 text-[11px] text-[var(--mfg)]"
        >
          <span>{reason}</span>
          <button
            type="button"
            onClick={reset}
            className="shrink-0 underline decoration-dotted hover:text-[var(--fg)]"
          >
            Dismiss
          </button>
        </div>
      ) : null}

      {draft ? (
        <div
          role="group"
          aria-label="Parsed event preview"
          className="flex flex-col gap-2 rounded-[4px] border border-[var(--border-strong)] bg-[var(--bg)] px-2.5 py-2"
        >
          <label className="flex flex-col gap-1">
            <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--mfg)]">
              Title
            </span>
            <Input
              aria-label="Draft title"
              value={draft.title}
              onChange={(event) => patchDraft({ title: event.target.value })}
              className="h-8"
            />
          </label>
          <div className="flex flex-wrap gap-2">
            <label className="flex flex-1 flex-col gap-1">
              <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--mfg)]">
                Starts
              </span>
              <Input
                aria-label="Draft start"
                value={draft.start_at ?? ""}
                placeholder="ISO 8601"
                onChange={(event) => patchDraft({ start_at: event.target.value || null })}
                className="h-8"
              />
            </label>
            <label className="flex flex-1 flex-col gap-1">
              <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--mfg)]">
                Ends
              </span>
              <Input
                aria-label="Draft end"
                value={draft.end_at ?? ""}
                placeholder="ISO 8601"
                onChange={(event) => patchDraft({ end_at: event.target.value || null })}
                className="h-8"
              />
            </label>
          </div>
          <label className="flex flex-col gap-1">
            <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--mfg)]">
              Location
            </span>
            <Input
              aria-label="Draft location"
              value={draft.location ?? ""}
              onChange={(event) => patchDraft({ location: event.target.value || null })}
              className="h-8"
            />
          </label>
          <div className="flex items-center justify-end gap-2 pt-0.5">
            <button
              type="button"
              onClick={reset}
              disabled={confirming}
              className={cn(
                PILL,
                "bg-transparent text-[var(--mfg)] border-[var(--border-strong)] hover:text-[var(--fg)]",
              )}
            >
              Discard
            </button>
            <button
              type="button"
              onClick={() => void handleConfirm()}
              disabled={disabled || confirming || draft.title.trim().length === 0}
              className={cn(
                PILL,
                "bg-[var(--fg)] text-[var(--bg)] border-[var(--fg)] hover:opacity-90",
              )}
            >
              {confirming ? "Adding…" : "Confirm & add"}
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

export default QuickAddBar;
