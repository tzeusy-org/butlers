import type { QaCaseSummary } from "@/api/types";
import { cn } from "@/lib/utils";

import { formatQaDetectedTime, qaSeverityClassName } from "./utils";

interface CaseListProps {
  cases: QaCaseSummary[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  className?: string;
}

const prStateClass: Record<NonNullable<QaCaseSummary["pr_state"]>, string> = {
  drafted: "bg-muted-foreground",
  open: "bg-amber-500",
  merged: "bg-emerald-500",
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

export function CaseList({ cases, selectedId, onSelect, className }: CaseListProps) {
  return (
    <aside className={cn("w-full md:w-[320px]", className)} aria-label="QA cases">
      <div className="border-b border-border/60 pb-2 font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground tnum">
        Cases · last 7d
      </div>
      <div className="divide-y divide-border/60 border-b border-border/60">
        {cases.map((qaCase) => {
          const active = qaCase.id === selectedId;
          return (
            <button
              key={qaCase.id}
              type="button"
              onClick={() => onSelect(qaCase.id)}
              className={cn(
                "grid w-full grid-cols-[12px_1fr_14px] gap-3 border-l-2 border-transparent py-3 pl-3 pr-1 text-left transition-colors duration-fast hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
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
          );
        })}
      </div>
    </aside>
  );
}
