import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  getFinanceAccounts,
  getFinanceSpendingSummary,
  getFinanceSubscriptions,
  getFinanceTransactions,
  getFinanceUpcomingBills,
  patchFinanceBulkMetadata,
} from "@/api/index.ts";
import type {
  FinanceAccountListParams,
  FinanceBulkUpdateRequest,
  FinanceSpendingSummaryParams,
  FinanceSubscriptionListParams,
  FinanceTransactionListParams,
  FinanceUpcomingBillsParams,
} from "@/api/index.ts";

/**
 * Primary poll interval for finance butler queries (bu-ep4ks.15). No fleet-bus event
 * type covers this domain (see event-cache-registry.ts's EVENT_CACHE_REGISTRY)
 * -- this cadence IS the update path, not a reconciliation sweep.
 */
const FINANCE_POLL_MS = 60_000;

// Never-blank floor (bu-nhcp5): every read hook below sets placeholderData so
// a params change (e.g. a future date-range/filter control) keeps rendering
// the outgoing window's rows instead of flashing back to a loading state.
// Paired with <FetchingDim isFetching> at the consuming call site.

/** List transactions with optional filters. Refreshes every 60s. */
export function useFinanceTransactions(params?: FinanceTransactionListParams) {
  return useQuery({
    queryKey: ["finance", "transactions", params],
    queryFn: () => getFinanceTransactions(params),
    refetchInterval: FINANCE_POLL_MS,
    placeholderData: (previousData) => previousData,
  });
}

/** List subscriptions with optional status filter. Refreshes every 60s. */
export function useFinanceSubscriptions(params?: FinanceSubscriptionListParams) {
  return useQuery({
    queryKey: ["finance", "subscriptions", params],
    queryFn: () => getFinanceSubscriptions(params),
    refetchInterval: FINANCE_POLL_MS,
    placeholderData: (previousData) => previousData,
  });
}

/** Get upcoming bills with urgency classification. Refreshes every 60s. */
export function useFinanceUpcomingBills(params?: FinanceUpcomingBillsParams) {
  return useQuery({
    queryKey: ["finance", "upcoming-bills", params],
    queryFn: () => getFinanceUpcomingBills(params),
    refetchInterval: FINANCE_POLL_MS,
    placeholderData: (previousData) => previousData,
  });
}

/** Get spending summary (total + breakdown). Refreshes every 60s. */
export function useFinanceSpendingSummary(params?: FinanceSpendingSummaryParams) {
  return useQuery({
    queryKey: ["finance", "spending-summary", params],
    queryFn: () => getFinanceSpendingSummary(params),
    refetchInterval: FINANCE_POLL_MS,
    placeholderData: (previousData) => previousData,
  });
}

/** List financial accounts with an optional type filter. Refreshes every 60s. */
export function useFinanceAccounts(params?: FinanceAccountListParams) {
  return useQuery({
    queryKey: ["finance", "accounts", params],
    queryFn: () => getFinanceAccounts(params),
    refetchInterval: FINANCE_POLL_MS,
    placeholderData: (previousData) => previousData,
  });
}

/**
 * Bulk-update transaction metadata via the facts overlay
 * (PATCH /transactions/bulk-metadata).
 *
 * Edits write normalized_merchant / inferred_category to the bitemporal facts
 * overlay; the overlay-aware GET /transactions read (bu-v3a4x.1) merges them
 * over the base finance.transactions rows. On success we invalidate every
 * finance transactions and spending-summary query so the overlay edits surface
 * immediately on the dashboard.
 */
export function useBulkUpdateTransactionMetadata() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (request: FinanceBulkUpdateRequest) => patchFinanceBulkMetadata(request),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["finance", "transactions"] });
      queryClient.invalidateQueries({ queryKey: ["finance", "spending-summary"] });
      queryClient.invalidateQueries({ queryKey: ["finance", "distinct-merchants"] });
    },
  });
}
