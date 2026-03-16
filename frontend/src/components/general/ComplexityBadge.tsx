import type { ComplexityTier } from "@/api/types.ts";
import { Badge } from "@/components/ui/badge";

// ---------------------------------------------------------------------------
// Complexity tier display helpers
// ---------------------------------------------------------------------------

const COMPLEXITY_LABELS: Record<ComplexityTier, string> = {
  trivial: "Trivial",
  medium: "Medium",
  high: "High",
  extra_high: "Extra High",
  discretion: "Discretion",
};

const COMPLEXITY_COLORS: Record<ComplexityTier, string> = {
  trivial: "bg-slate-500 text-white hover:bg-slate-500/90",
  medium: "bg-blue-600 text-white hover:bg-blue-600/90",
  high: "bg-amber-600 text-white hover:bg-amber-600/90",
  extra_high: "bg-red-600 text-white hover:bg-red-600/90",
  discretion: "bg-purple-600 text-white hover:bg-purple-600/90",
};

// eslint-disable-next-line react-refresh/only-export-components
export const COMPLEXITY_TIERS: ComplexityTier[] = [
  "trivial",
  "medium",
  "high",
  "extra_high",
  "discretion",
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
  const colorClass = COMPLEXITY_COLORS[tier as ComplexityTier] ?? "bg-slate-400 text-white";
  return (
    <Badge className={colorClass}>
      {complexityLabel(tier)}
    </Badge>
  );
}

export default ComplexityBadge;
