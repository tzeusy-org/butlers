/**
 * ConnectorRosterRow — one row in the connectors roster table.
 *
 * Columns (left → right):
 *   health dot · channel name+kind · function description+meta · sparkline ·
 *   auth pill · events · sessions · cost · disclosure
 *
 * The auth pill uses the same status label and color as the AttentionStrip
 * and the connector detail ReauthCallout (per spec AC2: consistent treatment).
 *
 * A left-rail severity indicator appears for non-ok connectors: red for
 * needs_reauth, amber for degraded/expiring.
 *
 * Design: hairline-divided rows, no card chrome. Health dot is 6px circle.
 * Mono numeric cells. Serif function gloss. No animations beyond hover tint.
 *
 * Spec: openspec/changes/complete-ingestion-redesign-parity/specs/
 *       dashboard-ingestion-dispatch-console/spec.md §"Connectors Roster"
 * Reference: (ingestion dispatch redesign, graduated) ingestion-connectors-a.jsx §ConnectorRow
 */

import { Link } from 'react-router'
import { Time } from '@/components/ui/time'
import type { ConnectorSummary } from '@/api/types'
import { Sparkline } from './Sparkline'
import {
  deriveConnectorDispatchInfo,
  authStatusLabel,
  authStatusColor,
  healthDotColor,
} from './connector-auth'
import { CONNECTOR_ROSTER_GRID_COLUMNS } from './layout'

interface ConnectorRosterRowProps {
  connector: ConnectorSummary
  /** Pre-computed 24h hourly spark data (length-24 array). Absent → all zeros. */
  spark24h?: number[]
  /** Pre-computed 24h event count. Falls back to today.messages_ingested. */
  events24h?: number
  /** Pre-computed 24h session count. */
  sessions24h?: number
  /** Pre-computed 24h cost in USD. */
  cost24h?: number
}

function formatNum(n: number): string {
  if (n >= 10_000) return Math.round(n / 1000) + 'k'
  if (n >= 1000) return (n / 1000).toFixed(1) + 'k'
  return String(n)
}

function formatCost(n: number): string {
  if (n === 0) return '—'
  if (n < 0.01) return '<$0.01'
  return `$${n.toFixed(2)}`
}

/**
 * Dense hairline-divided roster row for one connector.
 *
 * Click the disclosure link or the row to navigate to connector detail.
 */
export function ConnectorRosterRow({
  connector,
  spark24h,
  events24h,
  sessions24h,
  cost24h,
}: ConnectorRosterRowProps) {
  const c = connector
  const info = deriveConnectorDispatchInfo(c)
  const detailPath = `/ingestion/connectors/${encodeURIComponent(c.connector_type)}/${encodeURIComponent(c.endpoint_identity)}`

  const bars = spark24h ?? Array(24).fill(0)
  const eventsCount = events24h ?? c.today?.messages_ingested ?? 0
  const sessionsCount = sessions24h ?? 0
  const costValue = cost24h ?? 0

  const authLabel = authStatusLabel(info.authStatus)
  const authColorClass = authStatusColor(info.authStatus)
  const dotColorClass = healthDotColor(info.health)

  // Left rail severity color for non-ok connectors
  const railColorClass =
    info.authStatus === 'needs_reauth'
      ? 'bg-[color:var(--red,oklch(0.62_0.20_25))]'
      : info.health !== 'ok'
        ? 'bg-[color:var(--amber,oklch(0.72_0.12_70))]'
        : null

  const displayName = c.connector_type.replace(/_/g, ' ')
  const connectorKind = deriveKind(c)

  return (
    <div
      className="relative grid gap-x-4 py-4 border-b border-border/60 items-center hover:bg-foreground/[0.015] transition-colors"
      style={{ gridTemplateColumns: CONNECTOR_ROSTER_GRID_COLUMNS }}
      data-testid={`connector-row-${c.connector_type}`}
    >
      {/* Left severity rail */}
      {railColorClass && (
        <div
          aria-hidden="true"
          className={`absolute left-0 top-0 bottom-0 w-0.5 ${railColorClass}`}
        />
      )}

      {/* Health dot */}
      <span
        className={`w-1.5 h-1.5 rounded-full shrink-0 ${dotColorClass}`}
        aria-label={`health: ${info.health}`}
      />

      {/* Channel name + kind */}
      <div className="min-w-0">
        <div className="text-sm font-medium tracking-[-0.01em] truncate capitalize">
          {displayName}
        </div>
        <div className="font-mono text-[10px] text-muted-foreground/70 tracking-[0.04em] truncate">
          {connectorKind}
        </div>
      </div>

      {/* Function gloss + meta */}
      <div className="min-w-0">
        <div className="font-serif text-[13px] text-foreground leading-[1.4] line-clamp-1">
          {describeConnector(c)}
        </div>
        <div className="flex items-baseline gap-2 mt-0.5">
          {c.last_heartbeat_at ? (
            <span className="font-mono text-[10px] text-muted-foreground/60">
              last ·{' '}
              <Time value={c.last_heartbeat_at} mode="relative" className="inline" />
            </span>
          ) : (
            <span className="font-mono text-[10px] text-muted-foreground/40">last · never</span>
          )}
        </div>
      </div>

      {/* 24h sparkline */}
      <div className="flex flex-col gap-1" aria-hidden="true">
        <Sparkline data={bars} height={24} />
        <div className="flex justify-between font-mono text-[9px] text-muted-foreground/40 tracking-[0.04em]">
          <span>00</span>
          <span>12</span>
          <span>24</span>
        </div>
      </div>

      {/* Auth pill */}
      <div>
        <div className="inline-flex items-center gap-1.5">
          <span
            className={`w-1 h-1 rounded-full ${dotColorClass}`}
            aria-hidden="true"
          />
          <span
            className={`font-mono text-[10px] tracking-[0.06em] uppercase ${authColorClass}`}
            data-testid={`auth-status-${c.connector_type}`}
          >
            {authLabel}
          </span>
        </div>
        <div className="font-mono text-[10px] text-muted-foreground/50 mt-0.5 block truncate max-w-[110px]">
          {info.authNote}
        </div>
      </div>

      {/* Events */}
      <div className="font-mono text-[12px] tabular-nums text-right">
        {formatNum(eventsCount)}
      </div>

      {/* Sessions */}
      <div className="font-mono text-[12px] tabular-nums text-right text-muted-foreground">
        {sessionsCount > 0 ? sessionsCount : '—'}
      </div>

      {/* Cost */}
      <div
        className={`font-mono text-[12px] tabular-nums text-right ${costValue > 0 ? 'text-foreground' : 'text-muted-foreground/50'}`}
      >
        {formatCost(costValue)}
      </div>

      {/* Disclosure */}
      <Link
        to={detailPath}
        aria-label={`Open ${displayName} connector detail`}
        className="font-mono text-[13px] text-muted-foreground hover:text-foreground transition-colors justify-self-end"
      >
        ›
      </Link>
    </div>
  )
}

/** Derive a short kind label from connector metadata. */
function deriveKind(c: ConnectorSummary): string {
  // Infer from connector_type naming conventions
  const t = c.connector_type.toLowerCase()
  if (t.includes('webhook') || t.includes('whatsapp') || t.includes('telegram')) return 'webhook'
  if (t.includes('imap') || t.includes('gmail') || t.includes('email')) return 'imap'
  if (t.includes('spotify') || t.includes('calendar') || t.includes('notion')) return 'poll'
  if (t.includes('home_assistant')) return 'long-poll'
  return 'poll'
}

/** Produce a short serif description gloss from connector metadata. */
function describeConnector(c: ConnectorSummary): string {
  const t = c.connector_type.replace(/_/g, ' ')
  return `${t} · ${c.endpoint_identity}`
}
