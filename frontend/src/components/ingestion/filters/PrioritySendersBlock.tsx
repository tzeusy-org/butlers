/**
 * PrioritySendersBlock — first-class priority senders data surface.
 *
 * Backed by public.priority_contacts — the table the runtime actually reads
 * (connectors/gmail_policy.py joins public.priority_contacts → public.contacts
 * → relationship.entity_facts to resolve priority sender emails). This block
 * therefore reads GET /api/ingestion/priority-contacts and writes via
 * POST/DELETE on the same router, NOT the IngestionRule DSL proxy that
 * previously backed it (the proxy was misleading: the runtime never read it).
 *
 * The section header shows the count; each row shows contact name, channel
 * identifiers, and added timestamp. Priority contacts are global
 * (butler-agnostic) — the runtime priority set is shared, not per-butler.
 *
 * The "+ add" affordance opens an inline contact picker (backed by the
 * relationship contacts list); selecting a contact POSTs a new priority
 * contact. Removal DELETEs it.
 *
 * Mutations (add/remove) surface errors visibly via inline error state.
 * Errors are never silently swallowed.
 *
 * Spec: openspec/changes/redesign-ingestion-dispatch-console/specs/
 *       ingestion-priority-contacts/
 */

import { useState } from 'react'

import type { ContactSummary, PriorityContactEntry } from '@/api/types'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: '2-digit' })
  } catch {
    return iso.slice(0, 10)
  }
}

/** Human-readable channel handle line for a priority contact. */
function handleFromEntry(entry: PriorityContactEntry): string {
  if (entry.contact_info_values.length > 0) {
    return entry.contact_info_values.join(' · ')
  }
  return entry.contact_id
}

// ---------------------------------------------------------------------------
// PrioritySendersBlock
// ---------------------------------------------------------------------------

export interface PrioritySendersBlockProps {
  /** Priority-contact assignments from public.priority_contacts. */
  contacts: PriorityContactEntry[]
  /** True when the data fetch has completed (regardless of result). */
  loaded: boolean
  /** Whether the fetch encountered an error. */
  error: boolean
  /** Mutation error from add/remove action. */
  mutationError?: string | null
  /**
   * Candidate contacts for the add picker. Already-assigned contacts may be
   * included; the picker surfaces them all and the backend rejects duplicates.
   */
  addCandidates?: ContactSummary[]
  /** Whether the add-candidate list is still loading. */
  candidatesLoading?: boolean
  /** Add a priority contact by contact id. */
  onAdd?: (contactId: string) => void
  /** Remove a priority contact by contact id. */
  onRemove?: (contactId: string) => void
}

export function PrioritySendersBlock({
  contacts,
  loaded,
  error,
  mutationError,
  addCandidates = [],
  candidatesLoading = false,
  onAdd,
  onRemove,
}: PrioritySendersBlockProps) {
  const [picking, setPicking] = useState(false)

  function handleSelectCandidate(contactId: string) {
    if (contactId) onAdd?.(contactId)
    setPicking(false)
  }

  return (
    <div data-testid="priority-senders-block">
      {/* Section header */}
      <div className="flex items-baseline gap-3 py-3 border-b border-border">
        <span className="font-mono text-[10px] tracking-[0.14em] uppercase text-muted-foreground">
          priority · senders
        </span>
        <span className="font-mono text-[10px] text-muted-foreground/60">
          {loaded ? `${contacts.length} contacts · high-priority tag` : '…'}
        </span>
        <span className="ml-auto" />
        <button
          type="button"
          className="font-mono text-[10px] border border-foreground/30 px-2.5 py-1 hover:bg-foreground/5 transition-colors"
          onClick={() => setPicking((v) => !v)}
          aria-expanded={picking}
          data-testid="priority-senders-add"
        >
          {picking ? 'cancel' : '+ add'}
        </button>
      </div>

      {/* Add picker */}
      {picking && (
        <div
          className="mt-3 flex items-center gap-2"
          data-testid="priority-senders-add-picker"
        >
          <label
            className="font-mono text-[10px] tracking-[0.12em] uppercase text-muted-foreground"
            htmlFor="priority-sender-contact-select"
          >
            contact
          </label>
          <select
            id="priority-sender-contact-select"
            className="font-mono text-[11px] bg-transparent border border-foreground/30 px-2 py-1 flex-1 min-w-0"
            defaultValue=""
            disabled={candidatesLoading}
            onChange={(e) => handleSelectCandidate(e.target.value)}
            data-testid="priority-senders-contact-select"
          >
            <option value="" disabled>
              {candidatesLoading ? 'loading contacts…' : 'select a contact…'}
            </option>
            {addCandidates.map((c) => (
              <option key={c.id} value={c.id}>
                {c.full_name}
                {c.email ? ` · ${c.email}` : ''}
              </option>
            ))}
          </select>
        </div>
      )}

      {/* Gloss */}
      <p className="font-serif text-sm text-muted-foreground leading-[1.5] mt-3.5 max-w-[60ch]">
        Mail from these contacts is tagged with a <code className="font-mono text-[11px]">high_priority</code> policy
        tier as it enters the ingestion pipeline. That tag surfaces in
        observability (metrics and trace spans) so you can see priority-sender
        activity. It does not currently bypass label or global filter rules, and
        dispatch timing is the same as for other mail.
      </p>

      {/* Mutation error */}
      {mutationError && (
        <div
          className="mt-3 font-mono text-[11px] text-[color:var(--filter-red,oklch(0.62_0.20_25))] border border-[color:var(--filter-red,oklch(0.62_0.20_25))]/30 px-3 py-2"
          data-testid="priority-senders-mutation-error"
        >
          {mutationError}
        </div>
      )}

      {/* Loading skeleton */}
      {!loaded && (
        <div className="mt-4 space-y-2">
          {[1, 2].map((i) => (
            <div key={i} className="h-10 bg-foreground/5 animate-pulse" />
          ))}
        </div>
      )}

      {/* Error state */}
      {loaded && error && (
        <p
          className="font-serif italic text-sm text-muted-foreground py-5"
          data-testid="priority-senders-error"
        >
          Priority senders unavailable. Check connectivity and reload.
        </p>
      )}

      {/* Empty state */}
      {loaded && !error && contacts.length === 0 && (
        <p
          className="font-serif italic text-sm text-muted-foreground py-5"
          data-testid="priority-senders-empty"
        >
          No priority senders configured.
        </p>
      )}

      {/* Table */}
      {loaded && !error && contacts.length > 0 && (
        <div className="mt-4">
          {/* Column headers */}
          <div
            className="grid gap-3.5 py-2.5 border-b border-border/50 font-mono text-[9.5px] tracking-[0.14em] uppercase text-muted-foreground/70"
            style={{ gridTemplateColumns: '1.4fr 90px 24px' }}
          >
            <span>name · handle</span>
            <span>added</span>
            <span />
          </div>

          {contacts.map((entry) => (
            <div
              key={entry.contact_id}
              className="py-3 border-b border-border/50"
              data-testid={`priority-sender-row-${entry.contact_id}`}
            >
              <div
                className="grid gap-3.5 items-baseline"
                style={{ gridTemplateColumns: '1.4fr 90px 24px' }}
              >
                {/* Name / handle */}
                <div className="min-w-0">
                  <div className="text-[13.5px] font-medium tracking-[-0.005em] truncate">
                    {entry.name ?? handleFromEntry(entry)}
                  </div>
                  <span className="block font-mono text-[10px] text-muted-foreground mt-0.5 truncate">
                    {handleFromEntry(entry)}
                  </span>
                </div>

                {/* Added */}
                <span className="font-mono text-[10px] text-muted-foreground">
                  {formatDate(entry.added_at)}
                </span>

                {/* Remove */}
                <button
                  type="button"
                  className="font-mono text-[12px] text-muted-foreground hover:text-[color:var(--filter-red,oklch(0.62_0.20_25))]"
                  onClick={() => onRemove?.(entry.contact_id)}
                  aria-label={`Remove priority sender ${entry.name ?? entry.contact_id}`}
                  data-testid={`priority-sender-remove-${entry.contact_id}`}
                >
                  ×
                </button>
              </div>

              {/* Inert warning badge — shown only when this entry would match nothing at runtime */}
              {entry.is_inert && (
                <div
                  className="mt-1.5 font-mono text-[9.5px] tracking-[0.06em] text-[color:var(--filter-amber,oklch(0.72_0.15_85))] border border-[color:var(--filter-amber,oklch(0.72_0.15_85))]/40 px-2 py-0.5 inline-flex items-center gap-1.5"
                  data-testid={`priority-sender-inert-${entry.contact_id}`}
                  title="This contact has no email address in the system. The Gmail policy evaluator resolves priority senders via a linked entity with a has-email fact. Without one, this entry matches nothing."
                >
                  <span aria-hidden="true">⚠</span>
                  no email fact: entry matches nothing
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
