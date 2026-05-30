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
 * - useIngestionRules()            → all active rules
 * - useIngestionRules({enabled:false}) → archived rules (requires ?archived=true)
 *
 * Priority senders: rules with action starting with "route" and
 * rule_type="priority_sender" (or scope="priority").
 *
 * Channel defaults: rules with scope matching a channel name and
 * rule_type="channel_default".
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
import { GATE_DEFS, groupRulesByGate, deriveGateCounts } from './gate-state'
import type { GateKey } from './gate-state'
import { PipelineGateDiagram } from './PipelineGateDiagram'
import { GateSection } from './GateSection'
import { PrioritySendersBlock } from './PrioritySendersBlock'
import { ChannelDefaultsBlock } from './ChannelDefaultsBlock'
import { ArchivedRulesSection } from './ArchivedRulesSection'
import type { IngestionRule } from '@/api/types'

// ---------------------------------------------------------------------------
// Rule classification helpers
// ---------------------------------------------------------------------------

function isPrioritySender(rule: IngestionRule): boolean {
  return (
    rule.rule_type === 'priority_sender' ||
    rule.scope === 'priority' ||
    (rule.action.toLowerCase().startsWith('route') && rule.rule_type === 'priority')
  )
}

function isChannelDefault(rule: IngestionRule): boolean {
  return (
    rule.rule_type === 'channel_default' ||
    rule.scope === 'channel_default'
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

  // Pipeline stats
  const { data: statsData, isLoading: statsLoading } = usePipelineStats('24h')

  // Active rules
  const {
    data: activeRulesResp,
    isLoading: rulesLoading,
    isError: rulesError,
  } = useIngestionRules({ enabled: true })

  // Archived rules (soft-deleted = deleted_at is set, but still returned when ?archived=true)
  // The backend returns deleted rules with enabled=false and deleted_at set when
  // archived=true is passed. We use enabled=false to fetch them if that param works,
  // otherwise use the full list and filter locally.
  const {
    data: archivedRulesResp,
    isLoading: archivedLoading,
  } = useIngestionRules({ enabled: false })

  const updateRule = useUpdateIngestionRule()
  const deleteRule = useDeleteIngestionRule()

  // -------------------------------------------------------------------------
  // Derived data
  // -------------------------------------------------------------------------

  const allActiveRules: IngestionRule[] = activeRulesResp?.data ?? []
  const archivedRules: IngestionRule[] = (archivedRulesResp?.data ?? []).filter(
    (r) => !r.enabled || r.deleted_at != null,
  )

  // Split out special-purpose rules before gate bucketing
  const prioritySenderRules = allActiveRules.filter(isPrioritySender)
  const channelDefaultRules = allActiveRules.filter(isChannelDefault)
  const gatableRules = allActiveRules.filter(
    (r) => !isPrioritySender(r) && !isChannelDefault(r),
  )

  const rulesByGate = groupRulesByGate(gatableRules)

  // Pipeline funnel counts
  const pipelineStats = statsData
  const gateCounts = pipelineStats
    ? deriveGateCounts(pipelineStats)
    : GATE_DEFS.map((g) => ({ key: g.key as GateKey, in: 0, out: 0, preserved: 0, dropped: 0 }))

  const statsAvailable = pipelineStats?.aggregates_available ?? false
  const totalReceived = pipelineStats
    ? pipelineStats.ingested + pipelineStats.filtered
    : 0

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

  function handleRemovePrioritySender(id: string) {
    setPriorityMutationError(null)
    deleteRule.mutate(id, {
      onError: (err) => {
        setPriorityMutationError(
          err instanceof Error ? err.message : 'Failed to remove priority sender.',
        )
      },
    })
  }

  function handleEditChannelDefault(id: string) {
    // Edit is a future concern; wire the handler once a form exists.
    // Surface a note so the button is not silently a no-op.
    setChannelMutationError('Editing channel defaults is not yet available.')
    void id
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

  // -------------------------------------------------------------------------
  // Loading state
  // -------------------------------------------------------------------------

  if (statsLoading && rulesLoading) {
    return (
      <div className="space-y-4 py-6">
        {Array.from({ length: 3 }).map((_, i) => (
          <div key={i} className="h-16 bg-foreground/5 animate-pulse" />
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
          className="mb-4 font-mono text-[11px] text-[color:var(--filter-red,oklch(0.62_0.20_25))] border border-[color:var(--filter-red,oklch(0.62_0.20_25))]/30 px-3 py-2"
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
            : 'metrics unavailable'}
        </span>
      </div>

      {/* Five-gate diagram */}
      <PipelineGateDiagram counts={gateCounts} available={statsAvailable} />

      {/* Gate sections */}
      <div className="mt-14">
        {GATE_DEFS.map((def, i) => (
          <GateSection
            key={def.key}
            def={def}
            count={gateCounts[i] ?? { key: def.key, in: 0, out: 0, preserved: 0, dropped: 0 }}
            index={i}
            rules={rulesByGate[def.key] ?? []}
            onToggleRule={handleToggleRule}
            onEditRule={() => undefined}
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
          rules={prioritySenderRules}
          loaded={!rulesLoading}
          error={rulesError}
          mutationError={priorityMutationError}
          onRemove={handleRemovePrioritySender}
        />
        <ChannelDefaultsBlock
          rules={channelDefaultRules}
          loaded={!rulesLoading}
          error={rulesError}
          mutationError={channelMutationError}
          onEdit={handleEditChannelDefault}
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
            Rules are written in a small DSL — channel matchers, sender / kind /
            header predicates, and one verdict per rule:{' '}
            <code className="font-mono text-[11px]">drop</code>,{' '}
            <code className="font-mono text-[11px]">preserve</code>,{' '}
            <code className="font-mono text-[11px]">tier</code>,{' '}
            <code className="font-mono text-[11px]">route</code>.
          </p>
        </div>
        <span className="ml-auto" />
        <button
          type="button"
          className="font-mono text-[11px] border border-foreground px-3 py-1.5 hover:bg-foreground hover:text-background transition-colors"
          data-testid="filters-add-rule"
        >
          + add rule
        </button>
        <button
          type="button"
          className="font-mono text-[11px] border border-foreground/30 px-3 py-1.5 hover:bg-foreground/5 transition-colors text-muted-foreground"
          data-testid="filters-open-dsl"
        >
          open DSL
        </button>
      </div>
    </div>
  )
}
