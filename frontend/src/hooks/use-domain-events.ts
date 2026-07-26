/**
 * TanStack Query hooks for GET /api/domain-events/{subscriptions,deliveries}
 * (bu-317s5) -- butler detail's domain-event-bus visibility panel.
 */

import { useQuery } from "@tanstack/react-query";

import {
  listDomainEventSubscriptions,
  listDomainEventDeliveries,
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
