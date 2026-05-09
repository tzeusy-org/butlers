import { Badge } from "@/components/ui/badge";

interface ButlerStatusBadgeProps {
  status: "ok" | "degraded" | "error" | "down" | string;
  "data-testid"?: string;
}

/**
 * Shared status badge for butler health indicators.
 *
 * Covers: ok → "Up" (emerald), degraded → "Degraded" (amber outline),
 * error/down → "Down" (destructive), anything else → secondary variant
 * with the raw status string.
 */
export function ButlerStatusBadge({ status, "data-testid": testId }: ButlerStatusBadgeProps) {
  switch (status) {
    case "ok":
      return (
        <Badge
          data-testid={testId}
          className="bg-emerald-600 text-white hover:bg-emerald-600/90"
        >
          Up
        </Badge>
      );
    case "degraded":
      return (
        <Badge
          data-testid={testId}
          variant="outline"
          className="border-amber-500 text-amber-600"
        >
          Degraded
        </Badge>
      );
    case "error":
    case "down":
      return (
        <Badge data-testid={testId} variant="destructive">
          Down
        </Badge>
      );
    default:
      return (
        <Badge data-testid={testId} variant="secondary">
          {status}
        </Badge>
      );
  }
}
