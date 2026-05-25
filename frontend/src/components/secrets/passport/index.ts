// ---------------------------------------------------------------------------
// Passport-book primitives barrel — bu-qo3sf
//
// Re-exports all passport-book primitive components for clean import paths:
//   import { Sliver, StateLabel, WhatBreaks } from "@/components/secrets/passport"
// ---------------------------------------------------------------------------

export { Sliver } from "./Sliver"
export type { SliverProps, CredentialState } from "./Sliver"

export { StateLabel } from "./StateLabel"
export type { StateLabelProps } from "./StateLabel"

export { Fingerprint } from "./Fingerprint"
export type { FingerprintProps } from "./Fingerprint"

export { FingerprintRow } from "./FingerprintRow"
export type { FingerprintRowProps } from "./FingerprintRow"

export { KV } from "./KV"
export type { KVProps } from "./KV"

export { BlockHead } from "./BlockHead"
export type { BlockHeadProps } from "./BlockHead"

export { StampGlyph } from "./StampGlyph"
export type { StampGlyphProps, AuditAction } from "./StampGlyph"

export { StampRow } from "./StampRow"
export type { StampRowProps } from "./StampRow"

export { SeverityPip } from "./SeverityPip"
export type { SeverityPipProps, Severity } from "./SeverityPip"

export { ProviderMark } from "./ProviderMark"
export type { ProviderMarkProps } from "./ProviderMark"

export { IdentityChip } from "./IdentityChip"
export type { IdentityChipProps, IdentityRole } from "./IdentityChip"

export { ScopeRow, ScopeBalance, VisaRow } from "./ScopeRow"
export type { ScopeRowProps, ScopeBalanceProps, VisaRowProps, ScopeStatus } from "./ScopeRow"

export { ProbeResult } from "./ProbeResult"
export type { ProbeResultProps, ProbeOutcome } from "./ProbeResult"

export { WhatBreaks } from "./WhatBreaks"
export type { WhatBreaksProps } from "./WhatBreaks"
