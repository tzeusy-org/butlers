/**
 * Conflict & overcommitment radar banner (bu-q8o90x).
 *
 * A quiet banner shown atop the week/day grid when the conflicts scan
 * (`GET /api/calendar/workspace/conflicts`) finds issues in the visible window.
 * Collapsed, it shows a one-liner summarising issues by day; expanded, it lists
 * per-issue cards with the contributing event titles and, for an overlap, the
 * resolution options the scan compiled (bu-5ii4gj.11). Digits 1-9 pick the nth option of
 * the first overlap that has options.
 *
 * Honest degraded mode: when the scan could not run (`available === false`) the
 * banner renders nothing at all (silent), never a misleading "all clear". It also renders
 * nothing on a clean window. "Keep both" is an option like any other: it is recorded on the
 * server, so it survives a reload and goes quiet until one of the two events changes.
 */

import { useMemo, useState } from "react";
import { format, parseISO } from "date-fns";

import type { ConflictIssue, ResolutionOption } from "@/api/types.ts";
import { FetchingDim } from "@/components/ui/fetching-dim";
import { useRegisterShortcut, type ShortcutBinding } from "@/hooks/use-register-shortcut";
import { cn } from "@/lib/utils.ts";
import { optionLabel } from "./conflict-option-label.ts";

export interface ConflictRadarBannerProps {
  issues: ConflictIssue[];
  /** `false` ⇒ degraded scan; the banner stays silent (renders nothing). */
  available: boolean;
  /** Whether retained issues are refreshing for a new visible window. */
  isFetching?: boolean;
  /** Whether the query failed; this always takes precedence over stale issues. */
  isError?: boolean;
  /** Query error detail shown when the scan could not complete. */
  error?: Error | null;
  /** Carry out one offered option of an overlap (the page binds the scan window and digest). */
  onResolve?: (issue: ConflictIssue, option: ResolutionOption) => void;
  /** Whether a resolve is in flight. */
  isResolving?: boolean;
  className?: string;
}

const KIND_LABEL: Record<ConflictIssue["kind"], string> = {
  overlap: "overlap",
  back_to_back: "back-to-back run",
  overloaded_day: "overloaded day",
  outside_declared_hours: "outside your meeting hours",
};

/** Pill geometry shared with the calendar proposal controls (Design Language §4c). */
const PILL =
  "inline-flex items-center justify-center gap-1.5 h-7 rounded-[3px] border px-2.5 " +
  "font-mono text-[11px] leading-none transition-colors " +
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus " +
  "disabled:pointer-events-none disabled:opacity-40";

/** Pluralise a count + noun: `(2, "overlap") => "2 overlaps"`. */
function plural(count: number, noun: string): string {
  return `${count} ${noun}${count === 1 ? "" : "s"}`;
}

/** A short, human label for a YYYY-MM-DD date string (falls back to raw). */
function formatDay(date: string): string {
  try {
    return format(parseISO(date), "EEE MMM d");
  } catch {
    return date;
  }
}

/** Build the collapsed one-liner: per-day counts of each issue kind. */
function summariseByDay(issues: ConflictIssue[]): string {
  const byDay = new Map<string, ConflictIssue[]>();
  for (const issue of issues) {
    const list = byDay.get(issue.date) ?? [];
    list.push(issue);
    byDay.set(issue.date, list);
  }
  const parts: string[] = [];
  for (const date of [...byDay.keys()].sort()) {
    const dayIssues = byDay.get(date) ?? [];
    const overlaps = dayIssues.filter((i) => i.kind === "overlap").length;
    const dense = dayIssues.filter((i) => i.kind === "back_to_back").length;
    const overloaded = dayIssues.filter((i) => i.kind === "overloaded_day").length;
    // An info-level flag (answered, one-off) stays on its entry and out of the day headline.
    const outside = dayIssues.filter(
      (i) => i.kind === "outside_declared_hours" && i.severity === "warning",
    ).length;
    const bits: string[] = [];
    if (overlaps) bits.push(plural(overlaps, "overlap"));
    if (dense) bits.push(plural(dense, "back-to-back run"));
    if (overloaded) bits.push("a packed day");
    if (outside) bits.push(`${plural(outside, "event")} outside your meeting hours`);
    if (bits.length) parts.push(`${formatDay(date)} has ${bits.join(" · ")}`);
  }
  return parts.join("  ·  ");
}

function IssueCard({
  issue,
  firstShortcut,
  onResolve,
  isResolving,
}: {
  issue: ConflictIssue;
  /** The digit of this issue's first option, or null when it carries no shortcuts. */
  firstShortcut: number | null;
  onResolve?: (issue: ConflictIssue, option: ResolutionOption) => void;
  isResolving: boolean;
}) {
  const options = issue.options ?? [];
  return (
    <li
      className={cn(
        "rounded-md border px-3 py-2 text-sm",
        issue.severity === "warning"
          ? "border-[var(--amber)]/50 bg-[var(--amber)]/5"
          : "border-border bg-muted",
      )}
      data-kind={issue.kind}
      data-severity={issue.severity}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="font-medium text-fg">
          {formatDay(issue.date)} · {KIND_LABEL[issue.kind]}
        </span>
        <span className="shrink-0 text-xs uppercase tracking-wide text-muted-foreground">
          {issue.severity}
        </span>
      </div>
      <p className="mt-0.5 text-muted-foreground">{issue.summary}</p>
      {issue.events.length > 0 && (
        <ul className="mt-1 flex flex-wrap gap-1">
          {issue.events.map((event) => (
            <li
              key={event.entry_id}
              className="rounded bg-bg px-1.5 py-0.5 text-xs text-fg"
            >
              {event.title}
            </li>
          ))}
        </ul>
      )}
      {options.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-2">
          {options.map((option, index) => {
            const digit = firstShortcut === null ? null : firstShortcut + index;
            return (
              <button
                key={option.id}
                type="button"
                disabled={isResolving}
                data-option-kind={option.kind}
                aria-keyshortcuts={digit !== null && digit <= 9 ? String(digit) : undefined}
                className={cn(
                  PILL,
                  option.kind === "keep_both"
                    ? "border-[var(--border-strong)] text-[var(--mfg)] hover:text-fg"
                    : "border-fg bg-fg text-bg hover:opacity-90",
                )}
                onClick={() => onResolve?.(issue, option)}
              >
                {digit !== null && digit <= 9 ? `${digit} · ` : ""}
                {optionLabel(issue, option)}
              </button>
            );
          })}
        </div>
      )}
    </li>
  );
}

export function ConflictRadarBanner({
  issues,
  available,
  isFetching = false,
  isError = false,
  error = null,
  onResolve,
  isResolving = false,
  className,
}: ConflictRadarBannerProps) {
  const [expanded, setExpanded] = useState(false);

  const summary = useMemo(() => summariseByDay(issues), [issues]);
  // Digits 1-9 act on the first overlap that has options; its siblings carry no shortcuts.
  const shortcutIssue = useMemo(
    () => issues.find((i) => (i.options ?? []).length > 0) ?? null,
    [issues],
  );

  // The one shared registry owns the editable-field and dialog suspension.
  const shortcuts = useMemo<ShortcutBinding[]>(() => {
    if (!expanded || !shortcutIssue || !onResolve || isResolving) return [];
    return (shortcutIssue.options ?? []).slice(0, 9).map((option, index) => ({
      key: String(index + 1),
      display: [String(index + 1)],
      description: `Radar: ${optionLabel(shortcutIssue, option)}`,
      handler: () => onResolve(shortcutIssue, option),
    }));
  }, [expanded, shortcutIssue, onResolve, isResolving]);
  useRegisterShortcut(shortcuts);

  if (isError) {
    return (
      <div role="alert" className={cn("flex items-start gap-2 py-1", className)}>
        <span className="mt-[2px] font-mono text-[11px] text-[var(--red-text)]">●</span>
        <p className="text-sm text-fg">
          Failed to scan calendar conflicts. {error?.message ?? "Unknown error"}
        </p>
      </div>
    );
  }

  // Silent degraded mode and clean windows render nothing.
  if (!available || issues.length === 0) return null;

  const warningCount = issues.filter((i) => i.severity === "warning").length;

  const content = (
    <div
      role="status"
      aria-label="Calendar conflict radar"
      data-testid="conflict-radar-banner"
      className={cn(
        "rounded-lg border border-[var(--amber)]/40 bg-[var(--amber)]/5 px-3 py-2 text-sm",
        className,
      )}
    >
      <div className="flex items-center gap-2">
        <span aria-hidden className="text-[var(--amber-text)]">
          ⚠
        </span>
        <button
          type="button"
          className="flex-1 text-left font-medium text-fg"
          aria-expanded={expanded}
          onClick={() => setExpanded((v) => !v)}
        >
          {summary || `${plural(issues.length, "scheduling issue")} ahead`}
        </button>
        {warningCount > 0 && (
          <span className="shrink-0 rounded-full bg-[var(--amber)]/20 px-2 py-0.5 text-xs font-medium text-[var(--amber-text)]">
            {warningCount} to review
          </span>
        )}
        <button
          type="button"
          className="shrink-0 rounded px-1.5 py-0.5 text-xs text-muted-foreground hover:bg-muted"
          onClick={() => setExpanded((v) => !v)}
        >
          {expanded ? "Hide" : "Review"}
        </button>
      </div>
      {expanded && (
        <ul className="mt-2 flex flex-col gap-1.5">
          {issues.map((issue, index) => (
            <IssueCard
              key={`${issue.kind}-${issue.date}-${index}`}
              issue={issue}
              firstShortcut={issue === shortcutIssue ? 1 : null}
              onResolve={onResolve}
              isResolving={isResolving}
            />
          ))}
        </ul>
      )}
    </div>
  );

  return <FetchingDim isFetching={isFetching}>{content}</FetchingDim>;
}
