import type { QaInvestigationNotes } from "@/api/types";

type ClaimSegment = QaInvestigationNotes["blurb_segments"][number];

export function getClaimOrderFromSegments(segments: ClaimSegment[]): string[] {
  const seen = new Set<string>();
  const order: string[] = [];
  segments.forEach((segment) => {
    if (typeof segment === "string" || seen.has(segment.claim)) return;
    seen.add(segment.claim);
    order.push(segment.claim);
  });
  return order;
}
