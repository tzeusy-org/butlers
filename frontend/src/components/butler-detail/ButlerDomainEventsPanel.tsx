/**
 * ButlerDomainEventsPanel -- domain-event bus subscription visibility for one
 * butler (bu-317s5, domain-event bus slice 2).
 *
 * public.butler_subscriptions/public.domain_event_deliveries had zero
 * frontend wiring until this panel: a butler's standing subscriptions and
 * its recent fan-out deliveries were only visible via psql or the MCP
 * `list_my_subscriptions` tool from inside the butler's own session. Two
 * independently-fetched lists -- "subscriptions" (this butler's own,
 * active and inactive) and "recent deliveries" (fan-out events routed to
 * this butler) -- mirroring ButlerDelegationsPanel's outgoing/incoming
 * split so a failed query renders a distinct degraded note rather than a
 * fabricated empty list (degraded-source honesty doctrine).
 */

import { MonoLabel, Panel } from "@/components/butler-detail/atoms"
import { SourceDegradedNote } from "@/components/ui/query-boundary"
import { Time } from "@/components/ui/time"
import { useDomainEventSubscriptions, useDomainEventDeliveries } from "@/hooks/use-domain-events"
import type { SubscriptionEntry, DeliveryEntry } from "@/api/types"

const ROW_LIMIT = 5

function deliveryStatusTone(status: string): "red" | "amber" | "dim" | "green" {
  if (status === "failed" || status === "conflict") return "red"
  if (status === "pending") return "amber"
  if (status === "delivered") return "green"
  return "dim"
}

function SubscriptionRow({ entry }: { entry: SubscriptionEntry }) {
  return (
    <li
      className="py-1.5 border-b border-border/40 last:border-b-0"
      data-testid="subscription-row"
    >
      <p className="text-sm truncate" title={entry.event_type}>
        {entry.event_type}
      </p>
      <div className="flex items-center gap-1.5 mt-0.5">
        <MonoLabel color={entry.active ? "dim" : "red"} className="text-[10px]">
          {entry.active ? "active" : "inactive"}
        </MonoLabel>
        <span className="font-mono text-[10px] opacity-60" aria-hidden>
          ·
        </span>
        <MonoLabel color="dim" className="text-[10px] opacity-60">
          <Time value={entry.updated_at} mode="relative-compact" />
        </MonoLabel>
      </div>
    </li>
  )
}

function DeliveryRow({ entry }: { entry: DeliveryEntry }) {
  return (
    <li className="py-1.5 border-b border-border/40 last:border-b-0" data-testid="delivery-row">
      <p className="text-sm truncate" title={entry.event_type}>
        {entry.event_type}{" "}
        <span className="opacity-60">
          from {entry.source_butler}
        </span>
      </p>
      <div className="flex items-center gap-1.5 mt-0.5">
        <span data-testid="delivery-status-badge">
          <MonoLabel color={deliveryStatusTone(entry.status)} className="text-[10px]">
            {entry.status}
          </MonoLabel>
        </span>
        <span className="font-mono text-[10px] opacity-60" aria-hidden>
          ·
        </span>
        <MonoLabel color="dim" className="text-[10px] opacity-60">
          <Time value={entry.occurred_at} mode="relative-compact" />
        </MonoLabel>
      </div>
    </li>
  )
}

function SubscriptionList({
  entries,
  isLoading,
  isError,
}: {
  entries: SubscriptionEntry[]
  isLoading: boolean
  isError: boolean
}) {
  if (isLoading) {
    return <MonoLabel color="dim">loading</MonoLabel>
  }
  if (isError) {
    return <SourceDegradedNote label="Subscriptions" testId="subscriptions-error" />
  }
  if (entries.length === 0) {
    return <MonoLabel color="dim">no standing subscriptions</MonoLabel>
  }
  return (
    <ul data-testid="subscriptions-list">
      {entries.map((entry) => (
        <SubscriptionRow key={entry.id} entry={entry} />
      ))}
    </ul>
  )
}

function DeliveryList({
  entries,
  isLoading,
  isError,
}: {
  entries: DeliveryEntry[]
  isLoading: boolean
  isError: boolean
}) {
  if (isLoading) {
    return <MonoLabel color="dim">loading</MonoLabel>
  }
  if (isError) {
    return <SourceDegradedNote label="Deliveries" testId="deliveries-error" />
  }
  if (entries.length === 0) {
    return <MonoLabel color="dim">no recent deliveries</MonoLabel>
  }
  return (
    <ul data-testid="deliveries-list">
      {entries.map((entry) => (
        <DeliveryRow key={entry.id} entry={entry} />
      ))}
    </ul>
  )
}

export interface ButlerDomainEventsPanelProps {
  butlerName: string
}

export function ButlerDomainEventsPanel({ butlerName }: ButlerDomainEventsPanelProps) {
  const subscriptions = useDomainEventSubscriptions({ subscriber_butler: butlerName })
  const deliveries = useDomainEventDeliveries({
    subscriber_butler: butlerName,
    limit: ROW_LIMIT,
  })

  return (
    <Panel title="domain events" span={4} className="sm:col-span-2" testId="panel-domain-events">
      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <MonoLabel color="dim" className="mb-1 block">
            subscriptions
          </MonoLabel>
          <SubscriptionList
            entries={subscriptions.data?.data ?? []}
            isLoading={subscriptions.isLoading}
            isError={subscriptions.isError}
          />
        </div>
        <div>
          <MonoLabel color="dim" className="mb-1 block">
            recent deliveries
          </MonoLabel>
          <DeliveryList
            entries={deliveries.data?.data ?? []}
            isLoading={deliveries.isLoading}
            isError={deliveries.isError}
          />
        </div>
      </div>
    </Panel>
  )
}
