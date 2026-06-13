/**
 * Filters pipeline — barrel export for /ingestion/filters components.
 */

export { FiltersPipeline } from './FiltersPipeline'
export { PipelineGateDiagram } from './PipelineGateDiagram'
export { GateSection } from './GateSection'
export { RuleRow } from './RuleRow'
export { PrioritySendersBlock } from './PrioritySendersBlock'
export { ChannelDefaultsBlock } from './ChannelDefaultsBlock'
export { ArchivedRulesSection } from './ArchivedRulesSection'
export { RuleEditor } from './RuleEditor'
export type { EditorMode, RuleEditorProps } from './RuleEditor'
export {
  GATE_DEFS,
  gateForRule,
  groupRulesByGate,
  deriveGateCounts,
} from './gate-state'
export type { GateKey, GateDefinition, GateCount } from './gate-state'
