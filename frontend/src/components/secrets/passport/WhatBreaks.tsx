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
// (mono "loading…"), an unavailable fallback (mono amber "unavailable" — a
// fetch error or an unreachable catalogue pool, meta.catalogue_available ===
// false), a not-tracked empty state (mono dim "usage not tracked" — bu-xzaxm),
// and the full sorted list when data is available.
//
// Honesty rule (bu-xzaxm, reusing ConfirmImpact's vocabulary from bu-cyyi3):
// an empty catalogue result for a provider/category means the catalogue has
// no seeded rows for it — that is a coverage gap, not a verified zero. This
// must never render as the confident "Nothing depends on this credential."
// (the previous wording), since that reads as a guarantee the catalogue does
// not actually make.
//
// WhatBreaks intentionally has no LLM integration — content comes exclusively
// from the server-side provider_feature_catalogue table.
// ---------------------------------------------------------------------------

import * as React from "react"

import { useQuery } from "@tanstack/react-query"

import { getBreaksCatalogue } from "@/api/client"
import type { ApiResponse, BreakEntry } from "@/api/types"
import { Mono } from "@/components/ui/Mono"
import { cn } from "@/lib/utils"

import { ProviderMark } from "./atoms.tsx"
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
// Shared breaks-catalogue state derivation (bu-xzaxm) — one honesty ladder
// reused by both the browsing-page WhatBreaks list and the destructive-confirm
// ConfirmImpact (bu-cyyi3), so the two surfaces can never drift apart on what
// counts as "unavailable" vs "not tracked" vs "tracked".
// ---------------------------------------------------------------------------

/** The shared four-state honesty ladder for a breaks-catalogue query. Also
 *  mirrored on `data-confirm-impact-state` for ConfirmImpact tests. */
export type BreaksCatalogueState = "loading" | "unavailable" | "not-tracked" | "tracked"

function deriveBreaksCatalogueState(query: {
  isLoading: boolean
  isError: boolean
  data: ApiResponse<BreakEntry[]> | undefined
}): BreaksCatalogueState {
  if (query.isLoading) return "loading"
  const catalogueAvailable = query.data?.meta?.catalogue_available !== false
  if (query.isError || !query.data || !catalogueAvailable) return "unavailable"
  const entries = query.data.data ?? []
  return entries.length === 0 ? "not-tracked" : "tracked"
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

export function WhatBreaksRow({ entry, capabilities }: WhatBreaksRowProps) {
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
      {/* bu-sd0l7.2: reunified onto the shipping atoms.tsx ProviderMark (used
          by Spine.tsx/pages.tsx elsewhere on this same page). That atom takes
          an explicit glyph rather than deriving one, so the slug's first
          character is uppercased here — the same derivation the now-deleted
          standalone ./ProviderMark.tsx used to do internally. */}
      <ProviderMark glyph={entry.butler.charAt(0).toUpperCase()} label={entry.butler} />
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
  const query = useQuery({
    queryKey: ["secrets", "breaks-catalogue", provider ?? "__all__"],
    queryFn: () => getBreaksCatalogue(provider ? { provider } : undefined),
  })
  const { data } = query
  const state = deriveBreaksCatalogueState(query)

  if (state === "loading") {
    return (
      <div className={cn("py-2", className)} {...props} data-what-breaks-state="loading">
        <Mono muted>loading…</Mono>
      </div>
    )
  }

  if (state === "unavailable") {
    return (
      <div className={cn("py-2", className)} {...props} data-what-breaks-state="unavailable">
        <Mono muted>unavailable</Mono>
      </div>
    )
  }

  if (state === "not-tracked") {
    // Honesty (bu-xzaxm): the catalogue has no seeded rows for this
    // provider/category. That is a coverage gap, not a verified zero — never
    // render this as "Nothing depends on this credential."
    return (
      <div className={cn("py-2", className)} {...props} data-what-breaks-state="not-tracked">
        <Mono muted>usage not tracked</Mono>
      </div>
    )
  }

  const entries = sortBySeverityDesc(data?.data ?? [])

  return (
    <div className={cn("flex flex-col", className)} {...props} data-what-breaks-state="tracked">
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

// ---------------------------------------------------------------------------
// ConfirmImpact — dependent-feature list for destructive confirm panels
// (bu-cyyi3)
//
// Rendered INSIDE the delete (system) / revoke (CLI) / disconnect (user OAuth)
// two-pill confirm panels, reusing WhatBreaksRow's exact vocabulary (severity
// pip + butler letter-mark + feature name) so a destructive confirm is
// informed, not just a generic "yes/cancel" pair.
//
// Honesty rule (bu-xzaxm): the provider_feature_catalogue only covers a
// fixed set of known providers — an empty result here means "we have no
// record of what depends on this," never "nothing depends on this." A
// confirm surface must NEVER let that ambiguity read as an all-clear, so the
// empty case says "impact not tracked" — a confirm-flavored variant of the
// same not-tracked wording WhatBreaks now uses on the browsing page (both
// share deriveBreaksCatalogueState). A catalogue-unreachable fetch failure is
// called out separately as "unavailable" so it is never confused with a
// genuine (if untracked) result.
// ---------------------------------------------------------------------------

/** ConfirmImpact's four render states — also mirrored on the
 *  `data-confirm-impact-state` DOM attribute for tests. Alias of the shared
 *  `BreaksCatalogueState` ladder (bu-xzaxm) that WhatBreaks also uses. */
export type ConfirmImpactState = BreaksCatalogueState

export interface ConfirmImpactProps extends React.HTMLAttributes<HTMLDivElement> {
  /** Provider (or category) slug to look up in the breaks catalogue — the
   *  same value already passed to <WhatBreaks provider={...} /> elsewhere on
   *  the page, so this reuses the cached query result instead of refetching. */
  provider: string
  /** Optional live capability-probe state, forwarded to WhatBreaksRow. */
  capabilities?: CapabilityStatus[]
  /**
   * Called whenever the impact-fetch state changes (including on mount).
   * The enclosing destructive-confirm panel uses this to keep its "yes, …"
   * button disabled while impact is still `"loading"` — an uninformed
   * confirm (fired before the owner has seen what depends on this
   * credential) would defeat the whole point of this component.
   */
  onStateChange?: (state: ConfirmImpactState) => void
}

/**
 * ConfirmImpact — impact-aware dependent list for destructive confirms.
 *
 * @example
 *   <ConfirmImpact provider="google" />
 */
export function ConfirmImpact({
  provider,
  capabilities,
  className,
  onStateChange,
  ...props
}: ConfirmImpactProps) {
  const query = useQuery({
    queryKey: ["secrets", "breaks-catalogue", provider],
    queryFn: () => getBreaksCatalogue({ provider }),
  })
  const { data } = query
  const entries = data ? sortBySeverityDesc(data.data ?? []) : []
  const state = deriveBreaksCatalogueState(query)

  React.useEffect(() => {
    onStateChange?.(state)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- onStateChange is expected to be a stable setter (e.g. useState's dispatch); only re-fire when the derived state itself changes, not on every caller re-render.
  }, [state])

  if (state === "loading") {
    return (
      <div className={cn("py-1", className)} {...props} data-confirm-impact-state="loading">
        <Mono muted>checking impact…</Mono>
      </div>
    )
  }

  if (state === "unavailable") {
    return (
      <div className={cn("py-1", className)} {...props} data-confirm-impact-state="unavailable">
        <Mono style={{ color: "var(--amber-text)" }}>
          impact unavailable: could not reach the dependency catalogue.
        </Mono>
      </div>
    )
  }

  if (state === "not-tracked") {
    return (
      <div className={cn("py-1", className)} {...props} data-confirm-impact-state="not-tracked">
        <Mono muted>impact not tracked for this credential.</Mono>
      </div>
    )
  }

  return (
    <div className={cn("flex flex-col", className)} {...props} data-confirm-impact-state="tracked">
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
