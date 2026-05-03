import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

interface ChartSkeletonProps {
  /** Height of the chart placeholder area. @default "h-64" */
  height?: string;
  /** Optional data-testid for testing. */
  testId?: string;
}

/**
 * Skeleton loader for chart/visualization cards.
 *
 * Renders a card with a title placeholder and a large rectangular area
 * simulating where the chart will appear once data loads.
 */
export function ChartSkeleton({ height = "h-64", testId }: ChartSkeletonProps) {
  return (
    <Card data-testid={testId} role="status" aria-label="Loading chart">
      <CardHeader>
        <Skeleton className="h-5 w-40" />
        <Skeleton className="h-4 w-64" />
      </CardHeader>
      <CardContent>
        <Skeleton className={`w-full rounded-lg ${height}`} />
      </CardContent>
    </Card>
  );
}
