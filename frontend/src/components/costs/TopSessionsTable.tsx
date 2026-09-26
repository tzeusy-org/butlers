import { Link } from 'react-router'

import { Badge } from '../ui/badge'
import { Section, SectionContent, SectionHeader, SectionTitle } from '../ui/Section'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '../ui/table'
import { SourceDegradedNote } from '@/components/ui/query-boundary'
import { Time } from '@/components/ui/time'
import { formatCostUsd } from '@/lib/format-cost'

import type { TopSession } from '../../api/types'

interface TopSessionsTableProps {
  sessions: TopSession[]
  isLoading?: boolean
  /** The direct Overview top-sessions query failed before session data was available. */
  isUnavailable?: boolean
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return String(n)
}

export default function TopSessionsTable({ sessions, isLoading, isUnavailable }: TopSessionsTableProps) {
  if (isLoading) {
    return (
      <Section>
        <SectionHeader>
          <SectionTitle>Most Expensive Sessions</SectionTitle>
        </SectionHeader>
        <SectionContent>
          <div className="space-y-2">
            {Array.from({ length: 5 }).map((_, i) => (
              <div key={i} className="h-10 rounded bg-muted" />
            ))}
          </div>
        </SectionContent>
      </Section>
    )
  }

  if (isUnavailable) {
    return (
      <Section>
        <SectionHeader>
          <SectionTitle>Most Expensive Sessions</SectionTitle>
        </SectionHeader>
        <SectionContent>
          <SourceDegradedNote
            label="Top sessions"
            testId="top-sessions-unavailable"
          />
        </SectionContent>
      </Section>
    )
  }

  if (sessions.length === 0) {
    return (
      <Section>
        <SectionHeader>
          <SectionTitle>Most Expensive Sessions</SectionTitle>
        </SectionHeader>
        <SectionContent>
          <p className="text-sm text-muted-foreground">No session data available</p>
        </SectionContent>
      </Section>
    )
  }

  return (
    <Section>
      <SectionHeader>
        <SectionTitle>Most Expensive Sessions</SectionTitle>
      </SectionHeader>
      <SectionContent>
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
                    <Time value={session.started_at} mode="absolute" precision="minute" compact />
                  </Link>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </SectionContent>
    </Section>
  )
}
