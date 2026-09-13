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
 *
 * bu-d3k9e adds each subscription's contract, joined client-side by
 * `event_type` against GET /api/domain-events/contracts. `public.
 * butler_subscriptions` has no column that pins a subscription to the
 * schema_version it was created against -- `domain_event_contracts` is a
 * single-row-per-event_type projection of whatever the publisher's git
 * declaration says *right now*, not a version history -- so there is no
 * literal "the subscriber's bound version drifted from the publisher's
 * latest" signal to read. The drift marker below instead flags the one
 * misalignment that same response genuinely proves: an active subscription
 * whose butler is no longer in the contract's current `permitted_subscribers`
 * (narrowed after the subscription was created), or whose event_type no
 * longer has any declared contract at all. Both are real, actionable, and
 * derivable from this one fetch; a subscriber's own record of which
 * `schema_version` it last handled is not, without a schema change tracked
 * separately.
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
  useDomainEventContracts,
  useReplayDomainEventDelivery,
} from "@/hooks/use-domain-events"
import type { SubscriptionEntry, DeliveryEntry, ReactionSummary, ContractEntry } from "@/api/types"

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
  // Deliberately not `?? []`: the trace is an append-only ledger, and an absent
  // payload must not be flattened into a confirmed-empty one. isError is handled
  // above; what remains is a genuinely empty ledger, which says "no reaction
  // recorded" on its own terms (bu-ep4ks.5).
  const steps = reactions.data?.data
  if (!steps || steps.length === 0) {
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

/**
 * The one drift signal `GET /api/domain-events/contracts` can actually prove
 * for a subscription: it names an event_type the publisher no longer
 * declares, or names a butler the current contract no longer permits. `null`
 * means aligned (or contracts failed to load, handled separately by the
 * caller so a fetch outage never renders as a false "aligned").
 */
function contractDrift(
  entry: SubscriptionEntry,
  contract: ContractEntry | undefined,
): { label: string; tone: Tone } | null {
  if (!entry.active) return null
  if (!contract) return { label: "no contract", tone: "red" }
  if (!contract.permitted_subscribers.includes(entry.subscriber_butler)) {
    return { label: "not permitted", tone: "red" }
  }
  return null
}

function SubscriptionRow({
  entry,
  contract,
  contractsError,
}: {
  entry: SubscriptionEntry
  contract: ContractEntry | undefined
  contractsError: boolean
}) {
  const drift = contractsError ? null : contractDrift(entry, contract)
  return (
    <li
      className="py-1.5 border-b border-border/40 last:border-b-0"
      data-testid="subscription-row"
    >
      <p className="text-sm truncate" title={entry.event_type}>
        {entry.event_type}
      </p>
      <div className="flex items-center gap-1.5 mt-0.5 flex-wrap">
        <MonoLabel color={entry.active ? "dim" : "red"} className="text-[10px]">
          {entry.active ? "active" : "inactive"}
        </MonoLabel>
        <span className="font-mono text-[10px] opacity-60" aria-hidden>
          ·
        </span>
        {contractsError ? (
          <span data-testid="subscription-contract-unavailable">
            <MonoLabel color="dim" className="text-[10px] opacity-60">
              contract unavailable
            </MonoLabel>
          </span>
        ) : contract ? (
          <span data-testid="subscription-contract-version">
            <MonoLabel color="dim" className="text-[10px] opacity-60">
              contract v{contract.schema_version}
            </MonoLabel>
          </span>
        ) : null}
        {drift ? (
          <>
            <span className="font-mono text-[10px] opacity-60" aria-hidden>
              ·
            </span>
            <span data-testid="subscription-drift">
              <MonoLabel color={drift.tone} className="text-[10px]">
                {drift.label}
              </MonoLabel>
            </span>
          </>
        ) : null}
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
  const replay = useReplayDomainEventDelivery()
  const replaying = replay.isPending && replay.variables === entry.id
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
        {entry.status === "failed_permanent" ? (
          <button
            type="button"
            aria-label={`${replaying ? "Replaying" : "Replay"} ${entry.event_type} delivery`}
            disabled={replaying}
            onClick={() => replay.mutate(entry.id)}
            className="font-mono text-[10px] underline underline-offset-2 text-[var(--red-text)] hover:text-foreground disabled:opacity-50 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
          >
            {replaying ? "replaying…" : "replay"}
          </button>
        ) : null}
      </div>
      {open ? <ReactionTrace eventId={entry.event_id} traceId={traceId} /> : null}
    </li>
  )
}

function SubscriptionList({
  entries,
  isLoading,
  isError,
  contractsByEventType,
  contractsError,
}: {
  entries: SubscriptionEntry[]
  isLoading: boolean
  isError: boolean
  contractsByEventType: Map<string, ContractEntry>
  contractsError: boolean
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
        <SubscriptionRow
          key={entry.id}
          entry={entry}
          contract={contractsByEventType.get(entry.event_type)}
          contractsError={contractsError}
        />
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
  // Every publisher's contracts, not just this butler's own subscriptions:
  // a subscription's event_type may be owned by a different butler entirely.
  const contracts = useDomainEventContracts()
  const contractRows = contracts.isError ? undefined : contracts.data?.data
  const contractsByEventType = new Map<string, ContractEntry>()
  if (contractRows) {
    for (const contract of contractRows) contractsByEventType.set(contract.event_type, contract)
  }

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
            contractsByEventType={contractsByEventType}
            contractsError={contracts.isError}
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
