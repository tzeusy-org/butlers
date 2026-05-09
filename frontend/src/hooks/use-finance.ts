import { useQuery } from "@tanstack/react-query";

import {
  getFinanceBills,
  getFinanceSpendingSummary,
  getFinanceSubscriptions,
  getFinanceTransactions,
  getFinanceUpcomingBills,
} from "@/api/index.ts";
import type {
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
export function useFinanceBills(params?: {
  status?: string;
  payee?: string;
  offset?: number;
  limit?: number;
}) {
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
