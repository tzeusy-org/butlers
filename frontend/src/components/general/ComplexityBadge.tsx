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

const COMPLEXITY_COLORS: Record<ComplexityTier, string> = {
  reasoning: "bg-red-600 text-white hover:bg-red-600/90",
  workhorse: "bg-blue-600 text-white hover:bg-blue-600/90",
  cheap: "bg-slate-500 text-white hover:bg-slate-500/90",
  specialty: "bg-purple-600 text-white hover:bg-purple-600/90",
  local: "bg-teal-600 text-white hover:bg-teal-600/90",
  legacy: "bg-zinc-600 text-white hover:bg-zinc-600/90",
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
  const colorClass = COMPLEXITY_COLORS[tier as ComplexityTier] ?? "bg-slate-400 text-white";
  return (
    <Badge className={colorClass}>
      {complexityLabel(tier)}
    </Badge>
  );
}

export default ComplexityBadge;
