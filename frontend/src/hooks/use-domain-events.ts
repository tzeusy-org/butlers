/**
 * TanStack Query hooks for GET /api/domain-events/{subscriptions,deliveries,
 * events/:id/reactions} (bu-317s5, bu-6jv4m.8) -- butler detail's
 * domain-event-bus visibility panel.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { listDomainEventSubscriptions, listDomainEventDeliveries, listDomainEventReactions, listDomainEventContracts, replayDomainEventDelivery } from "@/api/client.ts";
import type { DomainEventSubscriptionsParams, DomainEventDeliveriesParams, DomainEventContractsParams } from "@/api/client.ts";

// Subscriptions and contracts are roster/migration configuration: they change on deploy, not
// at runtime, so they load once and refresh on remount or focus (no interval).
// Deliveries are an operational feed with no fleet event type, so they poll locally.
const DOMAIN_EVENT_DELIVERIES_POLL_MS = 30_000;

export function useDomainEventSubscriptions(params: DomainEventSubscriptionsParams = {}) {
  return useQuery({
    queryKey: ["domain-event-subscriptions", params],
    queryFn: () => listDomainEventSubscriptions(params),
  });
}

/**
 * Every materialized publisher contract, not just this butler's own: a
 * subscription's bound version has to be compared against its publisher's
 * current contract, which may belong to a different butler entirely.
 */
export function useDomainEventContracts(params: DomainEventContractsParams = {}) {
  return useQuery({
    queryKey: ["domain-event-contracts", params],
    queryFn: () => listDomainEventContracts(params),
  });
}

export function useDomainEventDeliveries(params: DomainEventDeliveriesParams = {}) {
  return useQuery({
    queryKey: ["domain-event-deliveries", params],
    queryFn: () => listDomainEventDeliveries(params),
    refetchInterval: DOMAIN_EVENT_DELIVERIES_POLL_MS,
  });
}

export function useReplayDomainEventDelivery() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: replayDomainEventDelivery,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["domain-event-deliveries"] });
      toast.success("Delivery queued for replay");
    },
    onError: (error: Error) => toast.error(error.message || "Could not replay delivery"),
  });
}

/**
 * The reaction trace for one event. Only fetched once the reader opens the
 * trace: a collapsed row costs nothing, and the trace is a deliberate
 * "what actually happened" question rather than something to poll behind
 * everyone's back.
 */
export function useDomainEventReactions(eventId: string, enabled: boolean) {
  return useQuery({
    queryKey: ["domain-event-reactions", eventId],
    queryFn: () => listDomainEventReactions(eventId),
    enabled,
  });
}
