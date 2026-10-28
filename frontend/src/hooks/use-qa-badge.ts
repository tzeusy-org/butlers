/**
 * Provides live badge counts for nav items.
 *
 * Returns a map from badgeKey → count or explicit availability state so the
 * Sidebar can render badge indicators without inventing a calm zero.
 *
 * The QA badge query is only fired when the QA butler is present in the
 * roster (i.e. the nav item will actually be visible), to avoid spurious
 * requests on instances that have no QA staffer deployed.
 */

import { useQaSummary } from './use-qa'
import { useButlers } from './use-butlers'
import {
  pendingApprovalMetricSourcesDegraded,
  useApprovalMetrics,
} from './use-approvals'

/** A sidebar badge with an explicit unavailable state. */
export type AvailabilityBadgeState = (
  | { kind: 'count'; count: number }
  | { kind: 'unavailable' }
) & {
  /** Optional presentation detail for a consumer that can offer source retry. */
  qaSource?: {
    source: 'discovery' | 'summary'
    stale: boolean
    retrying: boolean
    retry: () => Promise<boolean>
  }
}

/**
 * Returns the count of open QA escalations for the sidebar badge.
 *
 * This counts investigations the QA staffer escalated for human attention —
 * terminal cases (`unfixable`/`failed`) flagged as needing a human, still open
 * or closed within the last 7 days. Unlike the raw known-issues fingerprint
 * count, this is bounded and self-decaying: it only surfaces things a human can
 * act on, and entries age out once resolved. See `escalated_open_cases_sql`
 * (src/butlers/core/qa/severity.py).
 */
export function useQaEscalationsBadge(options?: { sourceDetails?: boolean }): AvailabilityBadgeState {
  const discovery = useButlers()
  const hasQa = discovery.data?.data.some((b) => b.name === 'qa') ?? false
  const summary = useQaSummary({ enabled: hasQa })
  // A healthy absence must win over any old summary cache (including errors).
  if (!discovery.isError && !hasQa) return { kind: 'count', count: 0 }

  // Discovery is the prerequisite. If both sources fail, repair it first; a
  // remaining summary error then offers its own retry rather than claiming
  // complete recovery from a successful roster read alone.
  const source = discovery.isError ? 'discovery' : summary.isError ? 'summary' : undefined
  if (source) {
    if (!options?.sourceDetails) return { kind: 'unavailable' }
    const query = source === 'discovery' ? discovery : summary
    const cachedCount = hasQa ? summary.data?.data.active_breakdown.escalated_open_cases : undefined
    return {
      ...(cachedCount === undefined
        ? { kind: 'unavailable' as const }
        : { kind: 'count' as const, count: cachedCount }),
      qaSource: {
        source,
        stale: query.data !== undefined,
        retrying: query.isFetching,
        retry: async () => (await query.refetch()).isSuccess,
      },
    }
  }
  return { kind: 'count', count: summary.data?.data.active_breakdown.escalated_open_cases ?? 0 }
}

/** Returns the pending-approval badge state without inventing a zero on partial metrics. */
export function useApprovalsPendingBadge(): AvailabilityBadgeState {
  const { data, isError } = useApprovalMetrics()
  if (isError || pendingApprovalMetricSourcesDegraded(data).length > 0) {
    return { kind: 'unavailable' }
  }
  return { kind: 'count', count: data?.data.total_pending ?? 0 }
}

/** Badge registry — approval badges carry explicit availability. */
export function useBadgeCounts(options?: { qaSourceDetails?: boolean }): Record<string, number | AvailabilityBadgeState> {
  const qaEscalations = useQaEscalationsBadge({ sourceDetails: options?.qaSourceDetails })
  const approvalsPending = useApprovalsPendingBadge()
  return {
    'qa-escalations': qaEscalations,
    'approvals-pending': approvalsPending,
  }
}
