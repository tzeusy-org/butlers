import * as React from "react"

import { cn } from "@/lib/utils"

type InlineActionButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  as?: "button"
}

type InlineActionAnchorProps = Omit<React.AnchorHTMLAttributes<HTMLAnchorElement>, "href"> & {
  as: "a"
  href: string
}

type InlineActionSummaryProps = React.HTMLAttributes<HTMLElement> & {
  as: "summary"
}

export type InlineActionLinkProps =
  | InlineActionButtonProps
  | InlineActionAnchorProps
  | InlineActionSummaryProps

const baseClassName = [
  "inline-flex min-h-11 min-w-11 items-center justify-center",
  "font-mono text-[11px] uppercase tracking-wider",
  "text-muted-foreground underline underline-offset-4 decoration-[var(--border-strong)]",
  "transition-colors hover:text-foreground cursor-pointer",
  "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-fg",
  "disabled:cursor-default disabled:opacity-50",
].join(" ")

function withoutAs<T extends { as?: unknown }>(props: T): Omit<T, "as"> {
  const elementProps = { ...props }
  Reflect.deleteProperty(elementProps, "as")
  return elementProps as Omit<T, "as">
}

/**
 * Canonical compact text action. It keeps the established mono-uppercase
 * affordance while giving every consumer a real focus treatment, a 44px
 * target, and native disabled semantics for buttons.
 */
export function InlineActionLink(props: InlineActionLinkProps) {
  if (props.as === "a") {
    return (
      <a
        {...withoutAs(props)}
        data-slot="inline-action-link"
        className={cn(baseClassName, props.className)}
      >
        {props.children}
      </a>
    )
  }

  if (props.as === "summary") {
    return (
      <summary
        {...withoutAs(props)}
        data-slot="inline-action-link"
        className={cn(baseClassName, "list-none", props.className)}
      >
        {props.children}
      </summary>
    )
  }

  const { type, ...buttonProps } = withoutAs(props)

  return (
    <button
      {...buttonProps}
      data-slot="inline-action-link"
      type={type ?? "button"}
      className={cn(baseClassName, props.className)}
    >
      {props.children}
    </button>
  )
}
