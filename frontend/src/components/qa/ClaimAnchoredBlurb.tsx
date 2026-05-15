import { useMemo } from "react";

import type { QaInvestigationNotes } from "@/api/types";
import { cn } from "@/lib/utils";

import { getClaimOrderFromSegments } from "./claimOrder";

type ClaimSegment = QaInvestigationNotes["blurb_segments"][number];
type ClaimMap = QaInvestigationNotes["claims"];

interface ClaimAnchoredBlurbProps {
  segments: ClaimSegment[];
  claims: ClaimMap;
  claimOrder?: string[];
  hoveredClaim: string[] | null;
  onClaimHover: (claimIds: string[] | null) => void;
  className?: string;
}

export function ClaimAnchoredBlurb({
  segments,
  claims,
  claimOrder,
  hoveredClaim,
  onClaimHover,
  className,
}: ClaimAnchoredBlurbProps) {
  const orderedClaimIds = useMemo(() => {
    const derivedOrder = getClaimOrderFromSegments(segments);
    if (!claimOrder) return derivedOrder;
    const seen = new Set(claimOrder);
    return [...claimOrder, ...derivedOrder.filter((claimId) => !seen.has(claimId))];
  }, [claimOrder, segments]);

  const claimNumbers = useMemo(
    () => new Map(orderedClaimIds.map((claimId, index) => [claimId, index + 1])),
    [orderedClaimIds],
  );

  return (
    <p
      className={cn(
        "font-serif text-[17px] leading-8 text-foreground",
        className,
      )}
    >
      {segments.map((segment, index) => {
        if (typeof segment === "string") {
          return <span key={`segment-${index}`}>{segment}</span>;
        }

        const active = hoveredClaim?.includes(segment.claim) ?? false;
        const claim = claims[segment.claim];
        const claimNumber = claimNumbers.get(segment.claim);

        return (
          <span
            key={`claim-${segment.claim}-${index}`}
            className={cn(
              "rounded-[2px] underline decoration-border/80 decoration-1 underline-offset-4 transition-colors duration-fast",
              active && "bg-severity-medium/15 decoration-amber-500",
            )}
            data-claim-id={segment.claim}
            data-testid={`qa-claim-${segment.claim}`}
            title={claim?.note}
            onMouseEnter={() => onClaimHover([segment.claim])}
            onMouseLeave={() => onClaimHover(null)}
          >
            {segment.text}
            <sup
              className={cn(
                "ml-1 align-super font-mono text-[10px] leading-none text-muted-foreground transition-colors duration-fast",
                active && "text-amber-500",
              )}
              data-testid={`qa-claim-${segment.claim}-marker`}
            >
              [{claimNumber}]
            </sup>
          </span>
        );
      })}
    </p>
  );
}
