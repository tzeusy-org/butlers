/**
 * ButlerDelegationsPanel -- cross-butler delegation ledger for one butler
 * (bu-ep4ks.3).
 *
 * public.delegation_ledger (bu-gxmfx) had zero frontend wiring until this
 * panel: a delegated question this butler asked, or one it was routed to
 * answer, was only visible via psql. Two independently-fetched lists --
 * "Delegated out" (asking_butler=this butler) and "Delegated in"
 * (target_butler=this butler) -- since the API filters by exactly one of
 * asking_butler/target_butler at a time, not an OR of both.
 *
 * wake_state (migration core_181) renders as a visually distinct badge for
 * the two failure states the wake protocol introduces --
 * "callback_failed" and "task_conflict" -- which previously rendered
 * identically to an ordinary answered row.
 */

import { MonoLabel, Panel } from "@/components/butler-detail/atoms"
import { SourceDegradedNote } from "@/components/ui/query-boundary"
import { Time } from "@/components/ui/time"
import { useDelegationLedger } from "@/hooks/use-delegation"
import type { DelegationLedgerEntry } from "@/api/types"

const ROW_LIMIT = 5

function wakeStateTone(wakeState: string): "red" | "amber" | "dim" {
  if (wakeState === "callback_failed" || wakeState === "task_conflict") return "red"
  if (wakeState === "callback_pending" || wakeState === "callback_routed") return "amber"
  return "dim"
}

function DelegationRow({ entry }: { entry: DelegationLedgerEntry }) {
  const showWakeBadge = entry.wake_state !== "not_applicable"
  return (
    <li className="py-1.5 border-b border-border/40 last:border-b-0" data-testid="delegation-row">
      <p className="text-sm truncate" title={entry.question}>
        {entry.question}
      </p>
      <div className="flex items-center gap-1.5 mt-0.5">
        <MonoLabel className="text-[10px]">{entry.status}</MonoLabel>
        {showWakeBadge ? (
          <>
            <span className="font-mono text-[10px] opacity-60" aria-hidden>
              ·
            </span>
            <span data-testid="delegation-wake-badge">
              <MonoLabel color={wakeStateTone(entry.wake_state)} className="text-[10px]">
                {entry.wake_state.replace(/_/g, " ")}
              </MonoLabel>
            </span>
          </>
        ) : null}
        <span className="font-mono text-[10px] opacity-60" aria-hidden>
          ·
        </span>
        <MonoLabel color="dim" className="text-[10px] opacity-60">
          <Time value={entry.asked_at} mode="relative-compact" />
        </MonoLabel>
      </div>
    </li>
  )
}

function DelegationList({
  entries,
  isLoading,
  isError,
  emptyText,
  testId,
}: {
  entries: DelegationLedgerEntry[]
  isLoading: boolean
  isError: boolean
  emptyText: string
  testId: string
}) {
  if (isLoading) {
    return <MonoLabel color="dim">loading</MonoLabel>
  }
  if (isError) {
    return <SourceDegradedNote label="Delegations" testId={`${testId}-error`} />
  }
  if (entries.length === 0) {
    return <MonoLabel color="dim">{emptyText}</MonoLabel>
  }
  return (
    <ul data-testid={testId}>
      {entries.map((entry) => (
        <DelegationRow key={entry.id} entry={entry} />
      ))}
    </ul>
  )
}

export interface ButlerDelegationsPanelProps {
  butlerName: string
}

export function ButlerDelegationsPanel({ butlerName }: ButlerDelegationsPanelProps) {
  const outgoing = useDelegationLedger({ asking_butler: butlerName, limit: ROW_LIMIT })
  const incoming = useDelegationLedger({ target_butler: butlerName, limit: ROW_LIMIT })

  return (
    <Panel title="delegations" span={4} className="sm:col-span-2" testId="panel-delegations">
      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <MonoLabel color="dim" className="mb-1 block">
            delegated out
          </MonoLabel>
          <DelegationList
            entries={outgoing.data?.data ?? []}
            isLoading={outgoing.isLoading}
            isError={outgoing.isError}
            emptyText="no delegated questions asked"
            testId="delegations-outgoing"
          />
        </div>
        <div>
          <MonoLabel color="dim" className="mb-1 block">
            delegated in
          </MonoLabel>
          <DelegationList
            entries={incoming.data?.data ?? []}
            isLoading={incoming.isLoading}
            isError={incoming.isError}
            emptyText="no delegated questions routed here"
            testId="delegations-incoming"
          />
        </div>
      </div>
    </Panel>
  )
}
