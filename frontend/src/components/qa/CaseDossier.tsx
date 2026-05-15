import { useMemo, useState, type ReactNode } from "react";

import { cn } from "@/lib/utils";
import { useQaCase, useQaCaseJournal } from "@/hooks/use-qa";

import { CaseDossierHeader } from "./CaseDossierHeader";
import { ClaimAnchoredBlurb } from "./ClaimAnchoredBlurb";
import { getClaimOrderFromSegments } from "./claimOrder";
import { CounterEvidence } from "./CounterEvidence";
import { EvidenceLog } from "./EvidenceLog";
import { PatrolJournal } from "./PatrolJournal";
import { PRPanel } from "./PRPanel";

interface CaseDossierProps {
  caseId: string | undefined;
  patrolIntervalMinutes?: number;
  className?: string;
}

function DossierEyebrow({ children }: { children: ReactNode }) {
  return (
    <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
      {children}
    </p>
  );
}

export function CaseDossier({
  caseId,
  patrolIntervalMinutes = 10,
  className,
}: CaseDossierProps) {
  const caseQuery = useQaCase(caseId);
  const journalQuery = useQaCaseJournal(caseId, { limit: 50 });
  const [hoveredClaim, setHoveredClaim] = useState<string[] | null>(null);

  const dossier = caseQuery.data?.data;
  const notes = dossier?.investigation_notes ?? null;
  const journalEvents = journalQuery.data?.data ?? dossier?.journal ?? [];

  const claimOrder = useMemo(
    () => (notes ? getClaimOrderFromSegments(notes.blurb_segments) : []),
    [notes],
  );

  if (!caseId) {
    return (
      <p className={cn("font-serif text-sm italic text-muted-foreground", className)}>
        Select a QA case to inspect the dossier.
      </p>
    );
  }

  if (caseQuery.isLoading) {
    return (
      <p className={cn("font-serif text-sm italic text-muted-foreground", className)}>
        Loading QA dossier…
      </p>
    );
  }

  if (caseQuery.isError || !dossier) {
    return (
      <p className={cn("font-serif text-sm italic text-destructive", className)}>
        QA dossier unavailable.
      </p>
    );
  }

  return (
    <article
      className={cn("space-y-8", className)}
      data-testid="qa-case-dossier"
    >
      <CaseDossierHeader
        case={dossier.case}
        stage={dossier.state_track_stage}
        dismissal={dossier.dismissal}
      />

      <div className="grid gap-8 lg:grid-cols-[minmax(0,1.2fr)_minmax(320px,0.8fr)]">
        <section className="space-y-6" aria-label="Diagnosis">
          {notes ? (
            <>
              <div className="space-y-2">
                <DossierEyebrow>Diagnosis</DossierEyebrow>
                <ClaimAnchoredBlurb
                  segments={notes.blurb_segments}
                  claims={notes.claims}
                  claimOrder={claimOrder}
                  hoveredClaim={hoveredClaim}
                  onClaimHover={setHoveredClaim}
                />
              </div>

              <div className="space-y-2">
                <DossierEyebrow>Hypothesis</DossierEyebrow>
                <p className="font-mono text-[11px] leading-relaxed text-foreground tnum">
                  {notes.hypothesis}
                </p>
              </div>

              <div className="space-y-2">
                <DossierEyebrow>Evidence · log fragments</DossierEyebrow>
                <EvidenceLog
                  evidence={notes.evidence_lines}
                  claims={notes.claims}
                  claimOrder={claimOrder}
                  hoveredClaim={hoveredClaim}
                  onRowHover={setHoveredClaim}
                />
              </div>

              <div className="space-y-2">
                <DossierEyebrow>Considered & ruled out</DossierEyebrow>
                <CounterEvidence items={notes.counter_evidence} />
              </div>
            </>
          ) : (
            <div className="space-y-2">
              <DossierEyebrow>Diagnosis</DossierEyebrow>
              <p className="font-serif text-[17px] italic leading-8 text-muted-foreground">
                Diagnosing…
              </p>
              <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
                Investigation notes have not been emitted yet.
              </p>
            </div>
          )}
        </section>

        <section className="space-y-3" aria-label="Proposed fix">
          <DossierEyebrow>Proposed fix</DossierEyebrow>
          <PRPanel
            pr={dossier.pr}
            whyThisFix={notes?.why_this_fix ?? null}
            diffSnapshot={notes?.diff_snapshot ?? null}
          />
        </section>
      </div>

      <PatrolJournal
        events={journalEvents}
        patrolIntervalMinutes={patrolIntervalMinutes}
      />
    </article>
  );
}
