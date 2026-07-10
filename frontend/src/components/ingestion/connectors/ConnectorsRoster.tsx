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
 * Data: uses useConnectorSummariesWithAggregates and useAvailableConnectors
 * hooks. Per-connector `hourly_events` (ingested) and `hourly_filtered_events`
 * (skip-routed, bu-scyro) both come from GET /api/ingestion/connectors/summaries
 * — sourced from the DB, not Prometheus, so sparklines are always populated
 * regardless of `aggregates_available`. When the response's top-level
 * `hourly_events_available` is `false` (the combined hourly query itself
 * failed), a `SourceDegradedNote` names the degraded source instead of letting
 * the all-zero fallback arrays render as an honest "quiet 24h". Likewise,
 * when `device_liveness_available` is `false` (the per-device liveness query
 * itself failed), every connector's `devices` falls back to `null` — a
 * `SourceDegradedNote` names that failure too, since a silently-null
 * `devices` list is indistinguishable from "no multi-device connectors"
 * (bu-fm3my; same shape as bu-scyro's hourly note). Both flags are
 * genuine-failure-only — absent (older cached response) must NOT trigger
 * the note, only an explicit `false`.
 *
 * `aggregates_available` on this endpoint is NOT gated here: the response's
 * per-connector fields (`hourly_events`, `hourly_filtered_events`, `today`,
 * `devices`) are all DB-sourced, not Prometheus-sourced, so nothing rendered
 * on this page actually depends on it (the flag mirrors the pipeline cache's
 * Prometheus reachability, consumed instead by BoardFooter's own
 * `aggregates_available` on a different endpoint/response). Gating a note on
 * it here would fabricate a "degraded" signal for data unaffected by the
 * flag.
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
import { SourceDegradedNote } from '@/components/ui/query-boundary'
import { AttentionStrip } from './AttentionStrip'
import { ConnectorRosterRow } from './ConnectorRosterRow'
import { DormantList } from './DormantList'
import { ArchivedConnectorsList } from './ArchivedConnectorsList'
import { ArchiveCandidatesList } from './ArchiveCandidatesList'
import { deriveConnectorDispatchInfo } from './connector-auth'
import { CONNECTOR_ROSTER_GRID_COLUMNS } from './layout'

// ---------------------------------------------------------------------------
// Column headers
// ---------------------------------------------------------------------------

const COLUMN_LABELS = [
  'status',    // health verdict — dot + word
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

  // The new endpoint returns { connectors: [...], aggregates_available: bool }.
  // Archived identities (bu-33dm2) are returned in the same list but are
  // superseded/dead endpoints — split them out so they never contribute to the
  // active roster, its attention strip, KPI band, sort order, or dormant
  // detection. They surface only in the collapsed "archived" section below,
  // which keeps their history reachable. `archived` is absent on older cached
  // responses; treat missing as false.
  const returnedConnectors: ConnectorSummary[] = connectorsResp?.data?.connectors ?? []
  const allConnectors = returnedConnectors.filter((c) => !c.archived)
  const archivedConnectors = returnedConnectors.filter((c) => c.archived)
  const sorted = sortConnectors(allConnectors)

  // hourly_events_available (bu-scyro) is false only when the backend's combined
  // ingested+filtered hourly query itself raised — in that case every connector's
  // hourly_events/hourly_filtered_events fall back to all-zero arrays and
  // today.messages_ingested (summed from hourly_events) reads as 0. Absent field
  // (older cached response) must NOT be treated as false. Never let that render as
  // an honest "quiet 24h" — surface the degraded source inline instead.
  const hourlyEventsAvailable = connectorsResp?.data?.hourly_events_available !== false

  // device_liveness_available (bu-e16to/bu-fm3my) is false only when the backend's
  // per-device liveness query itself raised — in that case every connector's
  // `devices` falls back to null, indistinguishable from "no multi-device
  // connectors" (silently hiding a stale/dead sibling device). Absent field
  // (older cached response) must NOT be treated as false.
  const deviceLivenessAvailable = connectorsResp?.data?.device_liveness_available !== false

  // Available dormant profiles (catalog entries not yet registered)
  const catalogProfiles = availableResp?.data ?? []
  const registeredTypes = new Set(allConnectors.map((c) => c.connector_type))
  const dormantProfiles = catalogProfiles.filter((p) => !registeredTypes.has(p.connector_type))

  // connector_type -> real channel, from the discovery catalog. Used so the
  // roster only ever shows a known kind — never a guess from name substrings.
  const catalogChannelByType = new Map(catalogProfiles.map((p) => [p.connector_type, p.channel]))

  // Roster-wide sparkline peak so bar heights are comparable across rows
  // instead of each row normalizing to its own (possibly tiny) peak.
  const rosterSparkMax = Math.max(
    1,
    ...allConnectors.flatMap((c) => c.hourly_events ?? []),
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
          <div key={i} className="h-14 w-full bg-foreground/5 rounded" />
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
              spark24hFiltered={c.hourly_filtered_events}
              catalogChannel={catalogChannelByType.get(c.connector_type)}
              rosterSparkMax={rosterSparkMax}
            />
          ))}
        </div>
      )}

      {/* Dormant / available connectors */}
      <DormantList profiles={dormantProfiles} />

      {/* Archive review queue (bu-u19yv) — active identities flagged
          `archive_candidate` (offline >30d + a newer online sibling). A
          SUGGESTION overlay: these rows also remain in the active roster above
          with their true offline liveness/KPIs; this queue only offers a
          one-click archive reusing the audit-logged archive endpoint. */}
      <ArchiveCandidatesList connectors={allConnectors} />

      {/* Archived / superseded connector identities (bu-33dm2) — collapsed,
          excluded from the active roster + KPIs above, history still reachable. */}
      <ArchivedConnectorsList connectors={archivedConnectors} />

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

      {/* Hourly events source degraded note -- never let a failed hourly query hide
          behind a quiet "events · 24h" total or empty-looking sparklines (bu-scyro). */}
      {!hourlyEventsAvailable && (
        <SourceDegradedNote
          label="24h activity"
          detail="hourly event source unavailable, sparklines and events · 24h above are incomplete"
          className="mt-4"
        />
      )}

      {/* Per-device liveness source degraded note -- a failed per-device query
          falls back to devices:null for every connector, which is otherwise
          indistinguishable from "no multi-device connectors" and would hide a
          silently-dead sibling device (bu-e16to/bu-fm3my). */}
      {!deviceLivenessAvailable && (
        <SourceDegradedNote
          label="device liveness"
          detail="per-device liveness source unavailable, multi-device connectors may be missing sibling device badges"
          className="mt-4"
        />
      )}

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
