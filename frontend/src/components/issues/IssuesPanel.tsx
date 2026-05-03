import { useState, useEffect } from 'react'
import { Link } from 'react-router'
import { Time } from '@/components/ui/time'
import { Badge } from '../ui/badge'
import { Button } from '../ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '../ui/card'
import type { Issue } from '../../api/types'

const DISMISSED_KEY = 'butlers-dismissed-issues'

function getDismissedIssues(): Set<string> {
  try {
    const raw = localStorage.getItem(DISMISSED_KEY)
    if (raw) return new Set(JSON.parse(raw))
  } catch {
    // ignore
  }
  return new Set()
}

function issueKey(issue: Issue): string {
  return `${issue.type}:${issue.error_message ?? issue.description}`
}

interface IssuesPanelProps {
  issues: Issue[]
  isLoading?: boolean
}

export default function IssuesPanel({ issues, isLoading }: IssuesPanelProps) {
  const [dismissed, setDismissed] = useState<Set<string>>(() => getDismissedIssues())

  useEffect(() => {
    localStorage.setItem(DISMISSED_KEY, JSON.stringify([...dismissed]))
  }, [dismissed])

  const visibleIssues = issues.filter(i => !dismissed.has(issueKey(i)))

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

  if (visibleIssues.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Issues</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">No issues recorded</p>
        </CardContent>
      </Card>
    )
  }

  const dismiss = (issue: Issue) => {
    setDismissed(prev => new Set([...prev, issueKey(issue)]))
  }

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle>Issues</CardTitle>
        <Badge variant="destructive">{visibleIssues.length}</Badge>
      </CardHeader>
      <CardContent>
        <div className="space-y-3">
          {visibleIssues.map((issue) => (
            <div
              key={issueKey(issue)}
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
                  onClick={() => dismiss(issue)}
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
