// ---------------------------------------------------------------------------
// atoms-utils.ts — non-component utilities for butler detail atoms
// (bu-zsnuh, bu-f4no2, bu-hdavr.3)
//
// Exports:
//   Tone                 — semantic tone token type
//   toneClass            — maps a Tone token to a Tailwind text utility class
//   MonoLabelProps       — props for <MonoLabel>
//   PanelProps           — props for <Panel>
//   KpiCellProps         — props for <KpiCell>
//   KVProps              — props for <KV>
//   ErrorLineProps       — props for <ErrorLine>
//   LoadingLineProps     — props for <LoadingLine>
//   EmptyLineProps       — props for <EmptyLine>
//   ButlerPanelGridProps — props for <ButlerPanelGrid>
//
// Split from atoms.tsx to satisfy react-refresh/only-export-components:
// fast-refresh requires component-only files; utilities and types live here.
// ---------------------------------------------------------------------------

import type React from "react"
import type { HTMLAttributes } from "react"

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
// Component prop interfaces — declared here so atoms.tsx stays component-only
// ---------------------------------------------------------------------------

export interface MonoLabelProps {
  children: React.ReactNode
  color?: Tone
  className?: string
}

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

export interface KpiCellProps {
  label: string
  value: React.ReactNode
  sub?: React.ReactNode
  tone?: Tone
  big?: boolean
  className?: string
}

export interface KVProps {
  k: string
  v: React.ReactNode
  mono?: boolean
  className?: string
}

export interface ErrorLineProps {
  children: React.ReactNode
  className?: string
}

export interface LoadingLineProps {
  className?: string
}

export interface EmptyLineProps {
  children: React.ReactNode
  className?: string
}

export interface ButlerPanelGridProps extends HTMLAttributes<HTMLDivElement> {
  children: React.ReactNode
}
