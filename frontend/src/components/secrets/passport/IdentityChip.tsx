// ---------------------------------------------------------------------------
// IdentityChip — name + role + colour-coded dot (bu-qo3sf)
//
// Used in the spine header and identity switcher to represent an identity
// (owner or household member entity).
//
// butler-secrets §Projection-Lens Identity Switcher: "The identity switcher
// in the page header SHALL be a projection lens over the owner's view of
// household-member credential data."
//
// Layout: [coloured dot 6px] [name in mono] [role eyebrow]
//
// Role dot colours match the role-semantic tokens already defined in index.css:
//   owner   → --role-admin (amber-ish warm, elevated-access signal)
//   member  → --category-1 (first categorical hue — relationship butler)
//   unknown → --muted-foreground
//
// The dot here is an intentional exception to the "one affordance per signal"
// rule: the dot is the identity marker, not a state indicator. No state colour
// (--red/--amber/--green) is used.
// ---------------------------------------------------------------------------

import * as React from "react"

import { cn } from "@/lib/utils"

/** Identity roles the chip can display. */
export type IdentityRole = "owner" | "member" | "unknown"

export interface IdentityChipProps extends React.HTMLAttributes<HTMLSpanElement> {
  /** Display name for the identity. */
  name: string
  /** Role drives the dot colour. */
  role: IdentityRole
  /**
   * When true, renders in an active/selected state with full-foreground text.
   * Default: false.
   */
  selected?: boolean
}

const ROLE_DOT_COLORS: Record<IdentityRole, string> = {
  owner:   "var(--role-admin,oklch(0.572_0.178_67.6))",
  member:  "var(--category-1)",
  unknown: "var(--muted-foreground)",
}

const ROLE_LABELS: Record<IdentityRole, string> = {
  owner:   "owner",
  member:  "member",
  unknown: "unknown",
}

/**
 * Compact identity chip: dot + name + role label.
 *
 * @example
 *   <IdentityChip name="Tze" role="owner" selected />
 *   <IdentityChip name="Alex" role="member" />
 */
export function IdentityChip({
  name,
  role,
  selected = false,
  className,
  ...props
}: IdentityChipProps) {
  const dotColor = ROLE_DOT_COLORS[role]
  const roleLabel = ROLE_LABELS[role]

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5",
        "font-mono text-[11px] font-normal leading-none tabular-nums",
        selected
          ? "text-[var(--fg,oklch(0.985_0_0))]"
          : "text-[var(--mfg,oklch(0.708_0_0))]",
        className,
      )}
      {...props}
    >
      {/* Identity dot — role-colour, not state-colour */}
      <span
        aria-hidden="true"
        className="inline-block shrink-0 rounded-full"
        style={{ width: 6, height: 6, backgroundColor: dotColor }}
      />
      <span>{name}</span>
      <span
        className="uppercase tracking-[0.10em] text-[9px]"
        style={{ color: "var(--dim,oklch(0.55_0_0))" }}
      >
        {roleLabel}
      </span>
    </span>
  )
}
