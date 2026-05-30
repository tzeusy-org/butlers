// ---------------------------------------------------------------------------
// FingerprintRow — two-line credential fingerprint block (bu-qo3sf)
//
// Renders the fingerprint and (optionally) the shell verify command as a
// two-line stack. The verify command line is toggled by the "show verify cmd"
// tweak; it is hidden by default.
//
// butler-secrets §Evidence-Over-Value Affordance Contract §1:
//   "fingerprint pill (sha256:7a3f…, mono 11px)"
//
// butler-secrets §Fingerprint verify command exposure:
//   "WHEN the '+ verify cmd' expander is toggled open on a credential page
//   THEN the page renders a single mono line containing a hard-coded shell
//   command literal of the form 'echo -n '<value>' | sha256sum | cut -c1-8'
//   (where <value> is a placeholder, never the real secret)"
//   "AND no LLM call is made to generate or annotate this command"
// ---------------------------------------------------------------------------

import * as React from "react"

import { Mono } from "@/components/ui/Mono"
import { cn } from "@/lib/utils"

import { Fingerprint } from "./Fingerprint"

export interface FingerprintRowProps extends React.HTMLAttributes<HTMLDivElement> {
  /**
   * Full fingerprint string in "<scheme>:<hash>" format.
   * Example: "sha256:7a3f8e9b"
   */
  fingerprint: string
  /**
   * When true, renders the shell verify command below the fingerprint.
   * Controlled by the "show verify cmd" tweak. Default: false.
   */
  showVerifyCmd?: boolean
}

/** The exact verify command literal — no LLM, no dynamic generation. */
const VERIFY_CMD = "echo -n '<value>' | sha256sum | cut -c1-8"

/**
 * Two-line fingerprint block: fingerprint + optional verify command.
 *
 * @example
 *   <FingerprintRow fingerprint="sha256:7a3f8e9b" />
 *   <FingerprintRow fingerprint="sha256:7a3f8e9b" showVerifyCmd />
 */
export function FingerprintRow({
  fingerprint,
  showVerifyCmd = false,
  className,
  ...props
}: FingerprintRowProps) {
  return (
    <div className={cn("flex flex-col gap-1", className)} {...props}>
      <Fingerprint fingerprint={fingerprint} />
      {showVerifyCmd && (
        <Mono as="code" muted>
          {VERIFY_CMD}
        </Mono>
      )}
    </div>
  )
}
