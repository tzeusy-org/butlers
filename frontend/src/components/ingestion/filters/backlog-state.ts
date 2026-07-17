import type { PipelineStats } from '@/api/types'

export interface AvailablePipelineBacklog {
  failedTotal: number
  replayPendingTotal: number
  writtenOffTotal: number
  activeTotal: number
}

/**
 * The pipeline endpoint deliberately keeps its DB-backed backlog envelope
 * independent from Prometheus funnel aggregates. Never substitute zero when
 * this envelope is absent or unavailable: that would misstate an unknown
 * execution backlog as an empty one.
 */
export function getAvailablePipelineBacklog(
  stats: PipelineStats | undefined,
): AvailablePipelineBacklog | null {
  if (
    stats?.backlog_available !== true ||
    typeof stats.failed_total !== 'number' ||
    typeof stats.replay_pending_total !== 'number' ||
    typeof stats.written_off_total !== 'number'
  ) {
    return null
  }

  return {
    failedTotal: stats.failed_total,
    replayPendingTotal: stats.replay_pending_total,
    writtenOffTotal: stats.written_off_total,
    activeTotal: stats.failed_total + stats.replay_pending_total,
  }
}
