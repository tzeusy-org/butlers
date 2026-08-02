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
import { useDecisions } from './use-decisions'

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

/** A sidebar badge with an explicit unavailable state. */
export type AvailabilityBadgeState =
  | { kind: 'count'; count: number }
  | { kind: 'unavailable' }

/** Returns the pending-approval badge state without inventing a zero on partial metrics. */
export function useApprovalsPendingBadge(): AvailabilityBadgeState {
  const { data, isError } = useApprovalMetrics()
  if (isError || pendingApprovalMetricSourcesDegraded(data).length > 0) {
    return { kind: 'unavailable' }
  }
  return { kind: 'count', count: data?.data.total_pending ?? 0 }
}

/**
 * Decisions uses the same explicit availability shape as approvals so a
 * readable empty digest remains distinct from an unavailable one.
 */
export type DecisionsBadgeState = AvailabilityBadgeState

export function useDecisionsOpenBadge(): DecisionsBadgeState {
  const { data, isError } = useDecisions()
  if (isError || data?.meta?.decisions_available === false) return { kind: 'unavailable' }
  return { kind: 'count', count: data?.data.length ?? 0 }
}

/** Badge registry — approval and decision badges carry explicit availability. */
export function useBadgeCounts(): Record<string, number | AvailabilityBadgeState> {
  const qaEscalations = useQaEscalationsBadge()
  const approvalsPending = useApprovalsPendingBadge()
  const decisionsOpen = useDecisionsOpenBadge()
  return {
    'qa-escalations': qaEscalations,
    'approvals-pending': approvalsPending,
    'decisions-open': decisionsOpen,
  }
}
