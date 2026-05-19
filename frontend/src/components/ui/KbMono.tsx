// ---------------------------------------------------------------------------
// KbMono — keyboard shortcut capsule primitive (bu-ec2wb)
//
// Renders a keyboard key label: mono font, small padding, hairline border.
// Reuses existing tokens from --border-strong and font-mono.
//
// Brief §2: "Keyboard shortcut capsule; mono, small padding, hairline border."
// Amendment 9: No new design tokens. Uses existing border and mono tokens.
// ---------------------------------------------------------------------------

import * as React from "react"

import { cn } from "@/lib/utils"

export interface KbMonoProps extends React.HTMLAttributes<HTMLElement> {
  /** The key label to render (e.g. "⌘", "K", "Ctrl"). */
  children: React.ReactNode
}

/**
 * Keyboard key capsule. Mono font, small horizontal padding, hairline border.
 *
 * @example
 *   <KbMono>⌘</KbMono>
 *   <KbMono>K</KbMono>
 */
export function KbMono({ children, className, ...props }: KbMonoProps) {
  return (
    <kbd
      className={cn(
        "inline-flex items-center justify-center",
        "h-5 min-w-5 px-1.5",
        "rounded",
        "border font-mono text-[10px] font-medium leading-none",
        "border-[var(--border-strong,oklch(1_0_0/0.18))]",
        "text-[var(--mfg,oklch(0.708_0_0))]",
        "bg-transparent",
        "select-none whitespace-nowrap",
        className,
      )}
      {...props}
    >
      {children}
    </kbd>
  )
}
