import { Link } from 'react-router'
import { Time } from '@/components/ui/time'
import { Badge } from '../ui/badge'
import { Button } from '../ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '../ui/card'
import { EmptyState } from '../ui/empty-state'
import type { Issue } from '../../api/types'

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
          <EmptyState
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

            return (
            <div
              key={issue.issue_key}
              className="flex items-start justify-between gap-3 rounded-md border p-3"
            >
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
              <div className="flex flex-wrap items-center justify-end gap-1">
                {issue.link && (
                  <Button variant="ghost" size="sm" asChild>
                    <Link to={issue.link}>View</Link>
                  </Button>
                )}
                {canPing && (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => onPingButler?.(singleButler)}
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
                    onClick={() => onRunScheduleNow?.(singleButler)}
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
                    onClick={() => onRestore?.(issue.issue_key)}
                    disabled={isRestoring || !onRestore}
                    className="text-muted-foreground"
                  >
                    Restore
                  </Button>
                ) : (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => onDismiss?.(issue)}
                    disabled={isDismissing || !onDismiss}
                    className="text-muted-foreground"
                  >
                    Acknowledge
                  </Button>
                )}
              </div>
            </div>
            )
          })}
        </div>
      </CardContent>
    </Card>
  )
}
