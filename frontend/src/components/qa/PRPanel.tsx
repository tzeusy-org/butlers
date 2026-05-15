import type { QaPrSummary } from "@/api/types";
import { Time } from "@/components/ui/time";
import { cn } from "@/lib/utils";

import { DiffPreview, type DiffPreviewLine } from "./DiffPreview";

interface PRPanelProps {
  pr: QaPrSummary | null;
  whyThisFix: string | null;
  /**
   * The API keeps the diff snapshot on QaInvestigationNotes, not QaPrSummary,
   * so the panel accepts it as a separate optional dossier field.
   */
  diffSnapshot?: DiffPreviewLine[] | null;
  className?: string;
}

const prStateClassName: Record<QaPrSummary["state"], string> = {
  closed: "border-muted-foreground/40 text-muted-foreground",
  drafted: "border-sky-500/40 text-sky-500",
  merged: "border-emerald-500/40 text-emerald-500",
  open: "border-amber-500/40 text-amber-500",
};

export function PRPanel({ pr, whyThisFix, diffSnapshot, className }: PRPanelProps) {
  if (!pr) {
    return (
      <p className={cn("font-serif text-sm italic text-muted-foreground", className)}>
        No PR — escalated to user.
      </p>
    );
  }

  return (
    <section className={cn("space-y-4", className)} aria-label="Pull request fix">
      <div className="space-y-2 border-b border-border/60 pb-3">
        <div className="flex flex-wrap items-center gap-2">
          <span
            className={cn(
              "border px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.12em] tnum",
              prStateClassName[pr.state],
            )}
          >
            {pr.state}
          </span>
          <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground tnum">
            pr #{pr.number} · {pr.state}
          </p>
          <a
            href={pr.url}
            target="_blank"
            rel="noreferrer"
            className="ml-auto font-mono text-[10px] uppercase tracking-[0.12em] text-foreground underline-offset-4 hover:underline"
          >
            Open PR
          </a>
        </div>
        <h3 className="font-sans text-[14px] font-medium leading-tight tracking-normal text-foreground">
          {pr.title}
        </h3>
        <p className="font-mono text-[10px] leading-none text-muted-foreground tnum">
          {pr.branch} · ci {pr.ci_status} · +{pr.additions} / -{pr.deletions}
        </p>
      </div>

      {whyThisFix ? (
        <div className="space-y-1">
          <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
            Why this fix
          </p>
          <p className="font-serif italic text-[13px] leading-relaxed text-foreground">{whyThisFix}</p>
        </div>
      ) : null}

      {diffSnapshot && diffSnapshot.length > 0 ? (
        <div className="space-y-2">
          <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
            Diff preview
          </p>
          <DiffPreview lines={diffSnapshot} />
        </div>
      ) : null}

      <p className="font-mono text-[10px] leading-none text-muted-foreground tnum">
        opened <Time value={pr.opened_at} mode="absolute" precision="time" /> ·{" "}
        {pr.merged_at ? (
          <>
            merged <Time value={pr.merged_at} mode="absolute" precision="time" />
          </>
        ) : (
          "not merged"
        )}
      </p>
    </section>
  );
}
