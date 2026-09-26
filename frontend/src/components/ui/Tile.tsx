import * as React from "react"

import { Eyebrow } from "@/components/ui/Eyebrow"
import { cn } from "@/lib/utils"

/**
 * A dense-grid module with its own loading and degradation boundary.
 * Ordinary dashboard composition belongs in Section.
 */
export interface TileProps extends React.HTMLAttributes<HTMLElement> {
  loading?: boolean
  degraded?: boolean
  children?: React.ReactNode
}

export function Tile({
  loading = false,
  degraded = false,
  className,
  ...props
}: TileProps) {
  return (
    <section
      data-slot="tile"
      aria-busy={loading || undefined}
      data-degraded={degraded || undefined}
      className={cn("min-w-0 space-y-4", className)}
      {...props}
    />
  )
}

export function TileHeader({
  className,
  ...props
}: React.ComponentProps<"header">) {
  return (
    <header
      data-slot="tile-header"
      className={cn("space-y-2", className)}
      {...props}
    />
  )
}

export function TileTitle({
  className,
  children,
  ...props
}: Omit<React.ComponentProps<"h2">, "children"> & {
  children: React.ReactNode
}) {
  return (
    <Eyebrow
      as="h2"
      data-slot="tile-title"
      className={className}
      {...props}
    >
      {children}
    </Eyebrow>
  )
}

export function TileDescription({
  className,
  ...props
}: React.ComponentProps<"p">) {
  return (
    <p
      data-slot="tile-description"
      className={cn("text-sm text-muted-foreground", className)}
      {...props}
    />
  )
}

export function TileAction({
  className,
  ...props
}: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="tile-action"
      className={cn("shrink-0", className)}
      {...props}
    />
  )
}

export function TileContent({
  className,
  ...props
}: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="tile-content"
      className={cn("min-w-0", className)}
      {...props}
    />
  )
}

export function TileFooter({
  className,
  ...props
}: React.ComponentProps<"footer">) {
  return (
    <footer
      data-slot="tile-footer"
      className={cn("flex items-center gap-2", className)}
      {...props}
    />
  )
}
