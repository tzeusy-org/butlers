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
 * - Archiving is reversible (bu-ep4ks.11 — the safety envelope for
 *   consequential actions): a click schedules the archive UNDO_WINDOW_MS out
 *   behind an "Undo" toast action, mirroring the undo-window pattern
 *   ButlersPage's board established for restore (bu-86c4c.15), rather than
 *   firing the mutation on click. The unarchive UI path back lives in
 *   ArchivedConnectorsList.
 *
 * Spec: openspec/specs/dashboard-ingestion-dispatch-console/spec.md
 *       §"Archived Connector Identities" — archive review queue
 */

import { Link } from 'react-router'
import { toast } from 'sonner'
import type { ConnectorSummary } from '@/api/types'
import { useArchiveConnector } from '@/hooks/use-ingestion'
import { UNDO_WINDOW_MS, useUndoWindow } from '@/hooks/use-undo-window'

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
  const archiveUndo = useUndoWindow('connector-archive')

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

  function handleArchive(connectorType: string, endpointIdentity: string, key: string) {
    if (archiveUndo.isScheduled(key)) return

    // archive.isError already renders the shared error banner below -- no
    // separate toast needed for the fired-but-failed case.
    archiveUndo.schedule(key, () => {
      archive.mutate({ connectorType, endpointIdentity })
    })

    toast(`Archiving ${endpointIdentity}`, {
      action: { label: 'Undo', onClick: () => archiveUndo.cancel(key) },
      duration: UNDO_WINDOW_MS,
    })
  }

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
        Offline &gt;30 days with a newer online identity of the same connector, so
        it is likely superseded. Archiving is never automatic; review each and archive
        if it is truly dead.
      </p>

      <div data-testid="archive-candidates-list">
        {sorted.map((c) => {
          const key = `${c.connector_type}:${c.endpoint_identity}`
          const detailPath = `/ingestion/connectors/${encodeURIComponent(
            c.connector_type,
          )}/${encodeURIComponent(c.endpoint_identity)}`
          const offlineDays = daysSince(c.last_heartbeat_at)
          const rowScheduled = archiveUndo.isScheduled(key)
          const rowPending = pendingKey === key || rowScheduled
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

              {/* Archive — reversible via the undo-window toast (bu-ep4ks.11) */}
              <button
                type="button"
                data-testid={`archive-candidate-action-${key}`}
                disabled={rowPending}
                onClick={() => handleArchive(c.connector_type, c.endpoint_identity, key)}
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
          Archive failed. The identity was not archived. Try again.
        </p>
      )}
    </div>
  )
}
