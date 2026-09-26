import * as React from "react"

import { Eyebrow } from "@/components/ui/Eyebrow"
import { cn } from "@/lib/utils"

/**
 * The canonical Dispatch surface. Sections use semantic markup and rule/rhythm
 * instead of the old elevated Card shell.
 */
export interface SectionProps extends React.HTMLAttributes<HTMLElement> {
  /** Optional eyebrow rendered as the section's accessible heading. */
  eyebrow?: React.ReactNode
  /** Quiet sections keep their structure but can render one calm sentence. */
  quiet?: boolean
  /** Sentence used for a quiet section with no active content. */
  empty?: React.ReactNode
  children?: React.ReactNode
}

export function Section({
  eyebrow,
  quiet = false,
  empty,
  className,
  children,
  ...props
}: SectionProps) {
  return (
    <section
      data-slot="section"
      className={cn("space-y-4", className)}
      {...props}
    >
      {eyebrow ? <Eyebrow as="h2">{eyebrow}</Eyebrow> : null}
      {quiet && empty ? (
        <p className="font-serif text-base italic leading-[1.5] text-muted-foreground">
          {empty}
        </p>
      ) : (
        children
      )}
    </section>
  )
}

export function SectionHeader({
  className,
  ...props
}: React.ComponentProps<"header">) {
  return (
    <header
      data-slot="section-header"
      className={cn("space-y-2", className)}
      {...props}
    />
  )
}

export function SectionTitle({
  className,
  children,
  ...props
}: Omit<React.ComponentProps<"h2">, "children"> & {
  children: React.ReactNode
}) {
  return (
    <Eyebrow
      as="h2"
      data-slot="section-title"
      className={className}
      {...props}
    >
      {children}
    </Eyebrow>
  )
}

export function SectionDescription({
  className,
  ...props
}: React.ComponentProps<"p">) {
  return (
    <p
      data-slot="section-description"
      className={cn("text-sm text-muted-foreground", className)}
      {...props}
    />
  )
}

export function SectionAction({
  className,
  ...props
}: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="section-action"
      className={cn("shrink-0", className)}
      {...props}
    />
  )
}

export function SectionContent({
  className,
  ...props
}: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="section-content"
      className={cn("min-w-0", className)}
      {...props}
    />
  )
}

export function SectionFooter({
  className,
  ...props
}: React.ComponentProps<"footer">) {
  return (
    <footer
      data-slot="section-footer"
      className={cn("flex items-center gap-2", className)}
      {...props}
    />
  )
}
