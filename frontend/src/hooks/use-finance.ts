import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  getFinanceAccounts,
  getFinanceBills,
  getFinanceDistinctMerchants,
  getFinanceSpendingSummary,
  getFinanceSubscriptions,
  getFinanceTransactions,
  getFinanceUpcomingBills,
  patchFinanceBulkMetadata,
} from "@/api/index.ts";
import type {
  FinanceAccountListParams,
  FinanceBillListParams,
  FinanceBulkUpdateRequest,
  FinanceDistinctMerchantsParams,
  FinanceSpendingSummaryParams,
  FinanceSubscriptionListParams,
  FinanceTransactionListParams,
  FinanceUpcomingBillsParams,
} from "@/api/index.ts";

/** List transactions with optional filters. Refreshes every 60s. */
export function useFinanceTransactions(params?: FinanceTransactionListParams) {
  return useQuery({
    queryKey: ["finance", "transactions", params],
    queryFn: () => getFinanceTransactions(params),
    refetchInterval: 60_000,
  });
}

/** List subscriptions with optional status filter. Refreshes every 60s. */
export function useFinanceSubscriptions(params?: FinanceSubscriptionListParams) {
  return useQuery({
    queryKey: ["finance", "subscriptions", params],
    queryFn: () => getFinanceSubscriptions(params),
    refetchInterval: 60_000,
  });
}

/** List bills with optional filters. Refreshes every 60s. */
export function useFinanceBills(params?: FinanceBillListParams) {
  return useQuery({
    queryKey: ["finance", "bills", params],
    queryFn: () => getFinanceBills(params),
    refetchInterval: 60_000,
  });
}

/** Get upcoming bills with urgency classification. Refreshes every 60s. */
export function useFinanceUpcomingBills(params?: FinanceUpcomingBillsParams) {
  return useQuery({
    queryKey: ["finance", "upcoming-bills", params],
    queryFn: () => getFinanceUpcomingBills(params),
    refetchInterval: 60_000,
  });
}

/** Get spending summary (total + breakdown). Refreshes every 60s. */
export function useFinanceSpendingSummary(params?: FinanceSpendingSummaryParams) {
  return useQuery({
    queryKey: ["finance", "spending-summary", params],
    queryFn: () => getFinanceSpendingSummary(params),
    refetchInterval: 60_000,
  });
}

/** List financial accounts with an optional type filter. Refreshes every 60s. */
export function useFinanceAccounts(params?: FinanceAccountListParams) {
  return useQuery({
    queryKey: ["finance", "accounts", params],
    queryFn: () => getFinanceAccounts(params),
    refetchInterval: 60_000,
  });
}

/** List distinct raw merchants with aggregate stats. Used to seed normalize affordances. */
export function useFinanceDistinctMerchants(params?: FinanceDistinctMerchantsParams) {
  return useQuery({
    queryKey: ["finance", "distinct-merchants", params],
    queryFn: () => getFinanceDistinctMerchants(params),
    refetchInterval: 60_000,
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
