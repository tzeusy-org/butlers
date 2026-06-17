// ---------------------------------------------------------------------------
// InsightDeliveryTile -- dashboard insight-delivery pipeline state tile
// (bu-dl98i.3.3)
//
// Data source: useInsightDeliveryState -> GET /api/system/insights/delivery-state
// Fields used: queued, delivered, failed, last_delivery_at
//
// Displays honest pipeline counts from the real delivery-state tables.
// All-zero counts with no last_delivery_at is the correct empty state —
// it means the pipeline has not run yet, not that something is broken.
// ---------------------------------------------------------------------------

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { useInsightDeliveryState } from "@/hooks/use-system"

// ---------------------------------------------------------------------------
// Loading / error sub-components
// ---------------------------------------------------------------------------

function TileSkeleton() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Insight Delivery</CardTitle>
        <CardDescription>Proactive insight pipeline state</CardDescription>
      </CardHeader>
      <CardContent>
        <div data-testid="insight-delivery-tile-skeleton" className="space-y-2">
          <Skeleton className="h-5 w-40" />
          <Skeleton className="h-5 w-40" />
          <Skeleton className="h-5 w-40" />
          <Skeleton className="h-5 w-48" />
        </div>
      </CardContent>
    </Card>
  )
}

function TileError() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Insight Delivery</CardTitle>
        <CardDescription>Proactive insight pipeline state</CardDescription>
      </CardHeader>
      <CardContent>
        <p data-testid="insight-delivery-tile-error" className="text-destructive text-sm">
          Could not load insight delivery state.
        </p>
      </CardContent>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// StatRow
// ---------------------------------------------------------------------------

interface StatRowProps {
  label: string;
  value: number | string;
  testId: string;
  muted?: boolean;
}

function StatRow({ label, value, testId, muted = false }: StatRowProps) {
  return (
    <div className="flex items-center justify-between gap-2 py-1">
      <dt className="text-sm text-muted-foreground">{label}</dt>
      <dd className={`m-0 text-sm font-medium tabular-nums${muted ? " text-muted-foreground" : ""}`} data-testid={testId}>
        {value}
      </dd>
    </div>
  )
}

// ---------------------------------------------------------------------------
// InsightDeliveryTile
// ---------------------------------------------------------------------------

/**
 * Displays insight delivery pipeline state from the health endpoint.
 *
 * Indicators:
 *   - Queued: pending candidates awaiting the next delivery cycle
 *   - Delivered: successfully delivered candidates (last ~30 days)
 *   - Failed: permanently blocked after 3 consecutive delivery failures
 *   - Last delivery: timestamp of the most recent successful delivery
 *
 * All-zero counts with "No deliveries yet" is an honest empty state.
 * Values come from real delivery-state tables — never placeholders.
 */
export function InsightDeliveryTile() {
  const { data: response, isPending, isError } = useInsightDeliveryState()

  if (isPending) return <TileSkeleton />
  if (isError) return <TileError />

  const state = response?.data

  const lastDeliveryLabel = state?.last_delivery_at
    ? new Date(state.last_delivery_at).toLocaleString()
    : "No deliveries yet"

  return (
    <Card>
      <CardHeader>
        <CardTitle>Insight Delivery</CardTitle>
        <CardDescription>Proactive insight pipeline state</CardDescription>
      </CardHeader>
      <CardContent data-testid="insight-delivery-tile-content">
        <dl className="divide-y divide-border">
          <StatRow
            label="Queued"
            value={state?.queued ?? 0}
            testId="insight-delivery-queued"
          />
          <StatRow
            label="Delivered"
            value={state?.delivered ?? 0}
            testId="insight-delivery-delivered"
          />
          <StatRow
            label="Failed"
            value={state?.failed ?? 0}
            testId="insight-delivery-failed"
          />
          <StatRow
            label="Last delivery"
            value={lastDeliveryLabel}
            testId="insight-delivery-last-at"
            muted={!state?.last_delivery_at}
          />
        </dl>
      </CardContent>
    </Card>
  )
}
