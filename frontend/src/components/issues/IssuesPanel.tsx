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
  /** Called with an issue's stable key when the user dismisses it. */
  onDismiss?: (issueKey: string) => void
  /** Disables the Dismiss control while a dismissal is in flight. */
  isDismissing?: boolean
}

export default function IssuesPanel({
  issues,
  isLoading,
  isError,
  onDismiss,
  isDismissing,
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
            title="No issues recorded."
            description="Issues appear when butlers report errors or warnings."
          />
        </CardContent>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle>Issues</CardTitle>
        <Badge variant="destructive">{issues.length}</Badge>
      </CardHeader>
      <CardContent>
        <div className="space-y-3">
          {issues.map((issue) => (
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
              <div className="flex items-center gap-1">
                {issue.link && (
                  <Button variant="ghost" size="sm" asChild>
                    <Link to={issue.link}>View</Link>
                  </Button>
                )}
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => onDismiss?.(issue.issue_key)}
                  disabled={isDismissing || !onDismiss}
                  className="text-muted-foreground"
                >
                  Dismiss
                </Button>
              </div>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  )
}
