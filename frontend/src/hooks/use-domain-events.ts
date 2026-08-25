/**
 * TanStack Query hooks for GET /api/domain-events/{subscriptions,deliveries,
 * events/:id/reactions} (bu-317s5, bu-6jv4m.8) -- butler detail's
 * domain-event-bus visibility panel.
 */

import { useQuery } from "@tanstack/react-query";

import {
  listDomainEventSubscriptions,
  listDomainEventDeliveries,
  listDomainEventReactions,
  type DomainEventSubscriptionsParams,
  type DomainEventDeliveriesParams,
} from "@/api/index.ts";
import { useBusAwarePollInterval } from "@/hooks/use-bus-aware-poll-interval";

export function useDomainEventSubscriptions(params: DomainEventSubscriptionsParams = {}) {
  const refetchInterval = useBusAwarePollInterval();
  return useQuery({
    queryKey: ["domain-event-subscriptions", params],
    queryFn: () => listDomainEventSubscriptions(params),
    refetchInterval,
  });
}

export function useDomainEventDeliveries(params: DomainEventDeliveriesParams = {}) {
  const refetchInterval = useBusAwarePollInterval();
  return useQuery({
    queryKey: ["domain-event-deliveries", params],
    queryFn: () => listDomainEventDeliveries(params),
    refetchInterval,
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
