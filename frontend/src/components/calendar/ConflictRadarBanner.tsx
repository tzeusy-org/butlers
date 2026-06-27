/**
 * Conflict & overcommitment radar banner (bu-q8o90x).
 *
 * A quiet banner shown atop the week/day grid when the conflicts scan
 * (`GET /api/calendar/workspace/conflicts`) finds issues in the visible window.
 * Collapsed, it shows a one-liner summarising issues by day; expanded, it lists
 * per-issue cards with the contributing event titles and — when a `pending` fix
 * proposal exists — Accept / Decline actions backed by the existing proposals
 * surface.
 *
 * Honest degraded mode: when the scan could not run (`available === false`) the
 * banner renders nothing at all (silent), never a misleading "all clear". It
 * also renders nothing on a clean window (no issues). A dismiss control hides it
 * for the current page session (client-only; reappears on reload).
 */

import { useMemo, useState } from "react";
import { format, parseISO } from "date-fns";

import type { ConflictIssue } from "@/api/types.ts";
import { cn } from "@/lib/utils.ts";

export interface ConflictRadarBannerProps {
  issues: ConflictIssue[];
  /** `false` ⇒ degraded scan; the banner stays silent (renders nothing). */
  available: boolean;
  /** Accept a pending fix proposal (existing proposals accept surface). */
  onAcceptProposal?: (proposalId: string) => void;
  /** Decline a pending fix proposal (existing proposals dismiss surface). */
  onDismissProposal?: (proposalId: string) => void;
  className?: string;
}

const KIND_LABEL: Record<ConflictIssue["kind"], string> = {
  overlap: "overlap",
  back_to_back: "back-to-back run",
  overloaded_day: "overloaded day",
};

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
    const bits: string[] = [];
    if (overlaps) bits.push(plural(overlaps, "overlap"));
    if (dense) bits.push(plural(dense, "back-to-back run"));
    if (overloaded) bits.push("a packed day");
    if (bits.length) parts.push(`${formatDay(date)} has ${bits.join(" · ")}`);
  }
  return parts.join("  ·  ");
}

function IssueCard({
  issue,
  onAcceptProposal,
  onDismissProposal,
}: {
  issue: ConflictIssue;
  onAcceptProposal?: (proposalId: string) => void;
  onDismissProposal?: (proposalId: string) => void;
}) {
  const hasFix = issue.proposal_ids.length > 0;
  return (
    <li
      className={cn(
        "rounded-md border px-3 py-2 text-sm",
        issue.severity === "warning"
          ? "border-amber-500/50 bg-amber-500/5"
          : "border-[var(--border)] bg-[var(--bg-subtle)]",
      )}
      data-kind={issue.kind}
      data-severity={issue.severity}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="font-medium text-[var(--fg)]">
          {formatDay(issue.date)} · {KIND_LABEL[issue.kind]}
        </span>
        <span className="shrink-0 text-xs uppercase tracking-wide text-[var(--fg-muted)]">
          {issue.severity}
        </span>
      </div>
      <p className="mt-0.5 text-[var(--fg-muted)]">{issue.summary}</p>
      {issue.events.length > 0 && (
        <ul className="mt-1 flex flex-wrap gap-1">
          {issue.events.map((event) => (
            <li
              key={event.entry_id}
              className="rounded bg-[var(--bg)] px-1.5 py-0.5 text-xs text-[var(--fg)]"
            >
              {event.title}
            </li>
          ))}
        </ul>
      )}
      {hasFix ? (
        <div className="mt-2 flex gap-2">
          {issue.proposal_ids.map((proposalId) => (
            <span key={proposalId} className="flex gap-1">
              <button
                type="button"
                className="rounded bg-emerald-600 px-2 py-0.5 text-xs font-medium text-white hover:bg-emerald-700"
                onClick={() => onAcceptProposal?.(proposalId)}
              >
                Accept fix
              </button>
              <button
                type="button"
                className="rounded border border-[var(--border)] px-2 py-0.5 text-xs text-[var(--fg)] hover:bg-[var(--bg-subtle)]"
                onClick={() => onDismissProposal?.(proposalId)}
              >
                Decline
              </button>
            </span>
          ))}
        </div>
      ) : (
        <p className="mt-1 text-xs italic text-[var(--fg-muted)]">
          No suggested fix yet. The radar will propose one shortly.
        </p>
      )}
    </li>
  );
}

export function ConflictRadarBanner({
  issues,
  available,
  onAcceptProposal,
  onDismissProposal,
  className,
}: ConflictRadarBannerProps) {
  const [expanded, setExpanded] = useState(false);
  const [dismissed, setDismissed] = useState(false);

  const summary = useMemo(() => summariseByDay(issues), [issues]);

  // Silent degraded mode and clean windows render nothing; a session dismiss
  // hides the banner until the page reloads.
  if (!available || issues.length === 0 || dismissed) return null;

  const warningCount = issues.filter((i) => i.severity === "warning").length;

  return (
    <div
      role="status"
      aria-label="Calendar conflict radar"
      data-testid="conflict-radar-banner"
      className={cn(
        "rounded-lg border border-amber-500/40 bg-amber-500/5 px-3 py-2 text-sm",
        className,
      )}
    >
      <div className="flex items-center gap-2">
        <span aria-hidden className="text-amber-500">
          ⚠
        </span>
        <button
          type="button"
          className="flex-1 text-left font-medium text-[var(--fg)]"
          aria-expanded={expanded}
          onClick={() => setExpanded((v) => !v)}
        >
          {summary || `${plural(issues.length, "scheduling issue")} ahead`}
        </button>
        {warningCount > 0 && (
          <span className="shrink-0 rounded-full bg-amber-500/20 px-2 py-0.5 text-xs font-medium text-amber-700 dark:text-amber-300">
            {warningCount} to review
          </span>
        )}
        <button
          type="button"
          className="shrink-0 rounded px-1.5 py-0.5 text-xs text-[var(--fg-muted)] hover:bg-[var(--bg-subtle)]"
          onClick={() => setExpanded((v) => !v)}
        >
          {expanded ? "Hide" : "Review"}
        </button>
        <button
          type="button"
          aria-label="Dismiss conflict radar"
          className="shrink-0 rounded px-1.5 py-0.5 text-xs text-[var(--fg-muted)] hover:bg-[var(--bg-subtle)]"
          onClick={() => setDismissed(true)}
        >
          ✕
        </button>
      </div>
      {expanded && (
        <ul className="mt-2 flex flex-col gap-1.5">
          {issues.map((issue, index) => (
            <IssueCard
              key={`${issue.kind}-${issue.date}-${index}`}
              issue={issue}
              onAcceptProposal={onAcceptProposal}
              onDismissProposal={onDismissProposal}
            />
          ))}
        </ul>
      )}
    </div>
  );
}
