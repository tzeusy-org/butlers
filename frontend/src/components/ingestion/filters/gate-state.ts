/**
 * gate-state.ts — maps IngestionRule fields to per-gate buckets.
 *
 * The backend exposes a flat list of IngestionRule objects.  This module
 * derives which pipeline gate each rule fires at based on its `action`
 * field, following the same bucketing used by the prototype
 * (ingestion dispatch redesign, graduated) ingestion-filters.jsx.
 *
 * Gate order: accept → dedupe → tier → route → execute
 *
 * Action → gate mapping:
 *   drop / preserve / allow  → accept (these control whether an event survives the entry gate)
 *   dedupe                   → dedupe
 *   tier                     → tier
 *   route                    → route
 *   execute / replay         → execute
 *   everything else          → accept (fallback)
 *
 * Spec: openspec/changes/complete-ingestion-redesign-parity/specs/
 *       dashboard-ingestion-dispatch-console/spec.md §"Filters Pipeline"
 */

import type { IngestionRule, PipelineStats } from '@/api/types'

// ---------------------------------------------------------------------------
// Gate definitions
// ---------------------------------------------------------------------------

export type GateKey = 'accept' | 'dedupe' | 'tier' | 'route' | 'execute'

export interface GateDefinition {
  key: GateKey
  label: string
  /** Short serif gloss explaining what this gate does. */
  gloss: string
  /**
   * Code-resident behavior note for gates that have no configurable rules
   * yet — shown instead of "no rules" when the rules list is empty.
   */
  codePolicy: string | null
}

export const GATE_DEFS: GateDefinition[] = [
  {
    key: 'accept',
    label: 'accept',
    gloss:
      'First contact: channel authentication, duplicate suppression window, and explicit block/allow rules.',
    codePolicy: null,
  },
  {
    key: 'dedupe',
    label: 'dedupe',
    gloss:
      'Canonicalises (source, ts) before checking the dedup window. Drops exact-duplicate envelopes.',
    codePolicy:
      'Deduplication logic lives in code, not in the rules DSL. Window: 90s for sensor data, 24h for content.',
  },
  {
    key: 'tier',
    label: 'tier',
    gloss: 'Assigns a processing tier (priority vs. standard) based on sender or content rules.',
    codePolicy: null,
  },
  {
    key: 'route',
    label: 'route',
    gloss:
      'Resolves which butler handles this event — or marks it as preserved-without-dispatch for audit.',
    codePolicy: null,
  },
  {
    key: 'execute',
    label: 'execute',
    gloss: 'Spawns the butler session. Failures retry up to 3 times, then queue for replay.',
    codePolicy:
      'Execution policy lives in code. Exponential back-off, 3 retries, then manual replay queue.',
  },
]

// ---------------------------------------------------------------------------
// Rule → gate bucketing
// ---------------------------------------------------------------------------

export function gateForRule(rule: IngestionRule): GateKey {
  const action = rule.action.toLowerCase()
  if (action.startsWith('drop') || action.startsWith('preserve') || action.startsWith('allow')) {
    return 'accept'
  }
  if (action.startsWith('dedupe')) return 'dedupe'
  if (action.startsWith('tier')) return 'tier'
  if (action.startsWith('route')) return 'route'
  if (action.startsWith('execute') || action.startsWith('replay')) return 'execute'
  // fallback: accept
  return 'accept'
}

export function groupRulesByGate(rules: IngestionRule[]): Record<GateKey, IngestionRule[]> {
  const groups: Record<GateKey, IngestionRule[]> = {
    accept: [],
    dedupe: [],
    tier: [],
    route: [],
    execute: [],
  }
  for (const rule of rules) {
    const gate = gateForRule(rule)
    groups[gate].push(rule)
  }
  return groups
}

// ---------------------------------------------------------------------------
// Pipeline stats → per-gate counts
// ---------------------------------------------------------------------------

/**
 * Derives per-gate in/out/drop counts from PipelineStats.
 *
 * PipelineStats only exposes top-level ingested, filtered, errored.
 * We synthesise a 5-step funnel from those values:
 *
 *   accept:   in=ingested+filtered, out=ingested
 *   dedupe:   in=ingested,          out=ingested-errored (rough estimate)
 *   tier:     in=dedupe.out,        out=dedupe.out
 *   route:    in=tier.out,          out=sum(routed_by_butler), preserved=tier.out-sum(routed)
 *   execute:  in=route.out+preserved, out=route.out
 *
 * When aggregates_available=false all values are zero.
 */
export interface GateCount {
  key: GateKey
  in: number
  out: number
  /** Events routed but not dispatched (preserved-without-dispatch, route gate only). */
  preserved: number
  /** Hard drops (filtered out). */
  dropped: number
}

export function deriveGateCounts(stats: PipelineStats): GateCount[] {
  if (!stats.aggregates_available) {
    return GATE_DEFS.map((g) => ({ key: g.key, in: 0, out: 0, preserved: 0, dropped: 0 }))
  }

  const totalIn = stats.ingested + stats.filtered
  const routedTotal = Object.values(stats.routed_by_butler).reduce((a, b) => a + b, 0)
  // preserved = events that passed route but weren't dispatched (logged for audit)
  const preserved = Math.max(0, stats.ingested - routedTotal)

  const accept: GateCount = {
    key: 'accept',
    in: totalIn,
    out: stats.ingested,
    preserved: 0,
    dropped: stats.filtered,
  }
  const dedupe: GateCount = {
    key: 'dedupe',
    in: stats.ingested,
    out: stats.ingested,  // no visibility into dedup counts from this endpoint
    preserved: 0,
    dropped: 0,
  }
  const tier: GateCount = {
    key: 'tier',
    in: stats.ingested,
    out: stats.ingested,  // all pass tier; tiering just changes processing priority
    preserved: 0,
    dropped: 0,
  }
  const route: GateCount = {
    key: 'route',
    in: stats.ingested,
    out: routedTotal,
    preserved,
    dropped: 0, // no hard drops at route gate
  }
  const execute: GateCount = {
    key: 'execute',
    in: routedTotal,
    out: routedTotal, // all routed events attempt execution
    preserved: 0,
    dropped: stats.errored,
  }

  return [accept, dedupe, tier, route, execute]
}
