// ---------------------------------------------------------------------------
// ProbeResult — most recent TestResult display (bu-qo3sf)
//
// Renders the result of the most recent credential probe: ok/fail, HTTP code,
// latency ms, pre-formatted timestamp, and an optional verbatim message tail.
//
// butler-secrets §Evidence-Over-Value Affordance Contract §5:
//   "most recent TestResult: ok/fail, HTTP code (when applicable), latency ms,
//   pre-formatted timestamp (server-formatted, e.g. '14:21 today'), serif-italic
//   message tail (verbatim provider error, never LLM-elaborated)."
//
// The message tail is rendered in Voice italic — never generated or modified
// by any LLM. It is the raw provider error, pre-formatted on the server.
// ---------------------------------------------------------------------------

import * as React from "react"

import { Mono } from "@/components/ui/Mono"
import { Voice } from "@/components/ui/Voice"
import { cn } from "@/lib/utils"

/** Probe outcome. */
export type ProbeOutcome = "ok" | "fail"

export interface ProbeResultProps extends React.HTMLAttributes<HTMLDivElement> {
  /** Whether the probe succeeded. */
  outcome: ProbeOutcome
  /**
   * HTTP status code from the probe, when applicable.
   * Omit for non-HTTP credentials.
   */
  httpCode?: number
  /** Probe round-trip latency in milliseconds. */
  latencyMs: number
  /**
   * Pre-formatted timestamp string from the server.
   * Example: "14:21 today", "3 May 09:03"
   */
  timestamp: string
  /**
   * Optional verbatim message tail from the provider.
   * Rendered in Voice italic. Never LLM-generated.
   */
  message?: string
}

/**
 * Probe result strip: outcome + code + latency + timestamp + message.
 *
 * @example
 *   <ProbeResult outcome="ok" latencyMs={142} timestamp="14:21 today" />
 *   <ProbeResult
 *     outcome="fail"
 *     httpCode={401}
 *     latencyMs={89}
 *     timestamp="09:03 today"
 *     message="Token expired: 401 Unauthorized"
 *   />
 */
export function ProbeResult({
  outcome,
  httpCode,
  latencyMs,
  timestamp,
  message,
  className,
  ...props
}: ProbeResultProps) {
  const outcomeColor =
    outcome === "ok"
      ? "var(--green)"
      : "var(--red)"

  return (
    <div className={cn("flex flex-col gap-1.5 py-2", className)} {...props}>
      <div className="flex items-baseline flex-wrap gap-3">
        {/* Outcome */}
        <Mono style={{ color: outcomeColor }}>{outcome}</Mono>

        {/* HTTP code — only when present */}
        {httpCode !== undefined && (
          <Mono muted>{httpCode}</Mono>
        )}

        {/* Latency */}
        <Mono muted>{latencyMs}ms</Mono>

        {/* Timestamp */}
        <Mono muted>{timestamp}</Mono>
      </div>

      {/* Verbatim message tail — serif italic, never LLM-generated */}
      {message && (
        <Voice variant="italic" className="text-[13px] leading-[1.4]">
          {message}
        </Voice>
      )}
    </div>
  )
}
