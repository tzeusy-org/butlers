/**
 * Dispatch primitives — shared layout components for the ingestion dispatch console.
 *
 * Import from this central location:
 *   import { DispatchLayout, DispatchHeader, DispatchSurface } from '@/components/ingestion/dispatch'
 *
 * These primitives are the foundation that Timeline, Connectors, Connector detail,
 * and Filters route children reuse. They encode the Dispatch visual language:
 * hairline rules, no card chrome, type hierarchy over shadow/fill.
 *
 * Spec: openspec/changes/complete-ingestion-redesign-parity/specs/
 *       dashboard-ingestion-dispatch-console/spec.md §"Dispatch Visual Language"
 */

export { DispatchLayout } from './DispatchLayout'
export { DispatchHeader } from './DispatchHeader'
export { DispatchSurface } from './DispatchSurface'
