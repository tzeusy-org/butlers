// ---------------------------------------------------------------------------
// WhatBreaks — list of butler features that depend on a credential (bu-qo3sf)
//
// Fetches from GET /api/secrets/breaks-catalogue?provider=<p> and renders the
// entries ordered by severity DESC (high → medium → low).
//
// butler-secrets §Evidence-Over-Value Affordance Contract §4:
//   "WhatBreaks list — butler features that will silently fail if the
//   credential is sick; severity pip per row; rendered from
//   public.provider_feature_catalogue server-side, never from a static
//   frontend JSON."
//
// Data is fetched via TanStack Query. The component shows a loading skeleton
// (mono "loading…"), an error fallback (mono dim "unavailable"), an empty
// state (Voice italic "Nothing depends on this credential."), and the full
// sorted list when data is available.
//
// WhatBreaks intentionally has no LLM integration — content comes exclusively
// from the server-side provider_feature_catalogue table.
// ---------------------------------------------------------------------------

import * as React from "react"

import { useQuery } from "@tanstack/react-query"

import { getBreaksCatalogue } from "@/api/client"
import type { BreakEntry } from "@/api/types"
import { Mono } from "@/components/ui/Mono"
import { Voice } from "@/components/ui/Voice"
import { cn } from "@/lib/utils"

import { ProviderMark } from "./ProviderMark"
import { SeverityPip } from "./SeverityPip"
import type { Severity } from "./SeverityPip"
import type { CapabilityStatus } from "./types"

// ---------------------------------------------------------------------------
// Severity ordering
// ---------------------------------------------------------------------------

const SEVERITY_ORDER: Record<Severity, number> = {
  high:   0,
  medium: 1,
  low:    2,
}

function sortBySeverityDesc(entries: BreakEntry[]): BreakEntry[] {
  return [...entries].sort(
    (a, b) =>
      SEVERITY_ORDER[a.severity as Severity] -
      SEVERITY_ORDER[b.severity as Severity],
  )
}

// ---------------------------------------------------------------------------
// Capability probe pip — live ok/fail glyph, replacing the static severity
// pip when a matching capability probe result is available (bu-4v5es).
// ---------------------------------------------------------------------------

function CapabilityProbePip({ status }: { status: CapabilityStatus }) {
  const ok = status.test?.ok ?? null

  if (ok === null) {
    const label = `${status.capability}: not yet probed`
    return (
      <span
        role="img"
        aria-label={label}
        title={label}
        className="font-mono text-[11px] font-normal leading-none tabular-nums shrink-0 inline-block w-4 text-center"
        style={{ color: "var(--dim,oklch(0.55_0_0))" }}
      >
        ?
      </span>
    )
  }

  const label = `${status.capability}: ${ok ? "ok" : status.test?.message ?? "failed"}`
  return (
    <span
      role="img"
      aria-label={label}
      title={label}
      className="font-mono text-[11px] font-normal leading-none tabular-nums shrink-0 inline-block w-4 text-center"
      style={{ color: ok ? "var(--green,oklch(0.65_0.15_145))" : "var(--red)" }}
    >
      {ok ? "✓" : "✗"}
    </span>
  )
}

// ---------------------------------------------------------------------------
// WhatBreaks row
// ---------------------------------------------------------------------------

interface WhatBreaksRowProps {
  entry: BreakEntry
  capabilities?: CapabilityStatus[]
}

function WhatBreaksRow({ entry, capabilities }: WhatBreaksRowProps) {
  const capabilityStatus = entry.capability
    ? capabilities?.find((c) => c.capability === entry.capability)
    : undefined

  return (
    <div
      className={cn(
        "flex items-baseline gap-3 py-1.5",
        "border-b border-[var(--border-soft,oklch(1_0_0/0.06))] last:border-b-0",
      )}
    >
      {capabilityStatus ? (
        <CapabilityProbePip status={capabilityStatus} />
      ) : (
        <SeverityPip severity={entry.severity as Severity} />
      )}
      <ProviderMark provider={entry.butler} />
      <Mono className="flex-1 min-w-0">{entry.feature}</Mono>
      <Mono muted className="shrink-0 text-[10px]">{entry.butler}</Mono>
    </div>
  )
}

// ---------------------------------------------------------------------------
// WhatBreaks
// ---------------------------------------------------------------------------

export interface WhatBreaksProps extends React.HTMLAttributes<HTMLDivElement> {
  /**
   * Provider slug to filter the catalogue by.
   * When omitted, the full catalogue is fetched and displayed.
   */
  provider?: string
  /**
   * Per-capability probe state for the credential being shown (bu-4v5es).
   * When a catalogue row's `capability` matches an entry here, its row shows
   * a live ok/fail glyph instead of the static severity pip.
   */
  capabilities?: CapabilityStatus[]
}

/**
 * Credential dependency list — features that will silently fail if this
 * credential is sick.
 *
 * Fetches from /api/secrets/breaks-catalogue and renders entries sorted
 * severity DESC. Never uses LLM-generated content.
 *
 * @example
 *   <WhatBreaks provider="google" />
 *   <WhatBreaks />  // full catalogue
 */
export function WhatBreaks({ provider, capabilities, className, ...props }: WhatBreaksProps) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["secrets", "breaks-catalogue", provider ?? "__all__"],
    queryFn: () => getBreaksCatalogue(provider ? { provider } : undefined),
  })

  if (isLoading) {
    return (
      <div className={cn("py-2", className)} {...props}>
        <Mono muted>loading…</Mono>
      </div>
    )
  }

  if (isError || !data) {
    return (
      <div className={cn("py-2", className)} {...props}>
        <Mono muted>unavailable</Mono>
      </div>
    )
  }

  const entries = sortBySeverityDesc(data.data ?? [])

  if (entries.length === 0) {
    return (
      <div className={cn("py-2", className)} {...props}>
        <Voice variant="italic">Nothing depends on this credential.</Voice>
      </div>
    )
  }

  return (
    <div className={cn("flex flex-col", className)} {...props}>
      {entries.map((entry, idx) => (
        <WhatBreaksRow
          key={`${entry.butler}:${entry.feature}:${idx}`}
          entry={entry}
          capabilities={capabilities}
        />
      ))}
    </div>
  )
}
