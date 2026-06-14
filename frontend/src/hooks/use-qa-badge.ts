/**
 * Provides live badge counts for nav items.
 *
 * Returns a map from badgeKey → count so the Sidebar can render
 * badge indicators without needing to know about domain specifics.
 *
 * The QA badge query is only fired when the QA butler is present in the
 * roster (i.e. the nav item will actually be visible), to avoid spurious
 * requests on instances that have no QA staffer deployed.
 */

import { useQaSummary } from './use-qa'
import { useButlers } from './use-butlers'
import { useApprovalMetrics } from './use-approvals'

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
export function useQaEscalationsBadge(): number {
  const { data: butlersResponse } = useButlers()
  const hasQa = butlersResponse?.data.some((b) => b.name === 'qa') ?? false
  const { data } = useQaSummary({ enabled: hasQa })
  return data?.data.active_breakdown.escalated_open_cases ?? 0
}

/** Returns the count of pending approval actions for the sidebar badge. */
export function useApprovalsPendingBadge(): number {
  const { data } = useApprovalMetrics()
  return data?.data.total_pending ?? 0
}

/** Badge registry — maps badgeKey to a hook that returns a count (or 0). */
export function useBadgeCounts(): Record<string, number> {
  const qaEscalations = useQaEscalationsBadge()
  const approvalsPending = useApprovalsPendingBadge()
  return {
    'qa-escalations': qaEscalations,
    'approvals-pending': approvalsPending,
  }
}
