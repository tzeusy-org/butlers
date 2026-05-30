// ---------------------------------------------------------------------------
// ScopeRow, ScopeBalance, VisaRow — scope inventory components (bu-qo3sf)
//
// butler-secrets §Evidence-Over-Value Affordance Contract §3:
//   "Scopes inventory (when applicable) — granted vs required scopes; missing
//   scopes called out in --amber; over-grant noted dim."
//
// ScopeRow    — single scope line: status icon + scope name (+ "required" tag)
// ScopeBalance — summary line: "N of M required scopes granted"
// VisaRow     — a scope with both granted and required annotation, used in
//               full visa-style scope tables (e.g. user OAuth pages)
// ---------------------------------------------------------------------------

import * as React from "react"

import { Eyebrow } from "@/components/ui/Eyebrow"
import { Mono } from "@/components/ui/Mono"
import { cn } from "@/lib/utils"

// ---------------------------------------------------------------------------
// ScopeRow
// ---------------------------------------------------------------------------

/** Status of a single scope. */
export type ScopeStatus = "granted" | "missing" | "extra"

export interface ScopeRowProps extends React.HTMLAttributes<HTMLDivElement> {
  /** Scope identifier string. */
  scope: string
  /**
   * Status drives colour:
   *   "granted" → full foreground (ok)
   *   "missing"  → --amber (required but absent)
   *   "extra"    → --dim   (granted beyond requirements — over-grant)
   */
  status: ScopeStatus
  /** When true, renders a "required" eyebrow tag after the scope name. */
  required?: boolean
}

const SCOPE_COLORS: Record<ScopeStatus, string> = {
  granted: "var(--fg,oklch(0.985_0_0))",
  missing: "var(--amber)",
  extra:   "var(--dim,oklch(0.55_0_0))",
}

const SCOPE_GLYPHS: Record<ScopeStatus, string> = {
  granted: "✓",
  missing: "!",
  extra:   "·",
}

/**
 * Single scope row: glyph + scope name + optional "required" tag.
 *
 * @example
 *   <ScopeRow scope="https://www.googleapis.com/auth/calendar" status="granted" />
 *   <ScopeRow scope="https://www.googleapis.com/auth/contacts" status="missing" required />
 */
export function ScopeRow({
  scope,
  status,
  required = false,
  className,
  ...props
}: ScopeRowProps) {
  const color = SCOPE_COLORS[status]
  const glyph = SCOPE_GLYPHS[status]

  return (
    <div
      className={cn(
        "flex items-baseline gap-2 py-1",
        "border-b border-[var(--border-soft,oklch(1_0_0/0.06))] last:border-b-0",
        className,
      )}
      {...props}
    >
      <span
        aria-hidden="true"
        className="font-mono text-[11px] leading-none tabular-nums shrink-0 w-4 text-center"
        style={{ color }}
      >
        {glyph}
      </span>
      <Mono className="flex-1 min-w-0 break-all" style={{ color }}>
        {scope}
      </Mono>
      {required && (
        <Eyebrow as="span" className="shrink-0">
          required
        </Eyebrow>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// ScopeBalance
// ---------------------------------------------------------------------------

export interface ScopeBalanceProps extends React.HTMLAttributes<HTMLDivElement> {
  /** Number of required scopes that are granted. */
  granted: number
  /** Total number of required scopes. */
  required: number
}

/**
 * Summary scope balance: "N of M required scopes granted".
 *
 * Renders in --amber if not fully balanced (granted < required).
 *
 * @example
 *   <ScopeBalance granted={5} required={7} />  // amber: "5 of 7 required scopes granted"
 *   <ScopeBalance granted={7} required={7} />  // dim: "7 of 7 required scopes granted"
 */
export function ScopeBalance({ granted, required, className, ...props }: ScopeBalanceProps) {
  const balanced = granted >= required
  const color = balanced
    ? "var(--dim,oklch(0.55_0_0))"
    : "var(--amber)"

  return (
    <div
      className={cn("flex items-center gap-1 py-1", className)}
      {...props}
    >
      <Mono style={{ color }}>
        {granted} of {required} required scope{required !== 1 ? "s" : ""} granted
      </Mono>
    </div>
  )
}

// ---------------------------------------------------------------------------
// VisaRow
// ---------------------------------------------------------------------------

export interface VisaRowProps extends React.HTMLAttributes<HTMLDivElement> {
  /** Scope identifier string. */
  scope: string
  /** Whether this scope is currently granted. */
  granted: boolean
  /** Whether this scope is required by at least one butler. */
  required: boolean
  /**
   * Butler names that require this scope.
   * Used for tooltip/title; not rendered inline.
   */
  requiredBy?: string[]
}

/**
 * Full visa-style scope row with granted + required indicators.
 *
 * Used in the User OAuth page where both granted and required state matter.
 *
 * @example
 *   <VisaRow scope="https://www.googleapis.com/auth/calendar" granted required />
 *   <VisaRow scope="https://www.googleapis.com/auth/contacts" granted={false} required />
 */
export function VisaRow({
  scope,
  granted,
  required,
  requiredBy,
  className,
  ...props
}: VisaRowProps) {
  let status: ScopeStatus
  if (!granted && required) {
    status = "missing"
  } else if (granted && !required) {
    status = "extra"
  } else {
    status = "granted"
  }

  return (
    <ScopeRow
      scope={scope}
      status={status}
      required={required}
      title={requiredBy ? `Required by: ${requiredBy.join(", ")}` : undefined}
      className={className}
      {...props}
    />
  )
}
