import { Link } from "react-router";

import type { QaCaseSummary } from "@/api/types";
import { cn } from "@/lib/utils";

import { formatQaDetectedTime, qaSeverityClassName } from "./utils";

interface CaseListProps {
  cases: QaCaseSummary[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  className?: string;
  /** Header label reflecting the active time-range filter, e.g. "Cases · last 7d". */
  headerLabel?: string;
  /** True when the backend has more matching cases than this page returned. */
  hasMore?: boolean;
  /** Total matching case count, used to caption the truncation. */
  totalCount?: number;
}

// bu-86c4c.6: raw Tailwind shades -> Dispatch state tokens (background-fill
// on a small status dot, the accepted "dot" affordance exception).
const prStateClass: Record<NonNullable<QaCaseSummary["pr_state"]>, string> = {
  drafted: "bg-muted-foreground",
  open: "bg-[var(--amber)]",
  merged: "bg-[var(--green)]",
  closed: "bg-muted-foreground",
};

function formatAge(seconds: number): string {
  if (seconds < 60) return `${Math.max(0, Math.floor(seconds))}s old`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m old`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h old`;
  return `${Math.floor(hours / 24)}d old`;
}

function sessionTraceDoorForCase(
  qaCase: QaCaseSummary,
): { targetId: string; traceCount: number } | null {
  const traceIds = Array.from(
    new Set(
      [qaCase.healing_session_id, ...(qaCase.session_ids ?? [])].filter(
        (sessionId): sessionId is string => Boolean(sessionId),
      ),
    ),
  );
  const targetId = traceIds[0];

  if (!targetId) return null;

  return { targetId, traceCount: traceIds.length };
}

export function CaseList({
  cases,
  selectedId,
  onSelect,
  className,
  headerLabel = "Cases · last 7d",
  hasMore = false,
  totalCount,
}: CaseListProps) {
  return (
    <aside className={cn("w-full md:w-[320px]", className)} aria-label="QA cases">
      <div className="border-b border-border/60 pb-2 font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground tnum">
        {headerLabel}
      </div>
      <div className="divide-y divide-border/60 border-b border-border/60">
        {cases.map((qaCase) => {
          const active = qaCase.id === selectedId;
          const sessionTraceDoor = sessionTraceDoorForCase(qaCase);
          return (
            <div
              key={qaCase.id}
            >
              <button
                type="button"
                onClick={() => onSelect(qaCase.id)}
                className={cn(
                  "grid w-full grid-cols-[12px_1fr_14px] gap-3 border-l-2 border-transparent py-3 pl-3 pr-1 text-left transition-colors duration-fast hover:bg-muted/40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-fg",
                  active && "border-l-2 border-foreground bg-white/[0.04]",
                )}
                data-testid={`qa-case-row-${qaCase.id}`}
                aria-current={active ? "true" : undefined}
              >
                <span
                  className={cn("mt-1 h-2.5 w-2.5 shrink-0", qaSeverityClassName[qaCase.sev])}
                  aria-label={`${qaCase.sev} severity`}
                />
                <span className="min-w-0">
                  <span className="flex min-w-0 items-center gap-2">
                    <span className="font-mono text-[10px] text-foreground tnum">
                      {qaCase.short_id}
                    </span>
                    <span className="truncate font-mono text-[10px] text-muted-foreground">
                      {qaCase.butler}
                    </span>
                  </span>
                  <span className="mt-1 block truncate font-sans text-[12.5px] leading-tight text-foreground">
                    {qaCase.headline ?? "Untitled QA case"}
                  </span>
                  <span className="mt-1 block font-mono text-[9.5px] leading-none text-muted-foreground tnum">
                    detected {formatQaDetectedTime(qaCase.detected)} · {formatAge(qaCase.age_seconds)}
                    {qaCase.state === "failed" ? (
                      <span
                        className="text-destructive"
                        data-testid={`qa-case-row-failed-badge-${qaCase.id}`}
                      >
                        {" "}
                        · failed
                      </span>
                    ) : null}
                  </span>
                </span>
                <span
                  className={cn(
                    "mt-1.5 h-2 w-2 justify-self-end rounded-full",
                    qaCase.pr_state ? prStateClass[qaCase.pr_state] : "bg-border",
                  )}
                  aria-label={qaCase.pr_state ? `PR ${qaCase.pr_state}` : "No PR"}
                />
              </button>
              {sessionTraceDoor ? (
                <Link
                  to={`/sessions/${sessionTraceDoor.targetId}`}
                  aria-label={`Open ${sessionTraceDoor.traceCount} linked session trace${
                    sessionTraceDoor.traceCount === 1 ? "" : "s"
                  } for QA case ${qaCase.short_id}`}
                  data-testid={`qa-case-session-door-${qaCase.id}`}
                  className="mb-2 ml-7 inline-flex min-h-6 items-center gap-1 font-mono text-[9.5px] uppercase tracking-[0.12em] text-muted-foreground underline decoration-[var(--border-strong)] underline-offset-4 hover:text-foreground focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-fg"
                >
                  {sessionTraceDoor.traceCount} trace
                  {sessionTraceDoor.traceCount === 1 ? "" : "s"}
                  <span aria-hidden="true">→</span>
                </Link>
              ) : null}
            </div>
          );
        })}
      </div>
      {hasMore ? (
        <p
          className="pt-2 font-mono text-[9.5px] uppercase tracking-[0.14em] text-muted-foreground"
          data-testid="qa-case-list-truncation"
        >
          Showing {cases.length}
          {totalCount !== undefined ? ` of ${totalCount}` : ""}. Narrow filters to see more
        </p>
      ) : null}
    </aside>
  );
}
