import type { ComplexityTier } from "@/api/types.ts";
import { Badge } from "@/components/ui/badge";

// ---------------------------------------------------------------------------
// Complexity tier display helpers
// ---------------------------------------------------------------------------

const COMPLEXITY_LABELS: Record<ComplexityTier, string> = {
  reasoning: "Reasoning",
  workhorse: "Workhorse",
  cheap: "Cheap",
  specialty: "Specialty",
  local: "Local",
  legacy: "Legacy",
};

// Fixed 6-slot model-complexity-tier palette. Complexity is a local taxonomy,
// not an operational state, so it uses the dedicated categorical ramp.
const COMPLEXITY_COLORS: Record<ComplexityTier, string> = {
  reasoning: "border-categorical-1 text-categorical-1",
  workhorse: "border-categorical-2 text-categorical-2",
  cheap: "border-categorical-3 text-categorical-3",
  specialty: "border-categorical-4 text-categorical-4",
  local: "border-categorical-5 text-categorical-5",
  legacy: "border-categorical-6 text-categorical-6",
};

// eslint-disable-next-line react-refresh/only-export-components
export const COMPLEXITY_TIERS: ComplexityTier[] = [
  "reasoning",
  "workhorse",
  "cheap",
  "specialty",
  "local",
  "legacy",
];

// eslint-disable-next-line react-refresh/only-export-components
export function complexityLabel(tier: ComplexityTier | string): string {
  return COMPLEXITY_LABELS[tier as ComplexityTier] ?? tier;
}

export interface ComplexityBadgeProps {
  tier: ComplexityTier | string;
}

/** A colored badge showing a complexity tier. */
export function ComplexityBadge({ tier }: ComplexityBadgeProps) {
  const colorClass =
    COMPLEXITY_COLORS[tier as ComplexityTier] ?? "border-muted-foreground text-muted-foreground";
  return (
    <Badge variant="outline" className={colorClass}>
      {complexityLabel(tier)}
    </Badge>
  );
}
