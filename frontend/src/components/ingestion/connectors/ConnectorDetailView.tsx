/**
 * ConnectorDetailView — Dispatch-language connector detail body.
 *
 * Renders the two-zone editorial layout for one connector:
 *
 * Header band:
 *   - Large letter-mark glyph (56px) + display headline + mono meta line
 *   - Serif purpose paragraph
 *   - ReauthCallout (conditional — only when auth broken/expiring)
 *
 * Left (1.4fr):
 *   - 4-cell KPI strip (events, error rate, avg/hr, last heartbeat)
 *   - 24h histogram using ConnectorStats timeseries
 *   - Lifetime counters table
 *   - Recent events list [bu-5ywn2]
 *   - Incident list [bu-5ywn2]
 *
 * Right (1fr):
 *   - ScopeList (from connector-oauth-scope-surface when available)
 *   - Schedule / config KV block
 *   - Config actions (cursor edit, settings)
 *   - Routing rules [bu-5ywn2]
 *
 * This component is purely presentational — data is wired in ConnectorDetailPage.
 * It replaces the old card-based ConnectorDetailPage layout.
 *
 * Design: no card chrome, no shadcn Card containers. One elevation.
 * Hairline borders for structure. Mono numerals, serif voice text.
 *
 * Spec: openspec/changes/complete-ingestion-redesign-parity/specs/
 *       dashboard-ingestion-dispatch-console/spec.md §"Connector Detail"
 * Reference: docs/redesigns/ingestion-connector-detail.jsx
 */

import { Link } from 'react-router'
import { Time } from '@/components/ui/time'
import type {
  ConnectorDetail,
  ConnectorEventsResponse,
  ConnectorIncidentsResponse,
  ConnectorRoutingRulesResponse,
  ConnectorStats,
} from '@/api/types'
import { ReauthCallout } from './ReauthCallout'
import { ScopeList, type OAuthScope } from './ScopeList'
import { ConnectorHistogram } from './ConnectorHistogram'
import { deriveConnectorDispatchInfo } from './connector-auth'

// ---------------------------------------------------------------------------
// KV row helper
// ---------------------------------------------------------------------------

function KVRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div
      className="grid gap-x-3 py-2 border-b border-border/50 items-baseline"
      style={{ gridTemplateColumns: '100px 1fr' }}
    >
      <span className="font-mono text-[9.5px] tracking-[0.14em] uppercase text-muted-foreground">
        {label}
      </span>
      <div className="min-w-0">{value}</div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Letter-mark glyph helper
// ---------------------------------------------------------------------------

/** Single uppercase letter glyph for the connector channel. */
function ChannelGlyph({ connectorType, size = 56 }: { connectorType: string; size?: number }) {
  const letter = connectorType.charAt(0).toUpperCase()
  // Neutral gray background for the glyph — no butler-hue treatment here (only letter marks)
  return (
    <div
      className="flex items-center justify-center rounded-sm bg-foreground/10 shrink-0 font-mono font-medium text-foreground/80"
      style={{ width: size, height: size, fontSize: size * 0.45 }}
      aria-hidden="true"
    >
      {letter}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Formatters
// ---------------------------------------------------------------------------

function fmtNum(n: number | undefined | null): string {
  if (n == null) return '—'
  if (n >= 10_000) return Math.round(n / 1000) + 'k'
  if (n >= 1000) return (n / 1000).toFixed(1) + 'k'
  return String(n)
}

function fmtPct(n: number | undefined | null): string {
  if (n == null) return '—'
  return `${n.toFixed(1)}%`
}

function fmtAvg(n: number | undefined | null): string {
  if (n == null) return '—'
  return n.toFixed(1) + '/hr'
}

// ---------------------------------------------------------------------------
// ConnectorDetailView
// ---------------------------------------------------------------------------

export interface ConnectorDetailViewProps {
  connector: ConnectorDetail
  stats: ConnectorStats | undefined
  /** OAuth scopes from connector-oauth-scope-surface. Null = unavailable. */
  oauthScopes?: OAuthScope[] | null
  /** Recent events response from /connectors/{type}/{identity}/events. [bu-5ywn2] */
  recentEvents?: ConnectorEventsResponse | null
  /** Incident events response from /connectors/{type}/{identity}/incidents. [bu-5ywn2] */
  incidents?: ConnectorIncidentsResponse | null
  /** Routing rules response from /connectors/{type}/{identity}/routing-rules. [bu-5ywn2] */
  routingRules?: ConnectorRoutingRulesResponse | null
  /** Called when user clicks re-authorize (auth error / expiring). */
  onReauth?: () => void
  /** Called when user clicks "set primary account" (no_primary_account case). */
  onSetPrimaryAccount?: () => void
  /** Called when user clicks "pause poll". */
  onPause?: () => void
  /** Called when user clicks "run now". */
  onRunNow?: () => void
}

/**
 * Dispatch-language two-zone connector detail layout.
 *
 * The header band always renders. The reauth callout appears only when
 * the derived auth status is needs_reauth or expiring. The scope list
 * shows unavailable state when oauthScopes is null/undefined/empty.
 */
export function ConnectorDetailView({
  connector,
  stats,
  oauthScopes,
  recentEvents,
  incidents,
  routingRules,
  onReauth,
  onSetPrimaryAccount,
  onPause,
  onRunNow,
}: ConnectorDetailViewProps) {
  const info = deriveConnectorDispatchInfo(connector)
  const displayName = connector.connector_type.replace(/_/g, ' ')

  // Derive spark data from timeseries (24h hourly buckets)
  const spark24h = deriveSparkline(stats)

  return (
    <div className="space-y-0">
      {/* Header band */}
      <div
        className="grid gap-8 pb-6 border-b border-border items-start"
        style={{ gridTemplateColumns: '1fr auto' }}
      >
        {/* Left: identity */}
        <div>
          {/* Breadcrumb eyebrow */}
          <div className="mb-4">
            <Link
              to="/ingestion/connectors"
              className="font-mono text-[10px] tracking-[0.10em] uppercase text-muted-foreground underline underline-offset-[3px] decoration-border hover:text-foreground transition-colors"
            >
              ← ingestion / connectors
            </Link>
          </div>

          <p className="font-mono text-[9.5px] tracking-[0.14em] uppercase text-muted-foreground mb-2">
            connector · {connector.connector_type} · {connector.endpoint_identity}
          </p>

          <div className="flex items-end gap-4">
            <ChannelGlyph connectorType={connector.connector_type} size={56} />
            <div>
              <h1
                className="font-medium tracking-[-0.025em] leading-[1.05] capitalize"
                style={{ fontSize: 44 }}
              >
                {displayName}.
              </h1>
              <div className="mt-1.5 flex items-baseline gap-3.5 font-mono text-[11px] text-muted-foreground tracking-[0.04em]">
                <span>{connector.endpoint_identity}</span>
                <span>·</span>
                <span className={livenessText(connector.liveness)}>{connector.liveness}</span>
                {connector.last_heartbeat_at && (
                  <>
                    <span>·</span>
                    <span>
                      last ·{' '}
                      <Time value={connector.last_heartbeat_at} mode="relative" className="inline" />
                    </span>
                  </>
                )}
              </div>
            </div>
          </div>

          <p className="mt-4 font-serif text-[15px] text-foreground leading-[1.5] max-w-[50ch]">
            {describeConnector(connector)}
          </p>
        </div>

        {/* Right: recovery callout (conditional) */}
        <ReauthCallout
          authStatus={info.authStatus}
          authNote={info.authNote}
          connectorType={connector.connector_type}
          onReauth={onReauth}
          onSetPrimaryAccount={onSetPrimaryAccount}
        />
      </div>

      {/* Two-column body */}
      <div
        className="mt-9 grid gap-14 items-start"
        style={{ gridTemplateColumns: '1.4fr 1fr' }}
      >
        {/* LEFT — KPI strip + histogram + counters */}
        <div className="space-y-8">
          {/* KPI strip */}
          <div
            className="grid gap-6 py-3.5 border-t border-b border-border"
            style={{ gridTemplateColumns: 'repeat(4, 1fr)' }}
            data-testid="kpi-strip"
          >
            {[
              {
                label: 'events · today',
                value: fmtNum(connector.today?.messages_ingested),
                delta: 'ingested',
              },
              {
                label: 'error rate',
                value: fmtPct(stats?.summary?.error_rate_pct),
                delta: `${fmtNum(connector.today?.messages_failed)} failed`,
              },
              {
                label: 'avg · per hour',
                value: fmtAvg(stats?.summary?.avg_messages_per_hour),
                delta: '24h window',
              },
              {
                label: 'last heartbeat',
                value: connector.last_heartbeat_at ? (
                  <Time value={connector.last_heartbeat_at} mode="relative" />
                ) : '—',
                delta: connector.last_heartbeat_at ? (
                  <Time value={connector.last_heartbeat_at} mode="absolute" className="inline" />
                ) : 'never',
              },
            ].map((kpi, i) => (
              <div key={i}>
                <div className="font-mono text-[9.5px] tracking-[0.14em] uppercase text-muted-foreground">
                  {kpi.label}
                </div>
                <div
                  className="mt-1.5 font-mono tabular-nums font-medium tracking-[-0.02em]"
                  style={{ fontSize: 26 }}
                >
                  {kpi.value}
                </div>
                <div className="font-mono text-[10px] text-muted-foreground/60 mt-1 block">
                  {kpi.delta}
                </div>
              </div>
            ))}
          </div>

          {/* 24h throughput histogram */}
          <div>
            <div className="flex items-baseline gap-3 mb-3">
              <span className="font-mono text-[9.5px] tracking-[0.14em] uppercase text-muted-foreground">
                throughput · 24h
              </span>
              <span className="font-mono text-[10px] text-muted-foreground/50">
                messages per hour
              </span>
            </div>
            <ConnectorHistogram data={spark24h} height={96} />
          </div>

          {/* Lifetime counters */}
          {connector.counters && (
            <div>
              <div className="font-mono text-[9.5px] tracking-[0.14em] uppercase text-muted-foreground mb-2.5">
                lifetime counters
              </div>
              <div
                className="grid gap-4"
                style={{ gridTemplateColumns: 'repeat(3, 1fr)' }}
              >
                {[
                  { label: 'ingested', value: connector.counters.messages_ingested },
                  { label: 'failed', value: connector.counters.messages_failed },
                  { label: 'api calls', value: connector.counters.source_api_calls },
                  { label: 'deduped', value: connector.counters.dedupe_accepted },
                  { label: 'checkpoints', value: connector.counters.checkpoint_saves },
                ].map(({ label, value }) => (
                  <div key={label}>
                    <div className="font-mono text-[9px] tracking-[0.12em] uppercase text-muted-foreground/60">
                      {label}
                    </div>
                    <div className="font-mono text-[16px] tabular-nums font-medium mt-0.5">
                      {value.toLocaleString()}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Recent events list [bu-5ywn2] */}
          <RecentEventsList
            events={recentEvents}
            connectorKind={connector.connector_type}
          />

          {/* Incident list [bu-5ywn2] */}
          <IncidentList incidents={incidents} />
        </div>

        {/* RIGHT — scopes + schedule + config */}
        <div className="flex flex-col gap-8">
          {/* OAuth scopes */}
          <ScopeList
            scopes={oauthScopes}
            reauthRequired={info.authStatus === 'needs_reauth'}
            connectorType={connector.connector_type}
          />

          {/* Schedule / config */}
          <div>
            <div className="font-mono text-[9.5px] tracking-[0.14em] uppercase text-muted-foreground mb-2.5">
              schedule
            </div>
            <KVRow
              label="cadence"
              value={
                <span className="font-mono text-[11px]">
                  {connector.registered_via ?? 'connector-driven'}
                </span>
              }
            />
            {connector.checkpoint?.updated_at && (
              <KVRow
                label="last checkpoint"
                value={
                  <span className="font-mono text-[11px]">
                    <Time value={connector.checkpoint.updated_at} mode="relative" />
                  </span>
                }
              />
            )}
            <KVRow
              label="state"
              value={
                <span
                  className={`font-mono text-[11px] ${connector.state === 'healthy' ? 'text-foreground' : 'text-[color:var(--amber,oklch(0.72_0.12_70))]'}`}
                >
                  {connector.state}
                </span>
              }
            />
            {onPause || onRunNow ? (
              <div className="mt-3.5 flex gap-2">
                {onPause && (
                  <button
                    type="button"
                    onClick={onPause}
                    className="font-mono text-[11px] border border-border px-3 py-1.5 hover:bg-foreground/5 transition-colors"
                  >
                    pause poll
                  </button>
                )}
                {onRunNow && (
                  <button
                    type="button"
                    onClick={onRunNow}
                    className="font-mono text-[11px] border border-border px-3 py-1.5 hover:bg-foreground/5 transition-colors"
                  >
                    run now
                  </button>
                )}
              </div>
            ) : null}
          </div>

          {/* Config block */}
          <div>
            <div className="font-mono text-[9.5px] tracking-[0.14em] uppercase text-muted-foreground mb-2.5">
              config
            </div>
            <KVRow
              label="version"
              value={
                <span className="font-mono text-[11px]">
                  {connector.version ?? '—'}
                </span>
              }
            />
            {connector.checkpoint?.cursor && (
              <KVRow
                label="cursor"
                value={
                  <span className="font-mono text-[11px] break-all text-muted-foreground">
                    {connector.checkpoint.cursor.slice(0, 40)}
                    {connector.checkpoint.cursor.length > 40 ? '…' : ''}
                  </span>
                }
              />
            )}
            <KVRow
              label="instance"
              value={
                <span className="font-mono text-[11px] text-muted-foreground">
                  {connector.instance_id ?? '—'}
                </span>
              }
            />
          </div>

          {/* Routing rules [bu-5ywn2] */}
          <RoutingRulesList rules={routingRules} />
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// RecentEventsList [bu-5ywn2]
// ---------------------------------------------------------------------------

/** Status pill for event rows (inline mono variant, no Badge dep). */
function EventStatusPill({ status }: { status: string }) {
  const color =
    status === 'ingested'
      ? 'text-[color:var(--green,oklch(0.72_0.17_150))]'
      : status === 'failed' || status === 'error' || status === 'replay_failed'
        ? 'text-[color:var(--red,oklch(0.62_0.20_25))]'
        : status === 'filtered'
          ? 'text-muted-foreground'
          : 'text-foreground'
  return (
    <span className={`font-mono text-[10px] ${color}`}>{status}</span>
  )
}

interface RecentEventsListProps {
  events: ConnectorEventsResponse | null | undefined
  connectorKind: string
}

function RecentEventsList({ events, connectorKind }: RecentEventsListProps) {
  return (
    <div data-testid="recent-events-section">
      <div className="flex items-baseline gap-3 mb-2.5">
        <span className="font-mono text-[9.5px] tracking-[0.14em] uppercase text-muted-foreground">
          recent events
        </span>
        <Link
          to={`/ingestion?channels=${encodeURIComponent(connectorKind)}`}
          className="font-mono text-[9px] text-muted-foreground/60 underline underline-offset-2 decoration-border hover:text-muted-foreground transition-colors"
        >
          view all
        </Link>
      </div>
      {!events || !events.events || events.events.length === 0 ? (
        <p
          className="font-mono text-[11px] text-muted-foreground/50 italic"
          data-testid="recent-events-empty"
        >
          No recent events.
        </p>
      ) : (
        <div className="space-y-0" data-testid="recent-events-list">
          {events.events.map((evt) => (
            <div
              key={evt.id}
              className="flex items-baseline gap-3 py-1.5 border-b border-border/30 last:border-b-0 min-w-0"
            >
              <span className="font-mono text-[10px] text-muted-foreground/60 shrink-0 w-[12ch] truncate">
                {evt.received_at ? (
                  <Time value={evt.received_at} mode="relative" className="inline" />
                ) : (
                  '—'
                )}
              </span>
              <EventStatusPill status={evt.status} />
              {(evt.error_detail || evt.filter_reason) && (
                <span className="font-mono text-[10px] text-muted-foreground/50 truncate min-w-0">
                  {(evt.error_detail ?? evt.filter_reason ?? '').slice(0, 60)}
                </span>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// IncidentList [bu-5ywn2]
// ---------------------------------------------------------------------------

interface IncidentListProps {
  incidents: ConnectorIncidentsResponse | null | undefined
}

function IncidentList({ incidents }: IncidentListProps) {
  return (
    <div data-testid="incident-list-section">
      <div className="font-mono text-[9.5px] tracking-[0.14em] uppercase text-muted-foreground mb-2.5">
        incidents
      </div>
      {!incidents || !incidents.incidents || incidents.incidents.length === 0 ? (
        <p
          className="font-mono text-[11px] text-muted-foreground/50 italic"
          data-testid="incident-list-empty"
        >
          No incidents recorded.
        </p>
      ) : (
        <div className="space-y-0" data-testid="incident-list">
          {incidents.incidents.map((inc) => (
            <div
              key={inc.id}
              className="flex items-baseline gap-3 py-1.5 border-b border-border/30 last:border-b-0 min-w-0"
            >
              <span className="font-mono text-[10px] text-muted-foreground/60 shrink-0 w-[12ch] truncate">
                {inc.received_at ? (
                  <Time value={inc.received_at} mode="relative" className="inline" />
                ) : (
                  '—'
                )}
              </span>
              <EventStatusPill status={inc.status} />
              {(inc.error_detail || inc.filter_reason) && (
                <span className="font-mono text-[10px] text-muted-foreground/50 truncate min-w-0">
                  {(inc.error_detail ?? inc.filter_reason ?? '').slice(0, 60)}
                </span>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// RoutingRulesList [bu-5ywn2]
// ---------------------------------------------------------------------------

interface RoutingRulesListProps {
  rules: ConnectorRoutingRulesResponse | null | undefined
}

function RoutingRulesList({ rules }: RoutingRulesListProps) {
  return (
    <div data-testid="routing-rules-section">
      <div className="font-mono text-[9.5px] tracking-[0.14em] uppercase text-muted-foreground mb-2.5">
        routing rules
      </div>
      {!rules || !rules.rules || rules.rules.length === 0 ? (
        <p
          className="font-mono text-[11px] text-muted-foreground/50 italic"
          data-testid="routing-rules-empty"
        >
          No routing rules reference this connector.
        </p>
      ) : (
        <div className="space-y-0" data-testid="routing-rules-list">
          {rules.rules.map((rule) => (
            <Link
              key={rule.id}
              to="/ingestion/filters"
              className="flex items-baseline gap-3 py-1.5 border-b border-border/30 last:border-b-0 hover:bg-foreground/[0.03] transition-colors -mx-1 px-1"
            >
              <span className="font-mono text-[10px] text-muted-foreground shrink-0">
                #{rule.priority}
              </span>
              <span className="font-mono text-[10px] text-foreground/80 shrink-0">
                {rule.action}
              </span>
              <span className="font-mono text-[9.5px] text-muted-foreground/60 truncate min-w-0">
                {rule.name ?? rule.rule_type}
              </span>
              {!rule.enabled && (
                <span className="font-mono text-[9px] text-muted-foreground/40 ml-auto shrink-0">
                  disabled
                </span>
              )}
            </Link>
          ))}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function livenessText(liveness: string): string {
  if (liveness === 'online') return 'text-[color:var(--green,oklch(0.72_0.17_150))]'
  if (liveness === 'stale') return 'text-[color:var(--amber,oklch(0.72_0.12_70))]'
  return 'text-[color:var(--red,oklch(0.62_0.20_25))]'
}

function describeConnector(connector: ConnectorDetail): string {
  const t = connector.connector_type.replace(/_/g, ' ')
  return `${t.charAt(0).toUpperCase() + t.slice(1)} connector: ${connector.endpoint_identity}.`
}

function deriveSparkline(stats: ConnectorStats | undefined): number[] {
  if (!stats?.timeseries?.length) return Array(24).fill(0)

  // Take up to the last 24 timeseries buckets (hourly)
  const buckets = stats.timeseries.slice(-24)
  const padded = Array(24).fill(0)
  buckets.forEach((b, i) => {
    const idx = 24 - buckets.length + i
    padded[idx] = b.messages_ingested
  })
  return padded
}
