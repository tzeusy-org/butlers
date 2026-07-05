import type { MouseEvent } from 'react'
import { Link } from 'react-router'
import { ButlerMark } from '@/components/ui/ButlerMark'
import { DisclosureRow } from '@/components/ui/DisclosureRow'
import { Time } from '@/components/ui/time'
import { cn } from '@/lib/utils'
import { Badge } from '../ui/badge'
import { Button } from '../ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '../ui/card'
import { EmptyState } from '../ui/empty-state'
import { ErrorState } from '../ui/error-state'
import { Skeleton } from '../ui/skeleton'
import type { AuditLogEntry, Issue } from '../../api/types'

/**
 * Audit-derived issue groups have real occurrences to drill into (each
 * `public.audit_log` error row that fed the group). Live reachability
 * issues ("unreachable") are synthetic single-occurrence entries, not
 * stored rows, so there is nothing behind them to fetch.
 */
function hasDrillableOccurrences(issue: Issue): boolean {
  return issue.type.startsWith('audit_error_group:') || issue.type.startsWith('scheduled_task_failure:')
}

/** Stop a nested control's click from bubbling to the row's DisclosureRow toggle. */
function stopRowToggle(e: MouseEvent) {
  e.stopPropagation()
}

interface IssuesPanelProps {
  issues: Issue[]
  isLoading?: boolean
  isError?: boolean
  /** Called with the full issue when the user acknowledges it. */
  onDismiss?: (issue: Issue) => void
  /** Disables the Acknowledge control while an ack is in flight. */
  isDismissing?: boolean
  /** Called with an issue's stable key when the user restores (undismisses) it. */
  onRestore?: (issueKey: string) => void
  /** Disables the Restore control while a restore is in flight. */
  isRestoring?: boolean
  /**
   * When true, this panel is showing acknowledged issues: it renders a
   * "Restore" affordance (via {@link onRestore}) instead of "Acknowledge",
   * and uses copy tuned for the acknowledged view.
   */
  dismissedView?: boolean
  /**
   * Called with a butler name when the user pings it to recheck reachability
   * right now (JARVIS audit move 6, bu-86c4c.15). Real backend: a live MCP
   * ping via `GET /api/butlers/{name}`. Only rendered for single-butler
   * "unreachable" issues, where "is it back yet?" is the operator's question.
   */
  onPingButler?: (butlerName: string) => void
  /** Butler name currently being pinged, if any (disables its row's Ping control). */
  pendingPingButler?: string | null
  /**
   * Called with a butler name when the user forces its scheduler to run now
   * (JARVIS audit move 6, bu-86c4c.15). Real backend: `POST
   * /api/butlers/{name}/tick`. Rendered for any single-butler issue.
   */
  onRunScheduleNow?: (butlerName: string) => void
  /** Butler name currently being ticked, if any (disables its row's control). */
  pendingRunNowButler?: string | null
  /**
   * `issue_key` of the currently-expanded row, if any (JARVIS audit move 6,
   * slice 3). At most one row's occurrences are fetched/shown at a time.
   */
  expandedIssueKey?: string | null
  /** Called with an issue's key when its disclosure row is toggled. */
  onToggleOccurrences?: (issueKey: string) => void
  /** Occurrences for the currently-expanded issue (empty until it settles). */
  occurrences?: AuditLogEntry[]
  /** True while the expanded issue's occurrences are being fetched. */
  occurrencesLoading?: boolean
  /** True if the occurrences fetch for the expanded issue failed. */
  occurrencesError?: boolean
  /**
   * `issue_key` of the row currently selected by j/k list-triage
   * (bu-qvnce.11 slice 4, `useListTriage` on IssuesPage). Highlights that
   * row and gives it the `issue-row` testid + `data-issue-key` so IssuesPage
   * can sync DOM focus to it, mirroring ApprovalsPage's RailItem selection.
   */
  selectedIssueKey?: string | null
}

/**
 * Occurrences list for one expanded issue group -- the individual
 * `public.audit_log` rows behind its "Seen Nx" count, each carrying a
 * ButlerMark chip and (when present) a link to the session that produced it.
 */
function OccurrencesPanel({
  occurrences,
  isLoading,
  isError,
}: {
  occurrences: AuditLogEntry[]
  isLoading?: boolean
  isError?: boolean
}) {
  if (isLoading) {
    return (
      <div className="space-y-1.5 border-t px-3 py-2">
        {Array.from({ length: 3 }).map((_, i) => (
          <Skeleton key={i} className="h-4 w-full" />
        ))}
      </div>
    )
  }

  if (isError) {
    return (
      <p className="border-t px-3 py-2 text-xs text-muted-foreground">
        Could not load occurrences. Try again shortly.
      </p>
    )
  }

  if (occurrences.length === 0) {
    return (
      <p className="border-t px-3 py-2 text-xs text-muted-foreground">
        No occurrences found for this group.
      </p>
    )
  }

  return (
    <ul className="space-y-1.5 border-t px-3 py-2">
      {occurrences.map((entry) => (
        <li key={entry.id} className="flex items-center gap-2 text-xs">
          <Time value={entry.ts} mode="relative" />
          <ButlerMark name={entry.actor} size={14} />
          <span className="text-muted-foreground">{entry.actor}</span>
          <code className="rounded bg-muted px-1 py-0.5 font-mono text-[11px]">
            {entry.action}
          </code>
          {entry.request_id && (
            <Link
              to={`/sessions?request=${encodeURIComponent(entry.request_id)}`}
              className="ml-auto shrink-0 text-muted-foreground hover:text-foreground hover:underline"
            >
              Session →
            </Link>
          )}
        </li>
      ))}
    </ul>
  )
}

export default function IssuesPanel({
  issues,
  isLoading,
  isError,
  onDismiss,
  isDismissing,
  onRestore,
  isRestoring,
  dismissedView = false,
  onPingButler,
  pendingPingButler,
  onRunScheduleNow,
  pendingRunNowButler,
  expandedIssueKey = null,
  onToggleOccurrences,
  occurrences = [],
  occurrencesLoading,
  occurrencesError,
  selectedIssueKey = null,
}: IssuesPanelProps) {
  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Issues</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-3">
            {Array.from({ length: 2 }).map((_, i) => (
              <div key={i} className="h-12 animate-pulse rounded bg-muted" />
            ))}
          </div>
        </CardContent>
      </Card>
    )
  }

  if (isError) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Issues</CardTitle>
        </CardHeader>
        <CardContent>
          <ErrorState
            title="Could not load issues."
            description="The issues feed is unavailable right now. Retrying automatically; check the backend if this persists."
          />
        </CardContent>
      </Card>
    )
  }

  if (issues.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Issues</CardTitle>
        </CardHeader>
        <CardContent>
          <EmptyState
            variant="page"
            title={dismissedView ? 'No acknowledged issues.' : 'No issues recorded.'}
            description={
              dismissedView
                ? 'Issues you acknowledge appear here until they recur, or you restore them.'
                : 'Issues appear when butlers report errors or warnings.'
            }
          />
        </CardContent>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle>{dismissedView ? 'Acknowledged issues' : 'Issues'}</CardTitle>
        <Badge variant={dismissedView ? 'secondary' : 'destructive'}>{issues.length}</Badge>
      </CardHeader>
      <CardContent>
        <div className="space-y-3">
          {issues.map((issue) => {
            // Remedies only make sense once a single, real butler is
            // identified — a grouped "N butlers" issue has no single target
            // to ping or tick.
            const singleButler =
              issue.butler && issue.butler !== 'multiple' ? issue.butler : null
            const canPing =
              !dismissedView && singleButler && issue.type === 'unreachable' && !!onPingButler
            const canRunNow = !dismissedView && singleButler && !!onRunScheduleNow
            const isPinging = !!singleButler && pendingPingButler === singleButler
            const isRunningNow = !!singleButler && pendingRunNowButler === singleButler

            const drillable = hasDrillableOccurrences(issue) && !!onToggleOccurrences
            const expanded = drillable && expandedIssueKey === issue.issue_key

            // The row's own content — shared between the drillable
            // (DisclosureRow-wrapped) and non-drillable (plain div) cases.
            const rowContent = (
              <>
                <div className="flex-1 space-y-1">
                  <div className="flex items-center gap-2">
                    <Badge variant={issue.severity === 'critical' ? 'destructive' : 'secondary'}>
                      {issue.severity}
                    </Badge>
                    <span className="text-sm font-medium">
                      {issue.butlers && issue.butlers.length > 1
                        ? `${issue.butlers.length} butlers`
                        : issue.butler}
                    </span>
                  </div>
                  <p className="text-sm text-muted-foreground">{issue.description}</p>
                  <p className="text-xs text-muted-foreground">
                    Seen {issue.occurrences ?? 1}x · First:{' '}
                    {issue.first_seen_at ? <Time value={issue.first_seen_at} mode="smart" /> : 'unknown'}
                    {' '}· Last:{' '}
                    {issue.last_seen_at ? <Time value={issue.last_seen_at} mode="smart" /> : 'unknown'}
                  </p>
                </div>
                {/* stopRowToggle on each control: these are real interactive
                    controls nested inside the row's own DisclosureRow toggle
                    target (when drillable) — a click here must act on the
                    control, not also expand/collapse the occurrences panel.
                    (Applied per-control, not via a wrapping div's onClick, so
                    the wrapper stays a plain non-interactive <div>.) */}
                <div className="flex flex-wrap items-center justify-end gap-1">
                  {issue.link && (
                    <Button variant="ghost" size="sm" asChild onClick={stopRowToggle}>
                      <Link to={issue.link}>View</Link>
                    </Button>
                  )}
                  {canPing && (
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={(e) => {
                        stopRowToggle(e)
                        onPingButler?.(singleButler)
                      }}
                      disabled={isPinging || !onPingButler}
                      className="text-muted-foreground"
                    >
                      {isPinging ? 'Pinging…' : 'Ping butler'}
                    </Button>
                  )}
                  {canRunNow && (
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={(e) => {
                        stopRowToggle(e)
                        onRunScheduleNow?.(singleButler)
                      }}
                      disabled={isRunningNow || !onRunScheduleNow}
                      className="text-muted-foreground"
                    >
                      {isRunningNow ? 'Running…' : 'Run schedule now'}
                    </Button>
                  )}
                  {dismissedView ? (
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={(e) => {
                        stopRowToggle(e)
                        onRestore?.(issue.issue_key)
                      }}
                      disabled={isRestoring || !onRestore}
                      className="text-muted-foreground"
                    >
                      Restore
                    </Button>
                  ) : (
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={(e) => {
                        stopRowToggle(e)
                        onDismiss?.(issue)
                      }}
                      disabled={isDismissing || !onDismiss}
                      className="text-muted-foreground"
                    >
                      Acknowledge
                    </Button>
                  )}
                </div>
              </>
            )

            return (
            <div
              key={issue.issue_key}
              data-testid="issue-row"
              data-issue-key={issue.issue_key}
              tabIndex={-1}
              className={cn(
                'rounded-md border',
                selectedIssueKey === issue.issue_key && 'border-foreground/40 bg-muted/40',
              )}
            >
              {drillable ? (
                <DisclosureRow
                  expanded={expanded}
                  onToggle={() => onToggleOccurrences?.(issue.issue_key)}
                  controlsId={`issue-occurrences-${issue.issue_key}`}
                  className="flex items-start justify-between gap-3 p-3"
                >
                  {rowContent}
                </DisclosureRow>
              ) : (
                <div className="flex items-start justify-between gap-3 p-3">{rowContent}</div>
              )}
              {expanded && (
                <div id={`issue-occurrences-${issue.issue_key}`}>
                  <OccurrencesPanel
                    occurrences={occurrences}
                    isLoading={occurrencesLoading}
                    isError={occurrencesError}
                  />
                </div>
              )}
            </div>
            )
          })}
        </div>
      </CardContent>
    </Card>
  )
}
