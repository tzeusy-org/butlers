/**
 * ArchiveCandidatesList — review queue for flag-only archive candidates (bu-u19yv).
 *
 * A candidate is an ACTIVE connector identity the backend flags with
 * `archive_candidate: true` — it last heartbeated >30d ago AND a newer,
 * currently-online sibling of the same connector_type exists. These are
 * SUGGESTIONS for a human to review, never auto-archived: auto-archiving risks
 * silencing a merely-quiet live connector.
 *
 * Honesty contract:
 * - Candidates STILL appear in the active roster with their true (offline)
 *   liveness and still count toward its KPIs — this queue is an additive
 *   overlay, not a filter. It never files a genuinely-failing live connector as
 *   "just an archive candidate": the backend only flags identities offline
 *   >30d, and this component renders the suggestion, not a health verdict.
 * - Each row offers a one-click archive that reuses the existing audit-logged
 *   archive endpoint (no new archive mechanics). On success the identity moves
 *   to the collapsed "archived" section and drops out of the fleet rollups.
 * - Each row also links to the connector detail route so its history stays
 *   reachable before a human decides to archive.
 *
 * Spec: openspec/specs/dashboard-ingestion-dispatch-console/spec.md
 *       §"Archived Connector Identities" — archive review queue
 */

import { Link } from 'react-router'
import type { ConnectorSummary } from '@/api/types'
import { useArchiveConnector } from '@/hooks/use-ingestion'

interface ArchiveCandidatesListProps {
  connectors: ConnectorSummary[]
}

/** Whole-days since an ISO timestamp, or null when unparseable/absent. */
function daysSince(iso: string | null | undefined): number | null {
  if (!iso) return null
  const then = Date.parse(iso)
  if (Number.isNaN(then)) return null
  return Math.floor((Date.now() - then) / 86_400_000)
}

/**
 * Review queue of archive-candidate identities with a one-click archive action.
 *
 * Renders nothing when there are no candidates. Filtering to
 * `archive_candidate === true` happens here so the caller can pass the full
 * active roster.
 */
export function ArchiveCandidatesList({ connectors }: ArchiveCandidatesListProps) {
  const archive = useArchiveConnector()

  const candidates = connectors.filter((c) => c.archive_candidate)
  if (candidates.length === 0) return null

  const sorted = [...candidates].sort(
    (a, b) =>
      a.connector_type.localeCompare(b.connector_type) ||
      a.endpoint_identity.localeCompare(b.endpoint_identity),
  )

  const pendingKey =
    archive.isPending && archive.variables
      ? `${archive.variables.connectorType}:${archive.variables.endpointIdentity}`
      : null

  return (
    <div data-testid="archive-candidates-section" className="mt-9">
      {/* Eyebrow — a review CALL TO ACTION, always visible (not collapsed) */}
      <div className="flex items-baseline gap-3 mb-2.5">
        <span className="font-mono text-[9.5px] tracking-[0.14em] uppercase text-muted-foreground">
          review · suggested for archiving
        </span>
        <span className="font-mono text-[9.5px] text-muted-foreground/50">
          {candidates.length} candidate{candidates.length !== 1 ? 's' : ''}
        </span>
      </div>

      <p className="font-serif italic text-[12.5px] text-muted-foreground/80 mb-3 max-w-prose">
        Offline &gt;30 days with a newer online identity of the same connector —
        likely superseded. Archiving is never automatic; review each and archive
        if it is truly dead.
      </p>

      <div data-testid="archive-candidates-list">
        {sorted.map((c) => {
          const key = `${c.connector_type}:${c.endpoint_identity}`
          const detailPath = `/ingestion/connectors/${encodeURIComponent(
            c.connector_type,
          )}/${encodeURIComponent(c.endpoint_identity)}`
          const offlineDays = daysSince(c.last_heartbeat_at)
          const rowPending = pendingKey === key
          return (
            <div
              key={key}
              data-testid={`archive-candidate-row-${key}`}
              className="grid gap-x-4 py-3 border-b border-border/40 items-center"
              style={{ gridTemplateColumns: '14px 1fr auto auto' }}
            >
              {/* Amber dot — a review suggestion (not an alarm); its true
                  offline state is already shown in the active roster row above.
                  Amber is the dashboard's "attention/soft" status token. */}
              <span
                className="w-1.5 h-1.5 rounded-full bg-[var(--amber)]/60"
                aria-hidden="true"
              />

              {/* Type + identity, linking to detail so history stays reachable */}
              <Link to={detailPath} className="min-w-0 group">
                <div className="text-[13.5px] text-foreground font-medium capitalize truncate group-hover:underline">
                  {c.connector_type}
                </div>
                <div className="font-mono text-[10px] text-muted-foreground/60 truncate">
                  {c.endpoint_identity}
                </div>
              </Link>

              {/* Why it is a candidate */}
              <span className="font-mono text-[10px] text-muted-foreground whitespace-nowrap">
                {offlineDays != null ? `offline ${offlineDays}d` : 'offline'} · superseded
              </span>

              {/* One-click archive — reuses the audit-logged archive endpoint */}
              <button
                type="button"
                data-testid={`archive-candidate-action-${key}`}
                disabled={archive.isPending}
                onClick={() =>
                  archive.mutate({
                    connectorType: c.connector_type,
                    endpointIdentity: c.endpoint_identity,
                  })
                }
                className="font-mono text-[11px] border border-foreground px-3 py-1.5 hover:bg-foreground hover:text-background transition-colors disabled:opacity-40 disabled:cursor-not-allowed whitespace-nowrap"
              >
                {rowPending ? 'archiving…' : 'archive'}
              </button>
            </div>
          )
        })}
      </div>

      {archive.isError && (
        <p
          data-testid="archive-candidates-error"
          className="font-mono text-[10.5px] text-[var(--red-text)] mt-2"
        >
          Archive failed — the identity was not archived. Try again.
        </p>
      )}
    </div>
  )
}
