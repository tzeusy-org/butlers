import { useMemo } from "react";

import type { QaInvestigationNotes } from "@/api/types";
import { cn } from "@/lib/utils";

type ClaimMap = QaInvestigationNotes["claims"];
type EvidenceLine = QaInvestigationNotes["evidence_lines"][number];

interface EvidenceLogProps {
  evidence: EvidenceLine[];
  claims: ClaimMap;
  claimOrder?: string[];
  hoveredClaim: string[] | null;
  onRowHover: (claimIds: string[] | null) => void;
  className?: string;
}

const levelClassName: Record<string, string> = {
  ERROR: "text-destructive",
  WARN: "text-amber-500",
};

function levelClass(level: string): string {
  return levelClassName[level.toUpperCase()] ?? "text-muted-foreground";
}

export function EvidenceLog({
  evidence,
  claims,
  claimOrder,
  hoveredClaim,
  onRowHover,
  className,
}: EvidenceLogProps) {
  const claimIds = useMemo(() => {
    const ordered = claimOrder ?? Object.keys(claims);
    const seen = new Set<string>();
    const validOrdered = ordered.filter((claimId) => {
      if (seen.has(claimId) || !claims[claimId]) return false;
      seen.add(claimId);
      return true;
    });
    return [
      ...validOrdered,
      ...Object.keys(claims).filter((claimId) => !seen.has(claimId)),
    ];
  }, [claimOrder, claims]);

  const evidenceToClaims = useMemo(() => {
    const map = new Map<string, string[]>();
    claimIds.forEach((claimId) => {
      claims[claimId]?.evidence_ids.forEach((evidenceId) => {
        const current = map.get(evidenceId) ?? [];
        map.set(evidenceId, [...current, claimId]);
      });
    });
    return map;
  }, [claimIds, claims]);

  return (
    <div
      className={cn(
        "divide-y divide-border/60 border-y border-border/60 font-mono text-[10px]",
        className,
      )}
      role="log"
      aria-label="QA evidence log"
    >
      {evidence.map((row) => {
        const myClaims = evidenceToClaims.get(row.id) ?? [];
        const active =
          hoveredClaim !== null && myClaims.some((c) => hoveredClaim.includes(c));
        const claimLabel = myClaims
          .map((claimId) => claimIds.indexOf(claimId) + 1)
          .join(",");

        return (
          <div
            key={row.id}
            className={cn(
              "grid min-h-8 grid-cols-[20px_74px_48px_90px_1fr] items-start gap-2 px-1 py-2 transition-colors duration-fast",
              active && "bg-severity-medium/10",
            )}
            data-evidence-id={row.id}
            data-testid={`qa-evidence-row-${row.id}`}
            onMouseEnter={() => {
              if (myClaims.length > 0) onRowHover(myClaims);
            }}
            onMouseLeave={() => {
              if (myClaims.length > 0) onRowHover(null);
            }}
          >
            <span
              className={cn(
                "text-muted-foreground transition-colors duration-fast",
                active && "text-amber-500",
              )}
              data-testid={`qa-evidence-row-${row.id}-claims`}
            >
              {claimLabel ? `[${claimLabel}]` : ""}
            </span>
            <span
              className={cn(
                "truncate text-muted-foreground transition-colors duration-fast tnum",
                active && "text-amber-500",
              )}
              data-testid={`qa-evidence-row-${row.id}-ts`}
            >
              {row.ts}
            </span>
            <span className={cn("truncate", levelClass(row.lvl))}>{row.lvl}</span>
            <span className="truncate text-muted-foreground">{row.butler}</span>
            <span className="min-w-0 whitespace-pre-wrap break-words text-foreground">{row.msg}</span>
          </div>
        );
      })}
    </div>
  );
}
