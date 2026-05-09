import { Badge } from "@/components/ui/badge";

interface ButlerStatusBadgeProps {
  status: "ok" | "degraded" | "error" | "down" | string;
  "data-testid"?: string;
  role?: string;
  "aria-label"?: string;
}

/**
 * Shared status badge for butler health indicators.
 *
 * Covers: ok → "Up" (emerald), degraded → "Degraded" (amber outline),
 * error/down → "Down" (destructive), anything else → secondary variant
 * with the raw status string.
 *
 * Pass `role="status"` and `aria-label` to satisfy accessibility requirements
 * in contexts where the badge conveys live health state.
 */
export function ButlerStatusBadge({
  status,
  "data-testid": testId,
  role,
  "aria-label": ariaLabel,
}: ButlerStatusBadgeProps) {
  switch (status) {
    case "ok":
      return (
        <Badge
          data-testid={testId}
          role={role}
          aria-label={ariaLabel ?? "Butler status: Up"}
          className="bg-emerald-600 text-white hover:bg-emerald-600/90"
        >
          Up
        </Badge>
      );
    case "degraded":
      return (
        <Badge
          data-testid={testId}
          role={role}
          aria-label={ariaLabel ?? "Butler status: Degraded"}
          variant="outline"
          className="border-amber-500 text-amber-600"
        >
          Degraded
        </Badge>
      );
    case "error":
    case "down":
      return (
        <Badge
          data-testid={testId}
          role={role}
          aria-label={ariaLabel ?? "Butler status: Down"}
          variant="destructive"
        >
          Down
        </Badge>
      );
    default:
      return (
        <Badge
          data-testid={testId}
          role={role}
          aria-label={ariaLabel ?? `Butler status: ${status}`}
          variant="secondary"
        >
          {status}
        </Badge>
      );
  }
}
