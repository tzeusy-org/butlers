/**
 * Dispatch presentational atoms local to the calendar workspace.
 *
 * The calendar is drawn in the Dispatch language: surfaces not cards, hairline
 * rules, hierarchy from type and rule, butler hue only on the letter-mark,
 * state color only when state demands. These pieces compose the shipped
 * Dispatch kit into the calendar-specific chrome.
 */

import { forwardRef, useState } from "react";
import { addDays, addHours, startOfDay } from "date-fns";
import { toast } from "sonner";

import type { UnifiedCalendarEntry } from "@/api/types.ts";
import { cn } from "@/lib/utils";
import {
  overlayAmountBadge,
  overlayButlerAccent,
  overlayKindGlyph,
  overlayMetadata,
} from "@/lib/calendar-overlays";
import { parseLocalDateTimeInput } from "@/pages/calendar/workspace-model";

/** Shared pill geometry per Design Language §4c (4px 10px / 1px border / 3px radius / mono 11px). */
const PILL_BASE =
  "inline-flex items-center justify-center gap-1.5 h-7 rounded-[3px] border px-2.5 " +
  "font-mono text-[11px] leading-none transition-colors " +
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus " +
  "disabled:pointer-events-none disabled:opacity-40";

/** Pill button (§4c). `active` inverts bg/fg for the selected state. Never colored. */
// forwardRef so `Tip`'s `asChild` (radix Slot) can attach its trigger ref and
// hover/focus handlers to the underlying <button> (bu-w40wg).
export const PillButton = forwardRef<
  HTMLButtonElement,
  React.ButtonHTMLAttributes<HTMLButtonElement> & { active?: boolean }
>(function PillButton({ active = false, className, ...props }, ref) {
  return (
    <button
      ref={ref}
      type="button"
      className={cn(
        PILL_BASE,
        active
          ? "bg-fg text-bg border-fg"
          : "bg-transparent text-[var(--mfg)] border-[var(--border-strong)] hover:text-fg",
        className,
      )}
      {...props}
    />
  );
});

PillButton.displayName = "PillButton";

/** Commit button (§4c) — fg background, bg text. At most one per surface. */
export function CommitButton({
  className,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      type="button"
      className={cn(
        PILL_BASE,
        "bg-fg text-bg border-fg hover:opacity-90",
        className,
      )}
      {...props}
    />
  );
}

/**
 * Inline snooze affordance for a due reminder / butler event. Clicking "Snooze"
 * reveals quick presets (+1h, +3h, tomorrow morning) plus a custom datetime so
 * the user can reschedule the item's due time without leaving the grid. The
 * chosen time is handed back as an ISO string; the parent fires the snooze
 * mutation.
 */
export function SnoozeMenu({
  disabled,
  onSnooze,
}: {
  disabled?: boolean;
  onSnooze: (iso: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [custom, setCustom] = useState("");

  function choose(date: Date) {
    onSnooze(date.toISOString());
    setOpen(false);
    setCustom("");
  }

  function chooseCustom() {
    const parsed = parseLocalDateTimeInput(custom);
    if (!parsed) {
      toast.error("Enter a valid snooze time");
      return;
    }
    choose(parsed);
  }

  const now = new Date();
  const tomorrowMorning = startOfDay(addDays(now, 1));
  tomorrowMorning.setHours(9, 0, 0, 0);
  const presets: Array<{ label: string; date: Date }> = [
    { label: "1 hour", date: addHours(now, 1) },
    { label: "3 hours", date: addHours(now, 3) },
    { label: "Tomorrow 9am", date: tomorrowMorning },
  ];

  return (
    <div className="relative inline-block">
      <PillButton
        data-testid="butler-snooze-button"
        onClick={() => setOpen((v) => !v)}
        disabled={disabled}
        active={open}
      >
        Snooze
      </PillButton>
      {open ? (
        <div
          data-testid="butler-snooze-menu"
          className="absolute right-0 z-20 mt-1 flex w-56 flex-col gap-2 rounded-[4px] border border-[var(--border-strong)] bg-bg p-2 shadow-md"
        >
          <div className="flex flex-wrap gap-1.5">
            {presets.map((preset) => (
              <PillButton
                key={preset.label}
                data-testid={`butler-snooze-preset-${preset.label}`}
                onClick={() => choose(preset.date)}
                disabled={disabled}
              >
                {preset.label}
              </PillButton>
            ))}
          </div>
          <div className="flex items-center gap-1.5">
            <input
              type="datetime-local"
              data-testid="butler-snooze-custom-input"
              value={custom}
              onChange={(e) => setCustom(e.target.value)}
              disabled={disabled}
              className="min-w-0 flex-1 rounded-[3px] border border-[var(--border-strong)] bg-transparent px-1.5 py-1 text-xs text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus disabled:opacity-50"
            />
            <CommitButton
              data-testid="butler-snooze-custom-confirm"
              onClick={chooseCustom}
              disabled={disabled || !custom.trim()}
            >
              Set
            </CommitButton>
          </div>
        </div>
      ) : null}
    </div>
  );
}

/** Mono uppercase kind tag (§4d) — labels a kind, never celebrates it. */
export function KindTag({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "font-mono text-[10px] uppercase tracking-[0.08em] leading-none text-[var(--mfg)]",
        className,
      )}
    >
      {children}
    </span>
  );
}

/**
 * A single read-only cross-domain overlay pill (finance bill/renewal, travel
 * leg, relationship date, health appointment). Structured only — title + a
 * kind glyph + an optional structured amount badge, all drawn verbatim from the
 * projected entry's `metadata`. Non-interactive context, so it renders as a
 * static chip, not a button.
 */
export function OverlayPill({ entry }: { entry: UnifiedCalendarEntry }) {
  const md = overlayMetadata(entry);
  const badge = overlayAmountBadge(md.meta);
  const accent = overlayButlerAccent(md.source_butler);
  const title = `${md.source_butler ?? "overlay"} · ${md.kind || "context"}${
    md.priority ? ` (${md.priority})` : ""
  }: ${entry.title}${badge ? ` (${badge})` : ""}`;
  return (
    <div
      data-overlay-entry-id={entry.entry_id}
      data-overlay-kind={md.kind}
      data-overlay-butler={md.source_butler ?? ""}
      title={title}
      className={cn(
        "pointer-events-auto flex w-full items-center gap-1 truncate rounded-[2px] border border-dashed bg-foreground/[0.02] px-1 py-0.5 text-left text-[10px] leading-none",
        accent,
      )}
    >
      <span aria-hidden className="shrink-0 font-mono">
        {overlayKindGlyph(md.kind)}
      </span>
      <span className="truncate text-fg">{entry.title}</span>
      {badge ? (
        <span className="ml-auto shrink-0 font-mono tabular-nums text-[var(--mfg)]">
          {badge}
        </span>
      ) : null}
    </div>
  );
}
