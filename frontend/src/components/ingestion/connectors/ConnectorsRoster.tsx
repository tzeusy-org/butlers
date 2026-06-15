/**
 * ConnectorsRoster — dense hairline-divided roster for /ingestion/connectors.
 *
 * Replaces the old card grid (ConnectorsListPage) with a dense register that
 * can be scanned at a glance. Auth-needed connectors sort to the top and also
 * appear in the AttentionStrip.
 *
 * Sections:
 * 1. AttentionStrip (conditional — renders only if any connector needs attention)
 * 2. Column headers (mono uppercase)
 * 3. Active connector rows (auth-needed first, then by liveness, then alphabetical)
 * 4. DormantList (collapsible, from available-catalog profiles not yet registered)
 * 5. KPI footer band (total connectors, healthy, auth-needed, events/24h)
 * 6. "add connector" action
 *
 * Data: uses existing useConnectorSummaries and useAvailableConnectors hooks.
 * Spark data comes from usePipelineStats — the 24h spark is available globally;
 * per-connector hourly timeseries is not exposed at list level, so sparklines
 * use the pipeline spark divided proportionally or fall back to zeros.
 *
 * NOTE: useConnectorDetail MUST NOT be mounted from this roster (spec §6.2).
 * Only summary-level data is shown here.
 *
 * Spec: openspec/changes/complete-ingestion-redesign-parity/specs/
 *       dashboard-ingestion-dispatch-console/spec.md §"Connectors Roster"
 */

import { Link } from 'react-router'
import {
  useConnectorSummariesWithAggregates,
  useAvailableConnectors,
} from '@/hooks/use-ingestion'
import type { ConnectorSummary } from '@/api/types'
import { AttentionStrip } from './AttentionStrip'
import { ConnectorRosterRow } from './ConnectorRosterRow'
import { DormantList } from './DormantList'
import { deriveConnectorDispatchInfo } from './connector-auth'
import { CONNECTOR_ROSTER_GRID_COLUMNS } from './layout'

// ---------------------------------------------------------------------------
// Column headers
// ---------------------------------------------------------------------------

const COLUMN_LABELS = [
  '',          // liveness dot (top) + state dot (bottom)
  'channel',
  'function',
  '24h activity',
  'auth',
  'events',
  '',          // disclosure
]

// ---------------------------------------------------------------------------
// Sort helpers
// ---------------------------------------------------------------------------

function sortConnectors(connectors: ConnectorSummary[]): ConnectorSummary[] {
  return [...connectors].sort((a, b) => {
    const aInfo = deriveConnectorDispatchInfo(a)
    const bInfo = deriveConnectorDispatchInfo(b)

    // Auth-needed or error first
    const aScore = needsAttentionScore(aInfo.needsAttention, aInfo.health)
    const bScore = needsAttentionScore(bInfo.needsAttention, bInfo.health)
    if (aScore !== bScore) return bScore - aScore

    // Then by connector_type alphabetically
    return a.connector_type.localeCompare(b.connector_type)
  })
}

function needsAttentionScore(needsAttention: boolean, health: string): number {
  if (!needsAttention) return 0
  if (health === 'error') return 2
  return 1
}

// ---------------------------------------------------------------------------
// KPI footer helpers
// ---------------------------------------------------------------------------

function formatNum(n: number): string {
  if (n >= 10_000) return Math.round(n / 1000) + 'k'
  if (n >= 1000) return (n / 1000).toFixed(1) + 'k'
  return String(n)
}

// ---------------------------------------------------------------------------
// ConnectorsRoster
// ---------------------------------------------------------------------------

/**
 * Main roster component for /ingestion/connectors.
 *
 * Connectors that need attention (auth error, degraded, offline) sort to the
 * top and appear in the attention strip. The roster uses existing hooks and
 * does not add new backend endpoints.
 */
export function ConnectorsRoster() {
  const { data: connectorsResp, isLoading: connectorsLoading } =
    useConnectorSummariesWithAggregates()
  const { data: availableResp } = useAvailableConnectors()

  // The new endpoint returns { connectors: [...], aggregates_available: bool }
  const allConnectors: ConnectorSummary[] = connectorsResp?.data?.connectors ?? []
  const sorted = sortConnectors(allConnectors)

  // Available dormant profiles (catalog entries not yet registered)
  const registeredTypes = new Set(allConnectors.map((c) => c.connector_type))
  const dormantProfiles = (availableResp?.data ?? []).filter(
    (p) => !registeredTypes.has(p.connector_type),
  )

  // KPI aggregates
  const totalConnectors = allConnectors.length
  const healthyCount = allConnectors.filter(
    (c) => !deriveConnectorDispatchInfo(c).needsAttention,
  ).length
  const authNeededCount = allConnectors.filter(
    (c) => deriveConnectorDispatchInfo(c).authStatus === 'needs_reauth',
  ).length
  // today.messages_ingested is already the 24h sum on the backend (derived from hourly_events).
  const totalEvents24h = allConnectors.reduce(
    (s, c) => s + (c.today?.messages_ingested ?? 0),
    0,
  )

  if (connectorsLoading) {
    return (
      <div className="space-y-3 py-4">
        {Array.from({ length: 3 }).map((_, i) => (
          <div key={i} className="h-14 w-full bg-foreground/5 rounded animate-pulse" />
        ))}
      </div>
    )
  }

  return (
    <div data-testid="connectors-roster">
      {/* Attention strip — only when issues present */}
      <AttentionStrip connectors={allConnectors} />

      {/* Column headers */}
      <div
        className="grid gap-x-4 py-2.5 border-b border-border"
        style={{ gridTemplateColumns: CONNECTOR_ROSTER_GRID_COLUMNS }}
      >
        {COLUMN_LABELS.map((label, i) => (
          <span
            key={i}
            className={`font-mono text-[9.5px] tracking-[0.14em] uppercase text-muted-foreground/70 ${i === 5 ? 'text-right' : ''}`}
          >
            {label}
          </span>
        ))}
      </div>

      {/* Roster rows */}
      {allConnectors.length === 0 ? (
        <p className="font-serif italic text-[14px] text-muted-foreground py-8">
          No connectors registered.
        </p>
      ) : (
        <div data-testid="roster-rows">
          {sorted.map((c) => (
            <ConnectorRosterRow
              key={`${c.connector_type}:${c.endpoint_identity}`}
              connector={c}
              spark24h={c.hourly_events}
            />
          ))}
        </div>
      )}

      {/* Dormant / available connectors */}
      <DormantList profiles={dormantProfiles} />

      {/* KPI footer band */}
      <div
        className="mt-9 pt-4 border-t border-border grid gap-6"
        style={{ gridTemplateColumns: 'repeat(5, 1fr)' }}
      >
        {[
          { label: 'connectors · live', value: formatNum(totalConnectors) },
          { label: 'healthy', value: formatNum(healthyCount) },
          { label: 'needs attention', value: formatNum(totalConnectors - healthyCount) },
          { label: 'auth · error', value: formatNum(authNeededCount) },
          { label: 'events · 24h', value: formatNum(totalEvents24h) },
        ].map(({ label, value }) => (
          <div key={label}>
            <div className="font-mono text-[9.5px] tracking-[0.14em] uppercase text-muted-foreground">
              {label}
            </div>
            <div className="mt-1.5 font-mono text-[22px] font-medium tracking-[-0.02em] tabular-nums">
              {value}
            </div>
          </div>
        ))}
      </div>

      {/* Actions */}
      <div className="mt-8 flex gap-2.5">
        <Link
          to="/secrets"
          className="font-mono text-[11px] border border-foreground px-3 py-1.5 hover:bg-foreground hover:text-background transition-colors"
        >
          + add connector
        </Link>
      </div>
    </div>
  )
}
