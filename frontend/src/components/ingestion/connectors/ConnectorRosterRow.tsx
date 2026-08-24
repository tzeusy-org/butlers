/**
 * ConnectorRosterRow — one row in the connectors roster table.
 *
 * Columns (left → right):
 *   health verdict (dot + word) · channel name+kind · function description+meta ·
 *   sparkline · auth pill · events (last 24h) · disclosure
 *
 * The auth pill uses the same status label and color as the AttentionStrip
 * and the connector detail ReauthCallout (per spec AC2: consistent treatment).
 * When the status is `needs_reauth`, the pill uses the shared recovery
 * resolver: registered OAuth, Passport pairing, or an honest unavailable
 * explanation (the same contract the connector detail ReauthCallout uses).
 *
 * A left-rail severity indicator appears for non-ok connectors: red for
 * needs_reauth, amber for degraded/expiring.
 *
 * Design: hairline-divided rows, no card chrome. The whole row is the
 * navigation target (a stretched link filling the row) with the chevron kept
 * as a visual disclosure cue only — it is no longer an independent click
 * target. State color is foreground/dot only, never a background fill.
 * The health verdict is a single dot + word (see connector-auth.ts
 * `healthVerdictWord`), replacing the former stacked liveness+state dots
 * that required memorizing which axis was which. Mono numeric cells. Serif
 * function gloss. No animations beyond hover tint.
 *
 * Spec: openspec/changes/complete-ingestion-redesign-parity/specs/
 *       dashboard-ingestion-dispatch-console/spec.md §"Connectors Roster"
 * Reference: (ingestion dispatch redesign, graduated) ingestion-connectors-a.jsx §ConnectorRow
 */

import { Link } from 'react-router'
import { Time } from '@/components/ui/time'
import type { ConnectorSummary } from '@/api/types'
import { ConnectorCheckpoints } from './ConnectorCheckpoints'
import { ConnectorDeviceBadges } from './ConnectorDeviceBadges'
import { Sparkline } from './Sparkline'
import {
  deriveConnectorDispatchInfo,
  authStatusLabel,
  authStatusColor,
  healthDotColor,
  healthTextColor,
  healthVerdictWord,
  resolveConnectorRecovery,
} from './connector-auth'
import { CONNECTOR_ROSTER_GRID_COLUMNS } from './layout'

interface ConnectorRosterRowProps {
  connector: ConnectorSummary
  /** Pre-computed 24h hourly spark data (length-24 array). Absent → all zeros. */
  spark24h?: number[]
  /**
   * Pre-computed 24h filtered/skip-routed spark data (bu-scyro). Rendered as a
   * visually-quiet second series on the same sparkline. Absent → no overlay.
   */
  spark24hFiltered?: number[]
  /** Pre-computed 24h event count. Falls back to sum of hourly_events. */
  events24h?: number
  /**
   * Real channel/kind for this connector_type from the available-connectors
   * catalog. Absent → kind is genuinely unknown and is not displayed
   * (never fabricated from name substrings).
   */
  catalogChannel?: string
  /**
   * Peak hourly value across every connector in the roster. When present,
   * the sparkline normalizes against this shared peak so bar heights are
   * comparable across rows instead of each row normalizing to its own max.
   */
  rosterSparkMax?: number
}

function formatNum(n: number): string {
  if (n >= 10_000) return Math.round(n / 1000) + 'k'
  if (n >= 1000) return (n / 1000).toFixed(1) + 'k'
  return String(n)
}

/**
 * Dense hairline-divided roster row for one connector.
 *
 * The whole row navigates to connector detail (click or Enter/Space while
 * focused); the disclosure chevron is a visual cue only.
 */
export function ConnectorRosterRow({
  connector,
  spark24h,
  spark24hFiltered,
  events24h,
  catalogChannel,
  rosterSparkMax,
}: ConnectorRosterRowProps) {
  const c = connector
  const info = deriveConnectorDispatchInfo(c)
  const detailPath = `/ingestion/connectors/${encodeURIComponent(c.connector_type)}/${encodeURIComponent(c.endpoint_identity)}`

  const bars = spark24h ?? Array(24).fill(0)
  // today.messages_ingested is already the 24h sum on the backend (derived from hourly_events).
  const eventsCount = events24h ?? c.today?.messages_ingested ?? 0

  const authLabel = authStatusLabel(info.authStatus)
  const authColorClass = authStatusColor(info.authStatus)
  const verdictWord = healthVerdictWord(c, info)
  const verdictDotClass = healthDotColor(info.health)
  const verdictTextClass = healthTextColor(info.health)

  // Left rail severity color for non-ok connectors
  const railColorClass =
    info.authStatus === 'needs_reauth'
      ? 'bg-[color:var(--red,oklch(0.62_0.20_25))]'
      : info.health !== 'ok'
        ? 'bg-[color:var(--amber,oklch(0.72_0.12_70))]'
        : null

  const displayName = c.connector_type.replace(/_/g, ' ')

  // The reauth pill doubles as the recovery action only for a known recovery
  // capability. The resolver is an allowlist so arbitrary connector types can
  // never become fabricated OAuth provider URLs.
  const recovery =
    info.authStatus === 'needs_reauth'
      ? resolveConnectorRecovery(c.connector_type, {
          connectorDetailPath: `${c.connector_type}/${c.endpoint_identity}`,
        })
      : null
  const authDisplayLabel =
    recovery?.kind === 'passport' && recovery.action === 'pair'
      ? 'pair'
      : recovery?.kind === 'unsupported'
        ? 'unavailable'
        : authLabel
  const authNote = recovery?.kind === 'unsupported' ? recovery.reason : info.authNote

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

      {/* Stretched row link — the whole row is the navigation target (click
          or keyboard Enter/Space). All other row content below stays
          position:static, so it paints (and hit-tests) BELOW this absolutely
          positioned link with no extra pointer-events wrangling needed. The
          reauth pill is the one exception: it is explicitly lifted above
          this link (relative z-10) so it stays independently clickable. */}
      <Link
        to={detailPath}
        aria-label={`Open ${displayName} connector detail`}
        className="absolute inset-0 z-0 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring focus-visible:ring-inset"
        data-testid={`row-link-${c.connector_type}`}
      />

      {/* Health verdict — single dot + word, folding liveness + health */}
      <div className="flex items-center gap-1.5">
        <span
          className={`w-1.5 h-1.5 rounded-full shrink-0 ${verdictDotClass}`}
          aria-hidden="true"
        />
        <span
          className={`font-mono text-[10px] tracking-[0.02em] ${verdictTextClass}`}
          data-testid={`health-verdict-${c.connector_type}`}
        >
          {verdictWord}
        </span>
      </div>

      {/* Channel name + kind */}
      <div className="min-w-0">
        <div className="text-sm font-medium tracking-[-0.01em] truncate capitalize">
          {displayName}
        </div>
        {catalogChannel && (
          <div className="font-mono text-[10px] text-muted-foreground/70 tracking-[0.04em] truncate">
            {catalogChannel}
          </div>
        )}
      </div>

      {/* Function gloss + meta */}
      <div className="min-w-0">
        <div className="font-serif text-[13px] text-foreground leading-[1.4] line-clamp-1">
          {c.endpoint_identity && c.endpoint_identity !== 'default'
            ? c.endpoint_identity
            : displayName}
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
      <div className="flex flex-col gap-1">
        <Sparkline
          data={bars}
          secondaryData={spark24hFiltered}
          maxValue={rosterSparkMax}
          height={24}
        />
        <div className="flex justify-between font-mono text-[9px] text-muted-foreground/40 tracking-[0.04em]">
          <span>00</span>
          <span>12</span>
          <span>24</span>
        </div>
      </div>

      {/* Auth pill — the reauth action when needs_reauth, otherwise a plain label */}
      <div>
        {recovery?.kind === 'oauth' ? (
          <a
            href={recovery.href}
            className={`relative z-10 inline-flex items-center gap-1 font-mono text-[10px] tracking-[0.06em] uppercase underline decoration-current/40 underline-offset-2 hover:decoration-current transition-colors ${authColorClass}`}
            data-testid={`auth-status-${c.connector_type}`}
            aria-label={`Re-authorize ${displayName}`}
          >
            {authDisplayLabel}
          </a>
        ) : recovery?.kind === 'passport' ? (
          <Link
            to={recovery.to}
            className={`relative z-10 inline-flex items-center gap-1 font-mono text-[10px] tracking-[0.06em] uppercase underline decoration-current/40 underline-offset-2 hover:decoration-current transition-colors ${authColorClass}`}
            data-testid={`auth-status-${c.connector_type}`}
            aria-label={
              recovery.action === 'pair'
                ? `Open ${displayName} pairing`
                : `Re-authorize ${displayName}`
            }
          >
            {authDisplayLabel}
          </Link>
        ) : (
          <span
            className={`font-mono text-[10px] tracking-[0.06em] uppercase ${authColorClass}`}
            data-testid={`auth-status-${c.connector_type}`}
          >
            {authDisplayLabel}
          </span>
        )}
        <div className="font-mono text-[10px] text-muted-foreground/50 mt-0.5 block truncate max-w-[110px]">
          {authNote}
        </div>
      </div>

      {/* Events (last 24h) */}
      <div className="font-mono text-[12px] tabular-nums text-right">{formatNum(eventsCount)}</div>

      {/* Disclosure — decorative visual cue; the stretched row link owns navigation */}
      <span aria-hidden="true" className="font-mono text-[13px] text-muted-foreground justify-self-end">
        ›
      </span>

      {/* Persisted checkpoint cursors owned by this runtime instance
          (bu-6jv4m.11). Storage state only: labelled and inspectable, with no
          liveness or health of their own. They used to appear as separate
          offline connectors in the roster above. */}
      {c.checkpoints && c.checkpoints.length > 0 && (
        <ConnectorCheckpoints checkpoints={c.checkpoints} connectorType={c.connector_type} />
      )}

      {/* Per-device liveness — only present for multi-device connector_types
          (e.g. OwnTracks). Wraps to its own implicit grid row below the main
          content, aligned under the channel/function columns. */}
      {c.devices && c.devices.length > 0 && <ConnectorDeviceBadges devices={c.devices} />}

      {/* Evidence-quality warnings are additive operational context. They do
          not replace or downgrade the transport health verdict above. */}
      {c.operational_warnings?.map((warning) => (
        <p
          key={warning}
          className="col-start-2 col-end-[-1] mt-1 font-serif text-[12px] leading-[1.45] text-[var(--amber-text)]"
          data-testid={`connector-warning-${c.connector_type}`}
        >
          {warning}
        </p>
      ))}
    </div>
  )
}
