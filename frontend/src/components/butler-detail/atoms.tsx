// ---------------------------------------------------------------------------
// atoms.tsx — shared primitive atoms for butler detail resident tabs
// (bu-iuol4.13)
//
// Exports:
//   <MonoLabel>  — uppercase mono 9px eyebrow label with letter-spacing
//   <Panel>      — panel with mono eyebrow header, grid-span support
//   <KpiCell>    — KPI card with tonal value display
//   <KV>         — key-value row for Config and similar tabs
//   <ErrorLine>  — icon + destructive-tone error message row
//
// Doctrine (non-negotiable):
//   - No raw oklch in JSX. No hex. No inline style except typed-primitive
//     exemption (§2.b in about/heart-and-soul/design-language.md).
//   - Tailwind tokens only: text-amber-500, text-destructive, etc.
//   - All numeric values use the .tnum utility (font-variant-numeric).
// ---------------------------------------------------------------------------

import React from "react"
import { AlertTriangle } from "lucide-react"
import { cn } from "@/lib/utils"

// ---------------------------------------------------------------------------
// Tone → className helper
//
// Token name → Tailwind text utility. This is the ONLY place the mapping
// lives; do not inline these strings at call sites.
// ---------------------------------------------------------------------------

export type Tone = "amber" | "red" | "green" | "dim" | "fg"

export function toneClass(tone: Tone): string {
  switch (tone) {
    case "amber": return "text-amber-500"
    case "red":   return "text-destructive"
    case "green": return "text-emerald-500"
    case "dim":   return "text-muted-foreground"
    case "fg":    return "text-foreground"
  }
}

// ---------------------------------------------------------------------------
// MonoLabel
//
// Uppercase mono 9px eyebrow label with letter-spacing and tabular-nums.
// Used to title sections, eyebrow headings, and KPI labels.
//
// Props:
//   children — label text
//   color    — optional tone token (defaults to "dim" = text-muted-foreground)
//   className — additional classes
// ---------------------------------------------------------------------------

export interface MonoLabelProps {
  children: React.ReactNode
  color?: Tone
  className?: string
}

export function MonoLabel({ children, color = "dim", className }: MonoLabelProps) {
  return (
    <span
      className={cn(
        "font-mono text-[9px] uppercase tracking-wider tnum",
        toneClass(color),
        className,
      )}
    >
      {children}
    </span>
  )
}

// ---------------------------------------------------------------------------
// Panel
//
// A panel with an optional mono eyebrow header. Applies border-right +
// border-bottom only (the page wrapper owns the outer frame). Supports
// 4-col grid context via the `span` prop.
//
// Props:
//   title   — eyebrow label text (optional)
//   sub     — secondary eyebrow text shown after title (optional)
//   span    — grid column span 1–4 (default: 1)
//   scroll  — if true, overflows the body with overflow-y-auto
//   height  — explicit CSS height string for the panel body (typed-primitive
//             exemption: this is a dynamic, unavoidable inline style)
//   accent  — if true, renders a left-edge accent stripe (uses primary color)
//   children — body content
//   className — additional wrapper classes
// ---------------------------------------------------------------------------

export interface PanelProps {
  title?: string
  sub?: string
  span?: 1 | 2 | 3 | 4
  scroll?: boolean
  height?: string
  accent?: boolean
  /** Forwarded to the outer wrapper div as data-testid. */
  testId?: string
  children?: React.ReactNode
  className?: string
}

const spanClass: Record<number, string> = {
  1: "col-span-1",
  2: "col-span-2",
  3: "col-span-3",
  4: "col-span-4",
}

export function Panel({
  title,
  sub,
  span = 1,
  scroll = false,
  height,
  accent = false,
  testId,
  children,
  className,
}: PanelProps) {
  return (
    <div
      className={cn(
        "relative flex flex-col border-r border-b border-border/60",
        spanClass[span],
        className,
      )}
      data-testid={testId}
    >
      {/* Left-edge accent stripe — only when accent=true */}
      {accent ? (
        <div
          className="absolute left-0 top-0 w-0.5 h-full bg-primary"
          aria-hidden="true"
        />
      ) : null}

      {/* Eyebrow header — only when title is provided */}
      {title ? (
        <div className="flex items-baseline gap-2 px-4 pt-3 pb-2 border-b border-border/40">
          <MonoLabel color="dim">{title}</MonoLabel>
          {sub ? <MonoLabel color="dim" className="opacity-60">{sub}</MonoLabel> : null}
        </div>
      ) : null}

      {/* Body */}
      <div
        className={cn("flex-1 p-4", scroll && "overflow-y-auto")}
        style={height ? { height } : undefined}
      >
        {children}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// KpiCell
//
// KPI card with a MonoLabel eyebrow, a numeric value, and an optional
// sub-label. The `tone` prop maps to a token name, not an oklch value.
//
// Props:
//   label — eyebrow MonoLabel text
//   value — the displayed value (string or ReactNode)
//   sub   — optional sub-label below the value (in muted color)
//   tone  — semantic token name for the value color (default: "fg")
//   big   — if true, renders the value at 28px; default is 22px
//   className — additional wrapper classes
// ---------------------------------------------------------------------------

export interface KpiCellProps {
  label: string
  value: React.ReactNode
  sub?: React.ReactNode
  tone?: Tone
  big?: boolean
  className?: string
}

export function KpiCell({
  label,
  value,
  sub,
  tone = "fg",
  big = false,
  className,
}: KpiCellProps) {
  return (
    <div className={cn("flex flex-col gap-0.5", className)}>
      <MonoLabel color="dim">{label}</MonoLabel>
      <span
        className={cn(
          "font-mono tnum font-medium leading-none",
          big ? "text-[28px]" : "text-[22px]",
          toneClass(tone),
        )}
      >
        {value}
      </span>
      {sub ? (
        <span className="font-mono text-xs tnum text-muted-foreground leading-tight">
          {sub}
        </span>
      ) : null}
    </div>
  )
}

// ---------------------------------------------------------------------------
// KV
//
// Key-value row used in Config tab and similar detail panels. Displays a
// key and a value side by side. The `mono` prop switches the value to
// font-mono (for paths, IDs, tokens, etc.).
//
// Props:
//   k       — key label
//   v       — value (string or ReactNode)
//   mono    — if true, value renders in font-mono
//   className — additional row classes
// ---------------------------------------------------------------------------

export interface KVProps {
  k: string
  v: React.ReactNode
  mono?: boolean
  className?: string
}

export function KV({ k, v, mono = false, className }: KVProps) {
  return (
    <div className={cn("flex items-baseline gap-4 py-1.5 border-b border-border/40 last:border-b-0", className)}>
      <span className="shrink-0 text-xs text-muted-foreground font-medium w-32 truncate">
        {k}
      </span>
      <span
        className={cn(
          "flex-1 text-xs text-foreground min-w-0 break-all",
          mono && "font-mono tnum",
        )}
      >
        {v}
      </span>
    </div>
  )
}

// ---------------------------------------------------------------------------
// ErrorLine
//
// Error state indicator: AlertTriangle icon + destructive-tone message text.
// Used inside panels to surface data-fetch failures without full-page errors.
//
// Props:
//   children  — error message text
//   className — additional wrapper classes
// ---------------------------------------------------------------------------

export interface ErrorLineProps {
  children: React.ReactNode
  className?: string
}

export function ErrorLine({ children, className }: ErrorLineProps) {
  return (
    <p
      className={cn(
        "flex items-center gap-1.5 text-sm min-w-0",
        toneClass("red"),
        className,
      )}
      data-testid="error-state-line"
    >
      <AlertTriangle className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
      <span className="truncate">{children}</span>
    </p>
  )
}
