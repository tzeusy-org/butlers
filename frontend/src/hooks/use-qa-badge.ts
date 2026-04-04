/**
 * Provides live badge counts for QA nav items.
 *
 * Returns a map from badgeKey → count so the Sidebar can render
 * badge indicators without needing to know about QA specifics.
 *
 * The QA badge query is only fired when the QA butler is present in the
 * roster (i.e. the nav item will actually be visible), to avoid spurious
 * requests on instances that have no QA staffer deployed.
 */

import { useQaKnownIssues } from './use-qa'
import { useButlers } from './use-butlers'

/** Returns the count of active (non-dismissed) QA known issues for the sidebar badge. */
export function useQaActiveBadge(): number {
  const { data: butlersResponse } = useButlers()
  const hasQa = butlersResponse?.data.some((b) => b.name === 'qa') ?? false
  const { data } = useQaKnownIssues({ dismissed: false, limit: 1 }, { enabled: hasQa })
  return data?.meta.total ?? 0
}

/** Badge registry — maps badgeKey to a hook that returns a count (or 0). */
export function useBadgeCounts(): Record<string, number> {
  const qaActive = useQaActiveBadge()
  return {
    'qa-known-issues': qaActive,
  }
}
