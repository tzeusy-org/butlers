/**
 * FiltersPipeline — full /ingestion/filters pipeline surface.
 *
 * Sections (in order):
 * 1. Header with event count + range (via DispatchHeader's aside)
 * 2. Five-gate diagram (PipelineGateDiagram)
 * 3. Five gate sections (GateSection × 5)
 * 4. Priority senders block
 * 5. Channel defaults block
 * 6. Archived/disabled rules (collapsible)
 * 7. Footer: last-modified info + add-rule + open-DSL CTAs
 *
 * Data sources:
 * - usePipelineStats("24h")        → PipelineStats (funnel counts)
 * - useIngestionRules({enabled:true})  → active rules
 * - useIngestionRules({archived:true}) → archived (soft-deleted) rules via ?archived=true
 *
 * Priority senders: rules with action starting with "route" and
 * rule_type="priority_sender" (or scope="priority").
 *
 * Channel defaults: the row list reads rules with scope matching a channel
 * name and rule_type="channel_default"; the inline "edit" affordance reads
 * and writes the actual runtime policy at GET/PATCH
 * /api/ingestion/channel-defaults/:channel (public.channel_defaults) via
 * useChannelDefault/useUpdateChannelDefault — a distinct store from the rule
 * rows above it.
 *
 * Mutation errors are always visible (inline). This component does not
 * optimistically hide previous state on failure.
 *
 * Spec: openspec/changes/complete-ingestion-redesign-parity/specs/
 *       dashboard-ingestion-dispatch-console/spec.md §"Filters Pipeline"
 */

import { useState } from 'react'
import { usePipelineStats } from '@/hooks/use-ingestion'
import { useIngestionRules, useUpdateIngestionRule, useDeleteIngestionRule } from '@/hooks/use-ingestion-rules'
import {
  usePriorityContacts,
  useAddPriorityContact,
  useRemovePriorityContact,
} from '@/hooks/use-priority-contacts'
import { useContacts } from '@/hooks/use-contacts'
import { useChannelDefault, useUpdateChannelDefault } from '@/hooks/use-channel-defaults'
import { GATE_DEFS, groupRulesByGate, deriveGateCounts } from './gate-state'
import type { GateKey } from './gate-state'
import { PipelineGateDiagram } from './PipelineGateDiagram'
import { GateSection } from './GateSection'
import { PrioritySendersBlock } from './PrioritySendersBlock'
import { ChannelDefaultsBlock, type ChannelDefaultEditorState } from './ChannelDefaultsBlock'
import { ArchivedRulesSection } from './ArchivedRulesSection'
import { RuleEditor, type EditorMode } from './RuleEditor'
import type { IngestionRule, PipelineStats } from '@/api/types'
import type { ChannelDefaultPolicy } from '@/api/index.ts'
import { ApiError } from '@/api/index.ts'
import { getAvailablePipelineBacklog } from './backlog-state'
import { SourceDegradedNote } from '@/components/ui/query-boundary'

// ---------------------------------------------------------------------------
// Rule classification helpers
// ---------------------------------------------------------------------------

function isChannelDefault(rule: IngestionRule): boolean {
  return (
    rule.rule_type === 'channel_default' ||
    rule.scope === 'channel_default'
  )
}

interface ExecutionBacklogProps {
  stats: PipelineStats | undefined
  loading: boolean
  statsError: boolean
  onRetry: () => void
}

function ExecutionBacklog({ stats, loading, statsError, onRetry }: ExecutionBacklogProps) {
  if (loading) return null

  if (statsError) {
    return (
      <SourceDegradedNote
        className="mt-6"
        label="execution backlog"
        detail="unavailable after refresh failed"
        onRetry={onRetry}
        testId="pipeline-execution-backlog-unavailable"
      />
    )
  }

  const backlog = getAvailablePipelineBacklog(stats)
  if (!backlog) {
    return (
      <div
        className="mt-6 border border-border px-4 py-3 font-mono text-[10px] tracking-[0.08em] uppercase text-muted-foreground/70"
        data-testid="pipeline-execution-backlog-unavailable"
        role="status"
        aria-live="polite"
      >
        backlog unavailable · execution counts could not be checked
      </div>
    )
  }

  return (
    <section
      className="mt-6 border border-border px-4 py-4"
      data-testid="pipeline-execution-backlog"
      aria-labelledby="pipeline-execution-backlog-heading"
      aria-live="polite"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-2">
        <div>
          <p
            id="pipeline-execution-backlog-heading"
            className="font-mono text-[9.5px] tracking-[0.14em] uppercase text-muted-foreground/70"
          >
            execution backlog · current ledger
          </p>
          <p className="mt-1 font-serif text-sm text-muted-foreground">
            Work awaiting resolution after the execute gate.
          </p>
        </div>
        <p className="font-mono text-lg font-medium tabular-nums tracking-[-0.02em]">
          {backlog.activeTotal.toLocaleString()} active
        </p>
      </div>

      <dl className="mt-4 grid gap-4 sm:grid-cols-3">
        <div>
          <dt className="font-mono text-[9.5px] tracking-[0.12em] uppercase text-[var(--red-text)]">
            unresolved failures
          </dt>
          <dd className="mt-1 font-mono text-xl font-medium tabular-nums text-[var(--red-text)]">
            {backlog.failedTotal.toLocaleString()}
          </dd>
        </div>
        <div>
          <dt className="font-mono text-[9.5px] tracking-[0.12em] uppercase text-[var(--amber-text)]">
            replay pending
          </dt>
          <dd className="mt-1 font-mono text-xl font-medium tabular-nums text-[var(--amber-text)]">
            {backlog.replayPendingTotal.toLocaleString()}
          </dd>
        </div>
        <div>
          <dt className="font-mono text-[9.5px] tracking-[0.12em] uppercase text-muted-foreground/70">
            reviewed write-offs
          </dt>
          <dd className="mt-1 font-mono text-xl font-medium tabular-nums text-muted-foreground">
            {backlog.writtenOffTotal.toLocaleString()}
          </dd>
        </div>
      </dl>

      <p className="mt-4 max-w-[72ch] font-serif text-[13.5px] leading-[1.55] text-muted-foreground">
        Unresolved failures and requested replays awaiting reconciliation are active backlog. Reviewed
        write-offs remain visible for audit, but are not active backlog.
      </p>
    </section>
  )
}

// ---------------------------------------------------------------------------
// FiltersPipeline
// ---------------------------------------------------------------------------

export function FiltersPipeline() {
  const [toggleError, setToggleError] = useState<string | null>(null)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [priorityMutationError, setPriorityMutationError] = useState<string | null>(null)
  const [channelMutationError, setChannelMutationError] = useState<string | null>(null)
  const [restoreError, setRestoreError] = useState<string | null>(null)

  // Channel defaults inline editor (public.channel_defaults, distinct from
  // the ingestion_rules-backed row list) — null means no channel is being edited.
  const [editingChannel, setEditingChannel] = useState<string | null>(null)

  // Rule editor (create / edit / DSL) — wires the footer + per-rule edit
  // affordances to a real create/update flow.
  const [editorState, setEditorState] = useState<{
    mode: EditorMode
    rule: IngestionRule | null
  } | null>(null)

  // Pipeline stats
  const {
    data: statsData,
    isLoading: statsLoading,
    isError: statsError,
    refetch: refetchStats,
  } = usePipelineStats('24h')

  // Active rules
  const {
    data: activeRulesResp,
    isLoading: rulesLoading,
    isError: rulesError,
    refetch: refetchRules,
  } = useIngestionRules({ enabled: true })

  // Archived rules (soft-deleted = deleted_at is set). The backend returns these
  // only when ?archived=true is passed; this is the PARAMS argument (query
  // string), NOT the react-query options. Passing { enabled: false } here was a
  // bug: it queried ?enabled=false (active, disabled rules — none) and left the
  // archived view permanently empty.
  const {
    data: archivedRulesResp,
    isLoading: archivedLoading,
  } = useIngestionRules({ archived: true })

  const updateRule = useUpdateIngestionRule()
  const deleteRule = useDeleteIngestionRule()

  // Priority senders — backed by public.priority_contacts (the runtime source
  // of truth), NOT the ingestion-rule DSL proxy.
  const {
    data: priorityContactsResp,
    isLoading: priorityLoading,
    isError: priorityError,
  } = usePriorityContacts()
  const addPriorityContact = useAddPriorityContact()
  const removePriorityContact = useRemovePriorityContact()

  // Contact candidates for the add picker.
  const { data: contactsResp, isLoading: contactsLoading } = useContacts({ limit: 200 })

  // Channel defaults edit — fetch the current policy only while a channel is
  // actively being edited (the backend 404s for unset channels; that's an
  // expected "not configured yet" state, not a fetch error).
  const {
    data: editingPolicyResp,
    isLoading: editingPolicyLoading,
    isError: editingPolicyIsError,
    error: editingPolicyError,
  } = useChannelDefault(editingChannel ?? '', { enabled: editingChannel !== null })
  const updateChannelDefault = useUpdateChannelDefault()

  // -------------------------------------------------------------------------
  // Derived data
  // -------------------------------------------------------------------------

  const allActiveRules: IngestionRule[] = activeRulesResp?.data ?? []
  // ?archived=true already scopes the response to soft-deleted rules
  // (deleted_at set); no client-side filtering needed.
  const archivedRules: IngestionRule[] = archivedRulesResp?.data ?? []

  const priorityContacts = priorityContactsResp?.data ?? []
  const contactCandidates = contactsResp?.contacts ?? []

  // Split out special-purpose rules before gate bucketing
  const channelDefaultRules = allActiveRules.filter(isChannelDefault)
  const gatableRules = allActiveRules.filter((r) => !isChannelDefault(r))

  const rulesByGate = groupRulesByGate(gatableRules)

  // Pipeline funnel counts
  const pipelineStats = statsData
  const gateCounts = pipelineStats
    ? deriveGateCounts(pipelineStats)
    : GATE_DEFS.map((g) => ({ key: g.key as GateKey, in: 0, out: 0, preserved: 0, dropped: 0 }))

  const statsAvailable = !statsError && pipelineStats?.aggregates_available === true
  const statsInitialLoading = statsLoading && !pipelineStats
  const totalReceived = pipelineStats
    ? pipelineStats.ingested + pipelineStats.filtered
    : 0

  // A 404 means the channel has no policy row yet — treat as "not configured"
  // (empty form), not a fetch error. Any other failure surfaces as an error.
  const editingPolicyNotFound =
    editingPolicyIsError && editingPolicyError instanceof ApiError && editingPolicyError.status === 404
  const editingState: ChannelDefaultEditorState = {
    loading: editingPolicyLoading,
    notFound: editingPolicyNotFound,
    error: editingPolicyIsError && !editingPolicyNotFound,
    policy: editingPolicyResp?.default_policy_json ?? null,
  }

  // -------------------------------------------------------------------------
  // Mutation handlers
  // -------------------------------------------------------------------------

  function handleToggleRule(id: string, enabled: boolean) {
    setToggleError(null)
    setDeleteError(null)
    updateRule.mutate(
      { id, body: { enabled } },
      {
        onError: (err) => {
          setToggleError(
            err instanceof Error ? err.message : 'Failed to toggle rule.',
          )
        },
      },
    )
  }

  function handleDeleteRule(id: string) {
    setToggleError(null)
    setDeleteError(null)
    deleteRule.mutate(id, {
      onError: (err) => {
        setDeleteError(
          err instanceof Error ? err.message : 'Failed to delete rule.',
        )
      },
    })
  }

  function handleAddPrioritySender(contactId: string) {
    setPriorityMutationError(null)
    addPriorityContact.mutate(
      { contact_id: contactId },
      {
        onError: (err) => {
          setPriorityMutationError(
            err instanceof Error ? err.message : 'Failed to add priority sender.',
          )
        },
      },
    )
  }

  function handleRemovePrioritySender(contactId: string) {
    setPriorityMutationError(null)
    removePriorityContact.mutate(
      { contactId },
      {
        onError: (err) => {
          setPriorityMutationError(
            err instanceof Error ? err.message : 'Failed to remove priority sender.',
          )
        },
      },
    )
  }

  function handleEditChannelDefault(channel: string) {
    setChannelMutationError(null)
    setEditingChannel(channel)
  }

  function handleCancelEditChannelDefault() {
    setEditingChannel(null)
  }

  function handleSaveChannelDefault(channel: string, policy: ChannelDefaultPolicy) {
    setChannelMutationError(null)
    updateChannelDefault.mutate(
      { channel, body: { default_policy_json: policy, updated_by: 'dashboard' } },
      {
        onSuccess: () => setEditingChannel(null),
        onError: (err) => {
          setChannelMutationError(
            err instanceof Error ? err.message : 'Failed to update channel default.',
          )
        },
      },
    )
  }

  function handleRestoreRule(id: string) {
    setRestoreError(null)
    updateRule.mutate(
      { id, body: { enabled: true } },
      {
        onError: (err) => {
          setRestoreError(
            err instanceof Error ? err.message : 'Failed to restore rule.',
          )
        },
      },
    )
  }

  // Rule editor wiring -------------------------------------------------------

  function handleAddRule() {
    setEditorState({ mode: 'create', rule: null })
  }

  function handleOpenDsl() {
    setEditorState({ mode: 'dsl', rule: null })
  }

  function handleEditRule(id: string) {
    const target = allActiveRules.find((r) => r.id === id) ?? null
    if (!target) return
    setEditorState({ mode: 'edit', rule: target })
  }

  function handleCloseEditor() {
    setEditorState(null)
  }

  // -------------------------------------------------------------------------
  // Loading state
  // -------------------------------------------------------------------------

  if (statsLoading && rulesLoading) {
    return (
      <div className="space-y-4 py-6">
        {Array.from({ length: 3 }).map((_, i) => (
          <div key={i} className="h-16 bg-foreground/5" />
        ))}
      </div>
    )
  }

  // -------------------------------------------------------------------------
  // Render
  // -------------------------------------------------------------------------

  return (
    <div data-testid="filters-pipeline">
      {/* Error banners */}
      {(toggleError || deleteError) && (
        <div
          className="mb-4 font-mono text-[11px] text-[var(--red-text)] border border-[var(--red)]/30 px-3 py-2"
          data-testid="filters-mutation-error"
        >
          {toggleError ?? deleteError}
        </div>
      )}

      {/* Stats header band */}
      <div className="flex items-baseline gap-3 mb-1">
        <span className="font-mono text-[10px] tracking-[0.14em] uppercase text-muted-foreground/70">
          {statsAvailable
            ? `${totalReceived.toLocaleString()} events · last 24h`
            : statsInitialLoading
              ? 'loading metrics'
              : 'metrics unavailable'}
        </span>
      </div>

      {/* Five-gate diagram */}
      <PipelineGateDiagram
        counts={gateCounts}
        loading={statsInitialLoading}
        available={statsAvailable}
        onRetry={() => void refetchStats()}
      />

      {/* DB-backed execution backlog: independent of Prometheus funnel metrics. */}
      <ExecutionBacklog
        stats={pipelineStats}
        loading={statsLoading}
        statsError={statsError}
        onRetry={() => void refetchStats()}
      />

      {/* Gate sections */}
      <div className="mt-14">
        {GATE_DEFS.map((def, i) => (
          <GateSection
            key={def.key}
            def={def}
            count={gateCounts[i] ?? { key: def.key, in: 0, out: 0, preserved: 0, dropped: 0 }}
            index={i}
            rules={rulesByGate[def.key] ?? []}
            metricsLoading={statsInitialLoading}
            metricsAvailable={statsAvailable}
            rulesLoading={rulesLoading}
            rulesError={rulesError}
            onRetryRules={() => void refetchRules()}
            onToggleRule={handleToggleRule}
            onEditRule={handleEditRule}
            onDeleteRule={handleDeleteRule}
          />
        ))}
      </div>

      {/* Priority senders + channel defaults (two-column) */}
      <div
        className="mt-16 grid gap-14"
        style={{ gridTemplateColumns: '1.3fr 1fr' }}
      >
        <PrioritySendersBlock
          contacts={priorityContacts}
          loaded={!priorityLoading}
          error={priorityError}
          mutationError={priorityMutationError}
          addCandidates={contactCandidates}
          candidatesLoading={contactsLoading}
          onAdd={handleAddPrioritySender}
          onRemove={handleRemovePrioritySender}
        />
        <ChannelDefaultsBlock
          rules={channelDefaultRules}
          loaded={!rulesLoading}
          error={rulesError}
          onRetry={() => void refetchRules()}
          mutationError={channelMutationError}
          editingChannel={editingChannel}
          editingState={editingState}
          saving={updateChannelDefault.isPending}
          onEdit={handleEditChannelDefault}
          onSaveEdit={handleSaveChannelDefault}
          onCancelEdit={handleCancelEditChannelDefault}
        />
      </div>

      {/* Archived rules */}
      {!archivedLoading && (
        <ArchivedRulesSection
          rules={archivedRules}
          onRestore={handleRestoreRule}
          restoreError={restoreError}
        />
      )}

      {/* Footer */}
      <div
        className="mt-14 pt-6 border-t border-border flex items-baseline gap-4"
        data-testid="filters-footer"
      >
        <div className="max-w-[52ch]">
          <span className="font-mono text-[10px] tracking-[0.14em] uppercase text-muted-foreground">
            add rule
          </span>
          <p className="mt-1.5 font-serif text-[13.5px] text-muted-foreground leading-[1.55]">
            Rules are written in a small DSL: channel matchers, sender / kind /
            header predicates, and one verdict per rule:{' '}
            <code className="font-mono text-[11px]">skip</code>,{' '}
            <code className="font-mono text-[11px]">metadata_only</code>,{' '}
            <code className="font-mono text-[11px]">low_priority_queue</code>,{' '}
            <code className="font-mono text-[11px]">pass_through</code>,{' '}
            <code className="font-mono text-[11px]">route_to:&lt;butler&gt;</code>.
          </p>
        </div>
        <span className="ml-auto" />
        <button
          type="button"
          className="font-mono text-[11px] border border-foreground px-3 py-1.5 hover:bg-foreground hover:text-background transition-colors"
          data-testid="filters-add-rule"
          onClick={handleAddRule}
        >
          + add rule
        </button>
        <button
          type="button"
          className="font-mono text-[11px] border border-foreground/30 px-3 py-1.5 hover:bg-foreground/5 transition-colors text-muted-foreground"
          data-testid="filters-open-dsl"
          onClick={handleOpenDsl}
        >
          open DSL
        </button>
      </div>

      {/* Rule editor (create / edit / DSL) */}
      {editorState && (
        <RuleEditor
          key={`${editorState.mode}:${editorState.rule?.id ?? 'new'}`}
          mode={editorState.mode}
          rule={editorState.rule}
          onClose={handleCloseEditor}
        />
      )}
    </div>
  )
}
