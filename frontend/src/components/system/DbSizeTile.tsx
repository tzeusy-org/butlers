// ---------------------------------------------------------------------------
// DbSizeTile -- PostgreSQL database total size + schema breakdown
// (bu-ngfzz.5)
//
// Data source: useDatabaseFacts -> GET /api/system/database
// Fields used: total_size_bytes, schemas, growth_rate_bytes_per_day
//
// Sparkline note: growth_rate_bytes_per_day is always null in v1. The
// sparkline is omitted rather than faked. A follow-up bead should add a
// time-series size history endpoint for recharts sparkline support.
// ---------------------------------------------------------------------------

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { useDatabaseFacts } from "@/hooks/use-system"
import type { SchemaSize } from "@/api/types"
import { humanizeBytes } from "./db-size-utils"

// ---------------------------------------------------------------------------
// Loading / error sub-components
// ---------------------------------------------------------------------------

function TileSkeleton() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Database Size</CardTitle>
        <CardDescription>PostgreSQL disk footprint</CardDescription>
      </CardHeader>
      <CardContent>
        <div data-testid="db-size-tile-skeleton" className="space-y-2">
          <Skeleton className="h-8 w-24" />
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-3/4" />
        </div>
      </CardContent>
    </Card>
  )
}

function TileError() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Database Size</CardTitle>
        <CardDescription>PostgreSQL disk footprint</CardDescription>
      </CardHeader>
      <CardContent>
        <p data-testid="db-size-tile-error" className="text-destructive text-sm">
          Could not load database size.
        </p>
      </CardContent>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// Schema breakdown row
// ---------------------------------------------------------------------------

interface SchemaRowProps {
  schema: SchemaSize
  totalBytes: number
}

function SchemaRow({ schema, totalBytes }: SchemaRowProps) {
  const pct = totalBytes > 0 ? Math.round((schema.size_bytes / totalBytes) * 100) : 0
  return (
    <li className="flex items-center justify-between gap-2 text-sm">
      <span className="font-mono text-xs text-muted-foreground truncate">
        {schema.schema_name}
      </span>
      <span className="shrink-0 tabular-nums text-xs">
        {humanizeBytes(schema.size_bytes)}
        <span className="text-muted-foreground ml-1">({pct}%)</span>
      </span>
    </li>
  )
}

// ---------------------------------------------------------------------------
// DbSizeTile
// ---------------------------------------------------------------------------

/**
 * Displays total PostgreSQL database size and a per-schema breakdown.
 *
 * Growth sparkline: omitted in v1 because growth_rate_bytes_per_day is always
 * null. A follow-up bead will add a size history endpoint to power the chart.
 */
export function DbSizeTile() {
  const { data: response, isPending, isError } = useDatabaseFacts()

  if (isPending) return <TileSkeleton />
  if (isError) return <TileError />

  const facts = response?.data

  // Show at most the top 5 schemas by size (already sorted by API)
  const topSchemas = (facts?.schemas ?? []).slice(0, 5)

  return (
    <Card>
      <CardHeader>
        <CardTitle>Database Size</CardTitle>
        <CardDescription>PostgreSQL disk footprint</CardDescription>
      </CardHeader>
      <CardContent data-testid="db-size-tile-content">
        <dl className="space-y-4 text-sm">
          <div>
            <dt className="text-muted-foreground text-xs">Total size</dt>
            <dd className="text-lg font-semibold tabular-nums">
              {facts != null ? humanizeBytes(facts.total_size_bytes) : "--"}
            </dd>
          </div>

          {topSchemas.length > 0 && (
            <div>
              <dt className="text-muted-foreground text-xs mb-2">Largest schemas (top 5)</dt>
              <dd>
                <ul className="space-y-1.5">
                  {topSchemas.map((schema) => (
                    <SchemaRow
                      key={schema.schema_name}
                      schema={schema}
                      totalBytes={facts?.total_size_bytes ?? 0}
                    />
                  ))}
                </ul>
              </dd>
            </div>
          )}
        </dl>
      </CardContent>
    </Card>
  )
}
