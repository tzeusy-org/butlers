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
 *
 * bu-6jv4m.8 splits each delivery row in two. `status` is transport: it says
 * a wake was scheduled on the subscriber, and nothing more. The reaction
 * badge beside it is the domain outcome the subscriber reported for itself.
 * They are labelled separately because "delivered" was routinely read as
 * "handled", and a delivered wake that nobody ever closed is exactly the
 * failure this panel now has to be able to show. Each row carries a
 * keyboard-reachable trace button that expands the append-only reaction
 * ledger for that event.
 */

import { useId, useState } from "react"

import { MonoLabel, Panel } from "@/components/butler-detail/atoms"
import type { Tone } from "@/components/butler-detail/atoms-utils"
import { SourceDegradedNote } from "@/components/ui/query-boundary"
import { Time } from "@/components/ui/time"
import {
  useDomainEventSubscriptions,
  useDomainEventDeliveries,
  useDomainEventReactions,
} from "@/hooks/use-domain-events"
import type { SubscriptionEntry, DeliveryEntry, ReactionSummary } from "@/api/types"

const ROW_LIMIT = 5

function deliveryStatusTone(status: string): Tone {
  if (status === "failed" || status === "failed_permanent" || status === "conflict") return "red"
  if (status === "pending") return "amber"
  if (status === "delivered") return "green"
  return "dim"
}

function reactionTone(status: string): Tone {
  if (status === "acted") return "green"
  if (status === "failed" || status === "unreported") return "red"
  if (status === "deferred") return "amber"
  return "dim"
}

/**
 * A delivered wake with no receipt is not the same absence as a pending one:
 * the subscriber was woken and never said what it did. Say so in amber
 * rather than leaving the row looking complete.
 */
function reactionBadge(entry: DeliveryEntry): { label: string; tone: Tone } {
  const reaction: ReactionSummary | null = entry.reaction
  if (reaction) return { label: `reaction ${reaction.status}`, tone: reactionTone(reaction.status) }
  if (entry.status === "delivered") return { label: "reaction none reported", tone: "amber" }
  return { label: "reaction none yet", tone: "dim" }
}

/**
 * The append-only trace for one event, opened on demand. A plain <button>
 * rather than a hover affordance: the trace has to be reachable by keyboard,
 * and aria-expanded/aria-controls tell a screen reader what the button owns.
 */
function ReactionTrace({ eventId, traceId }: { eventId: string; traceId: string }) {
  const reactions = useDomainEventReactions(eventId, true)
  if (reactions.isLoading) {
    return (
      <div id={traceId}>
        <MonoLabel color="dim">loading</MonoLabel>
      </div>
    )
  }
  if (reactions.isError) {
    return (
      <div id={traceId}>
        <SourceDegradedNote label="Reaction trace" testId="reaction-trace-error" />
      </div>
    )
  }
  const steps = reactions.data?.data ?? []
  if (steps.length === 0) {
    return (
      <div id={traceId}>
        <MonoLabel color="dim">no reaction recorded</MonoLabel>
      </div>
    )
  }
  return (
    <ol id={traceId} data-testid="reaction-trace" className="mt-1 pl-2 border-l border-border/40">
      {steps.map((step) => (
        <li key={step.id} data-testid="reaction-trace-step" className="py-0.5">
          <MonoLabel color={reactionTone(step.status)} className="text-[10px]">
            {step.subscriber_butler} {step.status}
          </MonoLabel>{" "}
          <MonoLabel color="dim" className="text-[10px] opacity-60">
            <Time value={step.recorded_at} mode="relative-compact" />
          </MonoLabel>
          {step.note ? <p className="text-xs opacity-70">{step.note}</p> : null}
        </li>
      ))}
    </ol>
  )
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
  const [open, setOpen] = useState(false)
  const traceId = useId()
  const reaction = reactionBadge(entry)
  return (
    <li className="py-1.5 border-b border-border/40 last:border-b-0" data-testid="delivery-row">
      <p className="text-sm truncate" title={entry.event_type}>
        {entry.event_type}{" "}
        <span className="opacity-60">
          from {entry.source_butler}
        </span>
      </p>
      <div className="flex items-center gap-1.5 mt-0.5 flex-wrap">
        <span data-testid="delivery-status-badge">
          <MonoLabel color={deliveryStatusTone(entry.status)} className="text-[10px]">
            wake {entry.status}
          </MonoLabel>
        </span>
        <span className="font-mono text-[10px] opacity-60" aria-hidden>
          ·
        </span>
        <span data-testid="delivery-reaction-badge">
          <MonoLabel color={reaction.tone} className="text-[10px]">
            {reaction.label}
          </MonoLabel>
        </span>
        <span className="font-mono text-[10px] opacity-60" aria-hidden>
          ·
        </span>
        <MonoLabel color="dim" className="text-[10px] opacity-60">
          <Time value={entry.occurred_at} mode="relative-compact" />
        </MonoLabel>
        <button
          type="button"
          data-testid="delivery-trace-toggle"
          aria-expanded={open}
          aria-controls={traceId}
          onClick={() => setOpen((wasOpen) => !wasOpen)}
          className="font-mono text-[10px] underline underline-offset-2 text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
        >
          {open ? "hide trace" : "trace"}
        </button>
      </div>
      {open ? <ReactionTrace eventId={entry.event_id} traceId={traceId} /> : null}
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
          <p className="text-[10px] text-muted-foreground mb-1" data-testid="deliveries-legend">
            wake = the subscriber was woken · reaction = what it reported doing
          </p>
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
