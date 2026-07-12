import type { QaCaseDossier, QaPrSummary } from "@/api/types";
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
  /**
   * "escalated" means the case was handed to the user without a fix;
   * "failed" means the investigation crashed before ever reaching a fix.
   * Every other pr-less stage (detect/diagnose/pr/landed) just hasn't
   * produced a PR yet -- rendering either of those calmer messages for a
   * dead or escalated case fabricates progress that was never made
   * (bu-qvnce.2 / bu-hmdqz.9).
   */
  stage: QaCaseDossier["state_track_stage"];
  className?: string;
}

const prStateClassName: Record<QaPrSummary["state"], string> = {
  closed: "border-muted-foreground/40 text-muted-foreground",
  drafted: "border-sky-500/40 text-sky-500",
  // bu-86c4c.6: merged/open are real states (success/pending) -> Dispatch
  // tokens. --amber-text is the AA-contrast-safe text variant (bu-86c4c.16).
  merged: "border-[var(--green)]/40 text-[var(--green)]",
  open: "border-[var(--amber)]/40 text-[var(--amber-text)]",
};

export function PRPanel({ pr, whyThisFix, diffSnapshot, stage, className }: PRPanelProps) {
  if (!pr) {
    if (stage === "escalated") {
      return (
        <p className={cn("font-serif text-sm italic text-muted-foreground", className)}>
          No PR. Escalated to user.
        </p>
      );
    }
    if (stage === "failed") {
      return (
        <p className={cn("font-serif text-sm italic text-destructive", className)}>
          No PR. Investigation failed.
        </p>
      );
    }
    return (
      <p className={cn("font-serif text-sm italic text-muted-foreground", className)}>
        No PR yet.
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
          {pr.branch} · ci {pr.ci_status ?? "unavailable"}
          {pr.additions !== null && pr.deletions !== null
            ? ` · +${pr.additions} / -${pr.deletions}`
            : ""}
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
