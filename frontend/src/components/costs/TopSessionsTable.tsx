import { Link } from 'react-router'

import { Badge } from '../ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from '../ui/card'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '../ui/table'
import { formatCostUsd } from '@/lib/format-cost'

import type { TopSession } from '../../api/types'

interface TopSessionsTableProps {
  sessions: TopSession[]
  isLoading?: boolean
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return String(n)
}

function formatTime(iso: string): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return d.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export default function TopSessionsTable({ sessions, isLoading }: TopSessionsTableProps) {
  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Most Expensive Sessions</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-2">
            {Array.from({ length: 5 }).map((_, i) => (
              <div key={i} className="h-10 rounded bg-muted" />
            ))}
          </div>
        </CardContent>
      </Card>
    )
  }

  if (sessions.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Most Expensive Sessions</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">No session data available</p>
        </CardContent>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Most Expensive Sessions</CardTitle>
      </CardHeader>
      <CardContent>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-10">#</TableHead>
              <TableHead>Butler</TableHead>
              <TableHead>Model</TableHead>
              <TableHead className="text-right">Tokens</TableHead>
              <TableHead className="text-right">Cost</TableHead>
              <TableHead className="text-right">Time</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {sessions.map((session, idx) => (
              <TableRow key={session.session_id}>
                <TableCell className="text-muted-foreground">{idx + 1}</TableCell>
                <TableCell>
                  <Link to={`/butlers/${session.butler}?tab=spend`} className="hover:underline">
                    <Badge variant="secondary">{session.butler}</Badge>
                  </Link>
                </TableCell>
                <TableCell className="text-muted-foreground text-xs">{session.model}</TableCell>
                <TableCell className="text-right tabular-nums text-xs">
                  {formatTokens(session.input_tokens)} / {formatTokens(session.output_tokens)}
                </TableCell>
                <TableCell className="text-right tabular-nums font-medium">
                  {formatCostUsd(session.cost_usd)}
                </TableCell>
                <TableCell className="text-right text-xs text-muted-foreground">
                  <Link to={`/sessions/${session.session_id}`} className="hover:underline">
                    {formatTime(session.started_at)}
                  </Link>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  )
}
