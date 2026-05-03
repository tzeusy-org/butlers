import { Skeleton } from "@/components/ui/skeleton";

interface ChartSkeletonProps {
  /** Height of the chart placeholder area. @default "h-64" */
  height?: string;
  /** Optional data-testid for testing. */
  testId?: string;
}

/**
 * Skeleton loader for chart/visualization areas.
 *
 * Renders a rectangular skeleton placeholder matching the chart's height.
 * Intended to be composed inside an existing card container rather than used
 * as a standalone card-level skeleton.
 */
export function ChartSkeleton({ height = "h-64", testId }: ChartSkeletonProps) {
  return (
    <Skeleton
      className={`w-full rounded-lg ${height}`}
      data-testid={testId}
      role="status"
      aria-label="Loading chart"
    />
  );
}
