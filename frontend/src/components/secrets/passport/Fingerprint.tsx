// ---------------------------------------------------------------------------
// Fingerprint — hash display with scheme/hash split (bu-qo3sf)
//
// Renders a credential fingerprint as a compact mono pill showing the scheme
// and the truncated hash: "sha256:7a3f…"
//
// butler-secrets §Evidence-Over-Value Affordance Contract §1:
//   "fingerprint pill (sha256:7a3f…, mono 11px)"
//
// Fingerprints are NEVER stored — they are computed on-read via
// PostgreSQL sha256(<secret_value>)::text and truncated to 8 hex chars.
// This component only renders; it never derives the fingerprint.
//
// Fingerprint format: "<scheme>:<8-hex-chars>"
// Example: "sha256:7a3f8e9b"
// ---------------------------------------------------------------------------

import * as React from "react"

import { Mono } from "@/components/ui/Mono"
import { cn } from "@/lib/utils"

export interface FingerprintProps extends React.HTMLAttributes<HTMLSpanElement> {
  /**
   * Full fingerprint string in "<scheme>:<hash>" format.
   * Example: "sha256:7a3f8e9b"
   */
  fingerprint: string
}

/**
 * Compact fingerprint display: scheme·hash in mono 11px.
 *
 * Splits on the first ":" to render scheme and hash with the separator
 * in muted colour, giving visual hierarchy without changing the content.
 *
 * @example
 *   <Fingerprint fingerprint="sha256:7a3f8e9b" />
 */
export function Fingerprint({ fingerprint, className, ...props }: FingerprintProps) {
  const colonIdx = fingerprint.indexOf(":")
  const scheme = colonIdx >= 0 ? fingerprint.slice(0, colonIdx) : fingerprint
  const hash = colonIdx >= 0 ? fingerprint.slice(colonIdx + 1) : ""

  return (
    <span
      className={cn("inline-flex items-baseline gap-0", className)}
      {...props}
    >
      <Mono muted>{scheme}</Mono>
      {hash && (
        <>
          <Mono muted>:</Mono>
          <Mono>{hash}</Mono>
        </>
      )}
    </span>
  )
}
